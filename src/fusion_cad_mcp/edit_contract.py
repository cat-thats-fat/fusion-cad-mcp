"""Bounded request and evidence for a guarded numeric length parameter edit.

The measurement routines supply root-context observations. This module can also
run inside the generated Fusion script so admission and comparison use the same
rules on both sides of the bridge.
"""

import json
import math
import re
import uuid
from typing import Any

EDIT_CONTRACT_VERSION = 1
MAX_EDIT_CHECKS = 32

# Each metric fixes its entity kind, number of selectors, units and native method.
_CONTRACT_METRICS = {
    "body_extent": ("body", 1, "mm", "BRepBody.preciseBoundingBox"),
    "body_volume": ("body", 1, "mm^3", "BRepBody.volume"),
    "occurrence_translation": ("occurrence", 1, "mm", "Occurrence.transform2"),
    "clearance": (
        "body",
        2,
        "mm",
        "MeasureManager.measureMinimumDistance and Design.analyzeInterference",
    ),
    "interference": ("body", 2, "mm^3", "Design.analyzeInterference"),
}
_CONTRACT_AXIS_METRICS = ("body_extent", "occurrence_translation")
_CONTRACT_OCCURRENCE_PATH = re.compile(r"[^+]+:[0-9]+(?:\+[^+]+:[0-9]+)*")
_CONTRACT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_CONTRACT_LENGTH_LITERAL = re.compile(
    r"(?<![\w.])(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    r"\s*(?:mm|cm|m|in|ft|yd|um|µm|μm)\b"
)


def _contract_text(value: Any, label: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or not value.strip()
        or not value.isprintable()
    ):
        raise ValueError(
            f"{label} must be a nonempty printable string of at most {limit} characters"
        )
    return value


def _contract_finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _contract_number(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if not _contract_finite(value) or (nonnegative and value < 0):
        description = "finite nonnegative" if nonnegative else "finite"
        raise ValueError(f"{label} must be a {description} number")
    return float(value) if value != 0 else 0.0


def _contract_selector(value: Any, label: str, kind: str) -> str:
    selector = _contract_text(value, label, 256)
    if len(json.dumps(selector, ensure_ascii=True)) - 2 > 256:
        raise ValueError(f"{label} exceeds 256 bytes when JSON-encoded")
    if kind == "body":
        path, separator, name = selector.partition(" / ")
        if (
            separator
            and name
            and name == name.strip()
            and " / " not in name
            and (path == "root" or _CONTRACT_OCCURRENCE_PATH.fullmatch(path))
        ):
            return selector
        raise ValueError(f"{label} must be 'root / Body' or '<full occurrence path> / Body'")
    if " / " not in selector and _CONTRACT_OCCURRENCE_PATH.fullmatch(selector):
        return selector
    raise ValueError(f"{label} must be a full occurrence path")


def _prepare_contract_check(check: Any, label: str, *, protected: bool) -> dict[str, Any]:
    if not isinstance(check, dict):
        raise ValueError(f"{label} must be an object")
    metric = check.get("metric")
    if not isinstance(metric, str) or metric not in _CONTRACT_METRICS:
        raise ValueError(f"{label}.metric is unsupported")
    kind, entity_count, units, _ = _CONTRACT_METRICS[metric]
    fields = {
        "id",
        "metric",
        "entities",
        "frame",
        "units",
        "baseline",
        "baseline_tolerance",
        "tolerance",
    }
    if metric in _CONTRACT_AXIS_METRICS:
        fields.add("axis")
    if not protected:
        fields.update(("comparator", "expected"))
    if check.keys() != fields:
        raise ValueError(f"{label} has missing or unsupported fields")
    check_id = _contract_text(check["id"], f"{label}.id", 64)
    if not _CONTRACT_ID.fullmatch(check_id):
        raise ValueError(
            f"{label}.id must be an identifier using letters, digits, dots, underscores or hyphens"
        )
    if check["frame"] != "root":
        raise ValueError(f"{label}.frame must be 'root'")
    if check["units"] != units:
        raise ValueError(f"{label}.units must be '{units}' for {metric}")
    entities = check["entities"]
    if not isinstance(entities, list) or len(entities) != entity_count:
        raise ValueError(f"{label}.entities must have {entity_count} selector(s)")
    selectors = [
        _contract_selector(value, f"{label}.entities[{i}]", kind)
        for i, value in enumerate(entities)
    ]
    if len(set(selectors)) != entity_count:
        raise ValueError(f"{label}.entities must name distinct entities")
    nonnegative = metric != "occurrence_translation"
    prepared = {
        "id": check_id,
        "metric": metric,
        "entities": selectors,
        "frame": "root",
        "units": units,
        "baseline": _contract_number(
            check["baseline"], f"{label}.baseline", nonnegative=nonnegative
        ),
        "baseline_tolerance": _contract_number(
            check["baseline_tolerance"], f"{label}.baseline_tolerance", nonnegative=True
        ),
        "tolerance": _contract_number(check["tolerance"], f"{label}.tolerance", nonnegative=True),
    }
    if "axis" in fields:
        if check["axis"] not in ("x", "y", "z"):
            raise ValueError(f"{label}.axis must be 'x', 'y' or 'z'")
        prepared["axis"] = check["axis"]
    if not protected:
        if check["comparator"] not in ("eq", "ge", "le"):
            raise ValueError(f"{label}.comparator must be 'eq', 'ge' or 'le'")
        prepared["comparator"] = check["comparator"]
        prepared["expected"] = _contract_number(
            check["expected"], f"{label}.expected", nonnegative=nonnegative
        )
    return prepared


def prepare_edit_contract(
    *,
    target_handle: str,
    operation_id: str,
    parameter_name: str,
    expression: str,
    expected_expression: str,
    expected_value_mm: float,
    value_tolerance_mm: float,
    requirements: list[dict[str, Any]],
    protected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize a V1 contract before opening or writing a Fusion document.

    The target handle is opaque here. Fusion checks its live session binding and
    confirms the expression's grammar and dimensional units before assignment.
    """
    operation_id = _contract_text(operation_id, "operation_id", 36)
    try:
        canonical_id = str(uuid.UUID(operation_id))
    except ValueError:
        canonical_id = None
    if canonical_id != operation_id:
        raise ValueError("operation_id must be a canonical UUID")
    expression = _contract_text(expression, "expression", 512)
    if not _CONTRACT_LENGTH_LITERAL.search(expression):
        raise ValueError("expression must contain an explicit length-unit literal")
    if protected is None:
        protected = []
    if not isinstance(requirements, list) or not requirements or not isinstance(protected, list):
        raise ValueError("requirements must be a nonempty list and protected must be a list")
    if len(requirements) + len(protected) > MAX_EDIT_CHECKS:
        raise ValueError(
            f"requirements and protected together may contain at most {MAX_EDIT_CHECKS} checks"
        )
    required_rows = [
        _prepare_contract_check(item, f"requirements[{i}]", protected=False)
        for i, item in enumerate(requirements)
    ]
    protected_rows = [
        _prepare_contract_check(item, f"protected[{i}]", protected=True)
        for i, item in enumerate(protected)
    ]
    all_ids = [item["id"] for item in required_rows + protected_rows]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("check ids must be unique across requirements and protected")
    if not any(item["metric"] == "body_extent" for item in required_rows):
        raise ValueError("requirements must include a body_extent check")
    return {
        "version": EDIT_CONTRACT_VERSION,
        "target_handle": _contract_text(target_handle, "target_handle", 256),
        "operation_id": operation_id,
        "parameter_name": _contract_text(parameter_name, "parameter_name", 128),
        "expression": expression,
        "expected_expression": _contract_text(expected_expression, "expected_expression", 512),
        "expected_value_mm": _contract_number(expected_value_mm, "expected_value_mm"),
        "value_tolerance_mm": _contract_number(
            value_tolerance_mm, "value_tolerance_mm", nonnegative=True
        ),
        "requirements": required_rows,
        "protected": protected_rows,
    }


def _contract_measurement(
    spec: dict, observations: Any
) -> tuple[float | None, str | None, str, int | None]:
    row = observations.get(spec["id"]) if isinstance(observations, dict) else None
    if not isinstance(row, dict):
        return None, "measurement_missing", "unknown", None
    if any(
        row.get(key) != spec.get(key) for key in ("metric", "entities", "frame", "units", "axis")
    ):
        return None, "measurement_scope_mismatch", "unknown", None
    reason = row.get("reason")
    if isinstance(reason, str) and reason:
        reason = "".join(
            character if character.isprintable() else " " for character in reason[:160]
        )
    elif reason is not None:
        reason = "measurement_unavailable"
    classification = row.get("classification")
    if classification != "exact":
        if classification == "heuristic":
            return None, reason or "exact_measurement_unavailable", "heuristic", None
        return None, reason or "measurement_classification_unavailable", "unknown", None
    count = None
    if spec["metric"] == "clearance":
        count = row.get("interference_count")
        if type(count) is not int or not 0 <= count <= 2**31 - 1:
            return None, reason or "interference_count_unavailable", "unknown", None
    unavailable_classification = "exact" if count else "unknown"
    if reason is not None:
        return None, reason, unavailable_classification, count
    value = row.get("value")
    if not _contract_finite(value) or (spec["metric"] != "occurrence_translation" and value < 0):
        return None, "measurement_value_unavailable", unavailable_classification, count
    return float(value) if value != 0 else 0.0, None, classification, count


def _contract_subject(spec: dict, *, protected: bool) -> dict[str, Any]:
    subject = {key: spec[key] for key in ("id", "metric", "frame", "units")}
    subject["entities"] = spec["entities"].copy()
    if "axis" in spec:
        subject["axis"] = spec["axis"]
    return {**subject, "protected": protected, "method": _CONTRACT_METRICS[spec["metric"]][3]}


def within_tolerance(observed: float, expected: float, tolerance: float) -> bool:
    """Compare finite values against the closed interval in measurement precision."""
    return expected - tolerance <= observed <= expected + tolerance


def _contract_baseline_row(spec: dict, observations: Any, *, protected: bool) -> dict[str, Any]:
    value, reason, classification, count = _contract_measurement(spec, observations)
    if count:
        status, reason = "failed", "interference_detected"
    elif value is None:
        status = "unknown"
    elif not within_tolerance(value, spec["baseline"], spec["baseline_tolerance"]):
        status, reason = "failed", "baseline_mismatch"
    else:
        status = "passed"
    row = {
        **_contract_subject(spec, protected=protected),
        "baseline": value,
        "expected": spec["baseline"],
        "tolerance": spec["baseline_tolerance"],
        "status": status,
        "classification": classification,
        "reason": reason,
    }
    if count is not None:
        row["interference_count"] = count
    return row


def _contract_check_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checks": rows,
        "failed_requirement_ids": [row["id"] for row in rows if row["status"] == "failed"],
        "unknown_requirement_ids": [row["id"] for row in rows if row["status"] == "unknown"],
    }


def compare_baseline(contract: dict, baseline: Any) -> dict[str, Any]:
    """Compare fresh trusted evidence with every precondition before assignment."""
    rows = [
        _contract_baseline_row(spec, baseline, protected=protected)
        for group, protected in (("requirements", False), ("protected", True))
        for spec in contract[group]
    ]
    return {
        **_contract_check_summary(rows),
        "passed": bool(rows) and all(row["status"] == "passed" for row in rows),
    }


def _contract_difference(observed: float | None, reference: float | None) -> float | None:
    if observed is None or reference is None:
        return None
    difference = observed - reference
    if not math.isfinite(difference):
        return None
    return difference if difference != 0 else 0.0


def _contract_result_row(
    spec: dict, baseline: Any, result: Any, *, protected: bool
) -> dict[str, Any]:
    before = _contract_baseline_row(spec, baseline, protected=protected)
    value, reason, classification, count = _contract_measurement(spec, result)
    comparator = "eq" if protected else spec["comparator"]
    expected = before["baseline"] if protected else spec["expected"]
    tolerance = spec["tolerance"]
    delta = _contract_difference(value, before["baseline"])
    difference = _contract_difference(value, expected)
    margin = None
    if before["status"] != "passed":
        status, reason = "unknown", "baseline_unconfirmed"
    elif count:
        status, reason = "failed", "interference_detected"
    elif value is None:
        status = "unknown"
    elif delta is None or difference is None:
        status, reason = "unknown", "measurement_difference_out_of_range"
    else:
        if comparator == "eq":
            passed = within_tolerance(value, expected, tolerance)
        elif comparator == "ge":
            margin = difference
            passed = value >= expected - tolerance
        else:
            margin = -difference if difference != 0 else 0.0
            passed = value <= expected + tolerance
        status = "passed" if passed else "failed"
        reason = None if passed else "outside_tolerance"
    row = {
        **_contract_subject(spec, protected=protected),
        "baseline": before["baseline"],
        "result": value,
        "delta": delta,
        "comparator": comparator,
        "expected": expected,
        "tolerance": tolerance,
        "status": status,
        "classification": classification,
        "reason": reason,
    }
    if margin is not None:
        row["raw_margin"] = margin
    if count is not None:
        row["interference_count"] = count
    return row


def compare_evidence(
    contract: dict, baseline: Any, result: Any, *, execution: str
) -> dict[str, Any]:
    """Evaluate trusted readings without deriving execution status from check results."""
    if execution in ("refused", "failed"):
        return {**_contract_check_summary([]), "execution": execution, "verification": "not_run"}
    if execution not in ("completed", "unknown"):
        raise ValueError("execution must be 'refused', 'completed', 'failed' or 'unknown'")
    rows = [
        _contract_result_row(
            spec, baseline, result if execution == "completed" else None, protected=protected
        )
        for group, protected in (("requirements", False), ("protected", True))
        for spec in contract[group]
    ]
    summary = _contract_check_summary(rows)
    if summary["failed_requirement_ids"]:
        verification = "failed"
    elif summary["unknown_requirement_ids"] or not rows:
        verification = "incomplete"
    else:
        verification = "verified"
    return {**summary, "execution": execution, "verification": verification}
