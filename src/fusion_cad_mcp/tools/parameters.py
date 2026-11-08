"""Group 2 / parameter tools.

Three tools that bracket every Sketch and Feature workflow:
- add_parameters: idempotent bulk add (won't replace existing)
- update_parameter: change the expression on an existing param
- list_parameters: enumerate with resolved values

All three are pure scripts wrapped by the adapter. Math-name guard per
patterns.md: reserved names like sin/cos/pi/e/sqrt MUST NOT be used as param
names; the script rejects those at generation time.

Parameters added by these tools bind correctly to both feature inputs
(via ValueInput.createByString) and sketch dimensions (via
dim.parameter.expression). Parametric sketches work; the lock-badge display
lag documented in gotchas.md G9 is cosmetic only.
"""

from __future__ import annotations

import json
from typing import Any

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_stdout_json, script_parse_failure
from ..mutation import MUTATION_SCRIPT, PARAMETER_VALUE_SCRIPT

# Names Fusion treats as math identifiers in expressions. Using these as user-param
# names creates expressions that look correct but evaluate to the math function
# instead of the parameter value. The skill caught this the hard way; we block at
# generation time so it never reaches Fusion.
RESERVED_MATH_NAMES = frozenset(
    [
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sinh",
        "cosh",
        "tanh",
        "abs",
        "sqrt",
        "exp",
        "log",
        "ln",
        "log10",
        "min",
        "max",
        "round",
        "floor",
        "ceil",
        "pi",
        "e",
    ]
)


def _validate_param_defs(defs: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Validate the shape of the defs list. Returns (ok, error_message)."""
    if not isinstance(defs, list):
        return False, "defs must be a list"
    if not defs:
        return False, "defs must be non-empty"
    seen = set()
    for i, d in enumerate(defs):
        if not isinstance(d, dict):
            return False, f"defs[{i}] must be an object"
        name = d.get("name")
        expr = d.get("expression")
        if not isinstance(name, str) or not name.strip():
            return False, f"defs[{i}].name must be a non-empty string"
        if not isinstance(expr, str) or not expr.strip():
            return False, f"defs[{i}].expression must be a non-empty string"
        for field in ("units", "comment"):
            if d.get(field) is not None and not isinstance(d[field], str):
                return False, f"defs[{i}].{field} must be a string or null"
        if name in RESERVED_MATH_NAMES:
            return False, (
                f"defs[{i}].name {name!r} is a reserved math identifier; "
                "Fusion will interpret expressions referencing it as the math function"
            )
        if not (name[0].isalpha() or name[0] == "_"):
            return False, f"defs[{i}].name {name!r} must start with a letter or underscore"
        if any(not (c.isalnum() or c in '_"$°µ') for c in name):
            return False, f"defs[{i}].name {name!r} contains unsupported characters"
        if name in seen:
            return False, f"defs[{i}].name {name!r} is duplicated"
        seen.add(name)
    return True, None


_PARAMETER_SCRIPT = MUTATION_SCRIPT + """
def _parameter_expectation(design, expression, units):
    # The numeric evaluator does not parse Fusion text parameter expressions.
    # UserParameter.add and its expression setter validate the text grammar.
    if units.casefold() == 'text':
        return expression.strip(), None
    manager = getattr(design, 'fusionUnitsManager', None) or design.unitsManager
    # An empty evaluator unit can mean the document's default length. The
    # parameter API itself validates unitless expressions if the evaluator
    # cannot parse the explicit unitless token on this Fusion build.
    evaluation_units = units or 'ul'
    if not units and not manager.isValidExpression('1', evaluation_units):
        return expression.strip(), None
    if not manager.isValidExpression(expression, evaluation_units):
        raise _MutationError('invalid_expression', 'Fusion could not evaluate the expression in the parameter units',
                             units=units)
    standardized = manager.standardizeExpression(expression, evaluation_units)
    value = manager.evaluateExpression(expression, evaluation_units)
    if not standardized or not math.isfinite(value):
        raise _MutationError('invalid_expression', 'Fusion did not return an evaluable expression', units=units)
    return standardized, value


def _parameter_readback(design, parameter, units, expected):
    if not parameter.isValid:
        raise _MutationError('parameter_readback_failed', 'Parameter is no longer valid')
    value = _mutation_parameter_value(parameter)
    actual_text = isinstance(value, str)
    if actual_text != (units.casefold() == 'text'):
        raise _MutationError('parameter_readback_failed', 'Fusion returned an unexpected parameter type')
    expression = parameter.expression
    canonical, expected_value = expected
    manager = getattr(design, 'fusionUnitsManager', None) or design.unitsManager
    if expected_value is None:
        matched = expression.strip() == canonical and (actual_text or not parameter.unit)
    else:
        actual_units = parameter.unit
        compatible = actual_units == units or (actual_units and units and
                     manager.isValidExpression('1 ' + actual_units, units))
        matched = (compatible and manager.standardizeExpression(expression, units or 'ul') == canonical
                   and math.isclose(value, expected_value, rel_tol=1e-9, abs_tol=1e-8))
    if not matched:
        raise _MutationError('parameter_readback_failed', 'Fusion did not preserve the requested expression and value')
    return {'expression': expression, 'units': parameter.unit, 'value': value,
            'value_type': 'text' if actual_text else 'numeric'}
"""


def build_add(defs: list[dict[str, Any]]) -> str:
    """Generate the Python script for an idempotent add_parameters operation.

    Each def is {name, expression, units?, comment?}. Existing params are skipped
    (not overwritten); use update_parameter for in-place edits.
    """
    safe_defs = [{key: d.get(key) for key in ("name", "expression", "units", "comment")} for d in defs]
    return f"""
import adsk.core
import adsk.fusion
import json

{_PARAMETER_SCRIPT}
PARAM_DEFS = {safe_defs!r}


def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}}))
        return
    params = design.userParameters

    pending = []
    skipped = []
    for d in PARAM_DEFS:
        existing = params.itemByName(d["name"])
        if existing is not None:
            skipped.append({{"name": d["name"], "current_expression": existing.expression}})
        else:
            pending.append(d)
    if not pending:
        print(json.dumps({{"ok": True, "added": [], "skipped": skipped, "total_params": params.count,
                           "verification": {{"changed": False, "recomputed": False}}}}))
        return

    tx = None
    name = None
    created = []
    try:
        for d in pending:
            name = d["name"]
            expression = d["expression"]
            units = d["units"] or ""
            expected = _parameter_expectation(design, expression, units)
            if tx is None:
                tx = _Mutation(design, None)
            try:
                param = params.add(name, adsk.core.ValueInput.createByString(expression),
                                   units, d["comment"] or "")
            except Exception:
                # The name was absent before this batch. A failed call can
                # still have created it, so check the collection before cleanup.
                partial = params.itemByName(name)
                if partial is not None:
                    tx.track(partial)
                raise
            if param is None or param.name != name:
                partial = params.itemByName(name)
                if partial is not None:
                    tx.track(partial)
                raise _MutationError('parameter_creation_failed', 'Fusion did not return the requested parameter')
            tx.track(param)
            created.append((param, name, units, expected))
        verification = tx.verify()
        added = []
        for param, name, units, expected in created:
            if params.itemByName(name) != param or not param.expression:
                raise _MutationError('parameter_readback_failed', 'Created parameter was not read back')
            added.append({{"name": name, **_parameter_readback(design, param, units, expected)}})
        print(json.dumps({{"ok": True, "added": added, "skipped": skipped, "total_params": params.count,
                           "verification": {{"changed": True, **verification}}}}))
    except Exception as exc:
        print(json.dumps(_mutation_failure(tx, exc, 'parameter_add_failed', name=name, skipped=skipped)))
""".strip()


def build_update(name: str, expression: str) -> str:
    """Generate the script for updating one parameter's expression."""
    return f"""
import adsk.core
import adsk.fusion
import json

{_PARAMETER_SCRIPT}
NAME = {json.dumps(name)}
EXPRESSION = {json.dumps(expression)}


def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}}))
        return
    param = design.userParameters.itemByName(NAME)
    if param is None:
        print(json.dumps({{"ok": False, "error": "param_not_found", "name": NAME}}))
        return
    tx = None
    try:
        before = param.expression
        if before == EXPRESSION:
            value = _mutation_parameter_value(param)
            print(json.dumps({{"ok": True, "name": NAME, "before": before, "after": before,
                               "value": value, "units": param.unit,
                               "value_type": 'text' if isinstance(value, str) else 'numeric',
                               "verification": {{"changed": False, "recomputed": False}}}}))
            return
        units = 'Text' if getattr(param, 'valueType', 0) == 1 else param.unit
        expected = _parameter_expectation(design, EXPRESSION, units)
        tx = _Mutation(design, None)
        tx.record_expression(param)
        param.expression = EXPRESSION
        verification = tx.verify()
        readback = _parameter_readback(design, param, units, expected)
        after = readback.pop('expression')
        print(json.dumps({{"ok": True, "name": NAME, "before": before, "after": after,
                           **readback, "verification": {{"changed": after != before, **verification}}}}))
    except Exception as exc:
        print(json.dumps(_mutation_failure(tx, exc, 'parameter_update_failed', name=NAME)))
""".strip()


def build_list() -> str:
    """Generate the script for listing all user parameters with resolved values."""
    return f"""
import adsk.core
import adsk.fusion
import json

{PARAMETER_VALUE_SCRIPT}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}}))
        return
    out = []
    for i in range(design.userParameters.count):
        p = design.userParameters.item(i)
        out.append({{
            "name": p.name,
            "expression": p.expression,
            "value": _mutation_parameter_value(p),
            "units": p.unit,
            "comment": p.comment,
        }})
    print(json.dumps({{"ok": True, "parameters": out, "count": len(out)}}))
""".strip()


# ---------- run wrappers ----------


def add_parameters(adapter: FusionAdapter, defs: list[dict[str, Any]]) -> Envelope:
    ok, err = _validate_param_defs(defs)
    if not ok:
        return Envelope(ok=False, error="invalid_defs", message=err or "")
    env = adapter.execute_script(build_add(defs))
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is None:
        return script_parse_failure(env, "parse_failed")
    if parsed.get("ok") is False:
        return Envelope(ok=False, error=parsed.get("error", "fusion_error"), message=env.message, result=parsed)
    return Envelope(ok=True, message=env.message, result=parsed)


def update_parameter(adapter: FusionAdapter, name: str, expression: str) -> Envelope:
    if not isinstance(name, str) or not name.strip():
        return Envelope(ok=False, error="invalid_name", message="name must be a non-empty string")
    if not isinstance(expression, str) or not expression.strip():
        return Envelope(
            ok=False, error="invalid_expression", message="expression must be a non-empty string"
        )
    env = adapter.execute_script(build_update(name, expression))
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is None:
        return script_parse_failure(env, "parse_failed")
    if parsed.get("ok") is False:
        return Envelope(
            ok=False, error=parsed.get("error", "fusion_error"), message=env.message, result=parsed
        )
    return Envelope(ok=True, message=env.message, result=parsed)


def list_parameters(adapter: FusionAdapter) -> Envelope:
    env = adapter.execute_script(build_list(), read_only=True)
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is None:
        return script_parse_failure(env, "parse_failed", read_only=True)
    if parsed.get("ok") is False:
        return Envelope(ok=False, error=parsed.get("error", "fusion_error"), message=env.message)
    return Envelope(ok=True, message=env.message, result=parsed)


__all__ = [
    "RESERVED_MATH_NAMES",
    "add_parameters",
    "build_add",
    "build_list",
    "build_update",
    "list_parameters",
    "update_parameter",
]
