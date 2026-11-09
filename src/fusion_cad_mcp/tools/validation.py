"""Read-only, bounded geometric design checks in root assembly coordinates.

Interference uses Fusion transient results and never creates model bodies. Bounding
checks compare axis-aligned boxes; they do not establish containment in a cavity.
"""

from __future__ import annotations

import math
import textwrap
from typing import Any

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from ..handles import emit_resolve, is_handle, parse_handle
from ..resolver import RESOLVER_SCRIPT


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _number(value: Any, name: str) -> float:
    if not _finite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def _vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or any(
        not _finite(x) for x in value
    ):
        raise ValueError(f"{name} must contain three finite coordinates")
    return [float(x) for x in value]


def _prepare(checks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(checks, list) or not 1 <= len(checks) <= 50:
        raise ValueError("checks must contain between 1 and 50 checks")
    prepared = []
    handles: dict[str, str] = {}
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"checks[{index}] must be an object")
        kind = check.get("type")
        if kind == "clearance":
            allowed, required = {"type", "body_a", "body_b", "min_mm"}, {"body_a", "body_b", "min_mm"}
            numeric = {"min_mm": _number(check.get("min_mm"), "min_mm")}
        elif kind == "interference":
            allowed, required = {"type", "body_a", "body_b", "max_overlap_mm3"}, {"body_a", "body_b"}
            numeric = {"max_overlap_mm3": _number(check.get("max_overlap_mm3", 0), "max_overlap_mm3")}
        elif kind == "bounds":
            allowed, required = {"type", "body", "min_mm", "max_mm", "tolerance_mm"}, {"body", "min_mm", "max_mm"}
            low, high = _vector(check.get("min_mm"), "min_mm"), _vector(check.get("max_mm"), "max_mm")
            if any(a > b for a, b in zip(low, high, strict=True)):
                raise ValueError("min_mm coordinates must not exceed max_mm")
            numeric = {"min_mm": low, "max_mm": high, "tolerance_mm": _number(check.get("tolerance_mm", 0), "tolerance_mm")}
        else:
            raise ValueError(f"checks[{index}].type is unsupported")
        if set(check) - allowed or not required.issubset(check):
            raise ValueError(f"checks[{index}] has missing or unsupported fields")
        selectors = ("body",) if kind == "bounds" else ("body_a", "body_b")
        item = {"type": kind, **numeric}
        for field in selectors:
            value = check[field]
            if not isinstance(value, str) or not value:
                raise ValueError(f"checks[{index}].{field} must be a nonempty selector")
            if is_handle(value):
                if parse_handle(value)["kind"] != "body":
                    raise ValueError(f"checks[{index}].{field} must be a body handle")
                handles.setdefault(value, f"_body_handle_{len(handles)}")
            item[field] = value
        prepared.append(item)
    return prepared, handles


def build_validate_design(checks: list[dict[str, Any]]) -> str:
    prepared, handles = _prepare(checks)
    functions = []
    for handle, name in handles.items():
        code = "def print(value): errors.append(json.loads(value))\n" + emit_resolve(handle, var="_entity")
        functions.append(f"def {name}(design, errors):\n" + textwrap.indent(code + "return _entity\n", "    "))
    mapping = "{" + ", ".join(f"{h!r}: {name}" for h, name in handles.items()) + "}"
    return (
        "import adsk.core, adsk.fusion, json, math\n"
        + RESOLVER_SCRIPT + "\n" + "\n".join(functions)
        + "\n_HANDLES = " + mapping + "\n_CHECKS = " + repr(prepared) + "\n" + _SCRIPT
    )


_SCRIPT = '''
def _point(p):
    if p is None: raise ValueError('closest point unavailable')
    result = [p.x * 10, p.y * 10, p.z * 10]
    if not all(math.isfinite(x) for x in result): raise ValueError('non-finite point')
    return result

def _body(design, root, selector):
    if selector not in _HANDLES:
        return _select_body(root, selector)
    errors = []
    entity = _HANDLES[selector](design, errors)
    if entity is None:
        return None, errors[0] if errors else {'error': 'handle_invalid'}
    return entity, None

def _interference(design, a, b):
    if not getattr(a, 'isSolid', False) or not getattr(b, 'isSolid', False):
        raise ValueError('interference requires two solid BRep bodies')
    entities = adsk.core.ObjectCollection.create()
    entities.add(a); entities.add(b)
    inp = design.createInterferenceInput(entities)
    if inp is None: raise ValueError('Fusion could not create interference input')
    inp.areCoincidentFacesIncluded = False
    results = design.analyzeInterference(inp)
    if results is None: raise ValueError('Fusion returned no interference result')
    volumes = []
    for i in range(results.count):
        body = results.item(i).interferenceBody
        if body is None: volumes.append(None); continue
        volume = body.volume * 1000
        if not math.isfinite(volume) or volume < 0: raise ValueError('invalid intersection volume')
        volumes.append(volume)
    return results.count, volumes

def _check(design, root, measure, spec):
    kind = spec['type']
    names = ['body'] if kind == 'bounds' else ['body_a', 'body_b']
    bodies = []
    for field in names:
        body, error = _body(design, root, spec[field])
        if error: return {'type': kind, 'status': 'unavailable', 'reason': error, 'check': spec}
        if not getattr(body, 'isValid', True):
            return {'type': kind, 'status': 'unavailable', 'reason': 'body_invalid', 'check': spec}
        bodies.append(body)
    if len(bodies) == 2 and bodies[0] == bodies[1]:
        return {'type': kind, 'status': 'unavailable', 'reason': 'same_body', 'check': spec}
    if kind == 'bounds':
        box = bodies[0].boundingBox
        low, high = _point(box.minPoint), _point(box.maxPoint)
        tolerance = spec['tolerance_mm']
        inside = all(x >= y - tolerance for x, y in zip(low, spec['min_mm'])) and all(x <= y + tolerance for x, y in zip(high, spec['max_mm']))
        return {'type': kind, 'status': 'pass' if inside else 'violation', 'check': spec,
                'evidence': {'body_bounds_min_mm': low, 'body_bounds_max_mm': high, 'frame': 'root',
                             'method': 'axis-aligned bounding box; not cavity containment'}}
    count, volumes = _interference(design, bodies[0], bodies[1])
    if kind == 'interference':
        if any(v is None for v in volumes) and spec['max_overlap_mm3'] > 0:
            return {'type': kind, 'status': 'unavailable', 'reason': 'overlap_volume_unavailable', 'check': spec}
        total = sum(v for v in volumes if v is not None)
        passes = (count == 0) if spec['max_overlap_mm3'] == 0 else total <= spec['max_overlap_mm3']
        return {'type': kind, 'status': 'pass' if passes else 'violation', 'check': spec,
                'evidence': {'interference_count': count, 'overlap_mm3': total if all(v is not None for v in volumes) else None}}
    evidence = {'interference_count': count, 'frame': 'root',
                'overlap_mm3': sum(volumes) if all(v is not None for v in volumes) else None}
    try:
        distance = measure.measureMinimumDistance(bodies[0], bodies[1])
        if distance is None or not math.isfinite(distance.value) or distance.value < 0:
            raise ValueError('Fusion could not measure minimum distance')
        distance_mm = distance.value * 10
        evidence.update({'minimum_distance_mm': distance_mm, 'closest_point_a_mm': _point(distance.positionOne),
                         'closest_point_b_mm': _point(distance.positionTwo)})
    except Exception as exc:
        if count == 0: raise
        evidence.update({'minimum_distance_mm': None, 'closest_point_a_mm': None,
                         'closest_point_b_mm': None, 'measurement_unavailable': str(exc)})
        return {'type': kind, 'status': 'violation', 'check': spec, 'evidence': evidence}
    passes = count == 0 and distance_mm >= spec['min_mm']
    return {'type': kind, 'status': 'pass' if passes else 'violation', 'check': spec, 'evidence': evidence}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({'ok': False, 'error': 'no_active_design'})); return
    root = design.rootComponent
    results = []
    for spec in _CHECKS:
        try: results.append(_check(design, root, app.measureManager, spec))
        except Exception as exc: results.append({'type': spec['type'], 'status': 'unavailable', 'reason': str(exc), 'check': spec})
    print(json.dumps({'ok': True, 'passed': all(r['status'] == 'pass' for r in results),
                      'checks': results, 'units': {'distance': 'mm', 'volume': 'mm^3'}}))
'''


def validate_design(adapter: FusionAdapter, checks: list[dict[str, Any]]) -> Envelope:
    try:
        script = build_validate_design(checks)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    env = execute_typed_script(adapter, script, read_only=True)
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return script_parse_failure(env, "validation_parse_failed", read_only=True)
    return Envelope(state=env.state, ok=parsed.get("ok") is True, error=parsed.get("error"), result=parsed, message=env.message)
