import json
import math

import pytest

from fusion_cad_mcp.edit_contract import compare_baseline, compare_evidence, prepare_edit_contract

_OPERATION_ID = "12345678-1234-4234-9234-123456789abc"


def _extent(**overrides):
    return {
        "id": "width",
        "metric": "body_extent",
        "entities": ["root / Shell"],
        "frame": "root",
        "units": "mm",
        "axis": "x",
        "baseline": 60,
        "baseline_tolerance": 0.125,
        "comparator": "eq",
        "expected": 70,
        "tolerance": 0.125,
        **overrides,
    }


def _pose(**overrides):
    return {
        "id": "mount-position",
        "metric": "occurrence_translation",
        "entities": ["Parent:1+Mount:2"],
        "frame": "root",
        "units": "mm",
        "axis": "z",
        "baseline": -5,
        "baseline_tolerance": 0.125,
        "tolerance": 0.125,
        **overrides,
    }


def _contract(**overrides):
    return prepare_edit_contract(
        **{
            "target_handle": "session-owned-working-copy",
            "operation_id": _OPERATION_ID,
            "parameter_name": "shellWidth",
            "expression": "pitch * 2 + 1 in",
            "expected_expression": "60 mm",
            "expected_value_mm": 60,
            "value_tolerance_mm": 0.125,
            "requirements": [_extent()],
            **overrides,
        }
    )


def _observation(spec, value, **overrides):
    subject = {
        key: spec[key] for key in ("metric", "entities", "frame", "units", "axis") if key in spec
    }
    return {**subject, "classification": "exact", "value": value, **overrides}


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", None),
        ("operation_id", _OPERATION_ID.upper()),
        ("operation_id", _OPERATION_ID.replace("-", "")),
        ("target_handle", ""),
        ("target_handle", "handle\nextra"),
        ("target_handle", "x" * 257),
        ("parameter_name", "  "),
        ("expression", "70"),
        ("expression", "pitch * 2"),
        ("expression", "70 mmOther"),
        ("expected_expression", None),
        ("expected_value_mm", True),
        ("expected_value_mm", 10**400),
        ("value_tolerance_mm", -0.01),
        ("value_tolerance_mm", float("inf")),
        ("requirements", []),
        ("requirements", {}),
        ("protected", {}),
    ],
)
def test_invalid_request_values_rejected(field, value):
    with pytest.raises(ValueError):
        _contract(**{field: value})


@pytest.mark.parametrize("expression", ["70mm", "2.75 in", "pitch * 2 + 1e-3 m"])
def test_explicit_length_literal_forms(expression):
    assert _contract(expression=expression)["expression"] == expression


@pytest.mark.parametrize(
    "overrides",
    [
        {"metric": []},
        {"metric": "dimension"},
        {"units": "cm"},
        {"units": "mm^3"},
        {"frame": "component"},
        {"frame": None},
        {"axis": "z+"},
        {"comparator": "lt"},
        {"id": "with spaces"},
        {"id": "x" * 65},
        {"entities": ["Shell"]},
        {"entities": ["Assembly / Shell"]},
        {"entities": ["root / "]},
        {"entities": ["root / Outer / Shell"]},
        {"entities": ["root / " + "x" * 250]},
        {"entities": ["root / " + "🚀" * 21]},
        {"entities": ["root / Shell", "root / Other"]},
        {"baseline": -1},
        {"expected": float("nan")},
        {"tolerance": float("inf")},
        {"baseline_tolerance": True},
        {"required": False},
    ],
)
def test_invalid_check_fields_rejected(overrides):
    with pytest.raises(ValueError):
        _contract(requirements=[_extent(**overrides)])


def test_check_bound_uniqueness_and_required_extent():
    rows = [_extent(id=f"width-{i}") for i in range(32)]
    assert len(_contract(requirements=rows)["requirements"]) == 32
    with pytest.raises(ValueError, match="at most 32"):
        _contract(requirements=rows, protected=[_pose()])
    with pytest.raises(ValueError, match="unique"):
        _contract(protected=[_pose(id="width")])
    with pytest.raises(ValueError, match="body_extent"):
        _contract(requirements=[_pose(comparator="eq", expected=-5)])
    with pytest.raises(ValueError, match="distinct"):
        _contract(requirements=[_extent(), _clearance(entities=["root / Shell"] * 2)])
    with pytest.raises(ValueError, match="full occurrence path"):
        _contract(protected=[_pose(entities=["Mount"])])
    with pytest.raises(ValueError, match="missing or unsupported"):
        _contract(protected=[_pose(expected=-5)])


def test_exact_boundaries_and_protected_comparison_use_measured_baseline():
    width = _extent(entities=["Chassis:2+Panel:1 / Shell"])
    pose = _pose()
    contract = _contract(requirements=[width], protected=[pose])
    baseline = {"width": _observation(width, 60.125), "mount-position": _observation(pose, -5.125)}
    after = {"width": _observation(width, 70.125), "mount-position": _observation(pose, -5.25)}

    assert compare_baseline(contract, baseline)["passed"]
    compared = compare_evidence(contract, baseline, after, execution="completed")
    assert compared["verification"] == "verified"
    width_row, pose_row = compared["checks"]
    assert width_row["classification"] == "exact"
    assert width_row["frame"] == "root"
    assert width_row["method"] == "BRepBody.preciseBoundingBox"
    assert width_row["entities"] == ["Chassis:2+Panel:1 / Shell"]
    assert (width_row["baseline"], width_row["result"], width_row["delta"]) == (60.125, 70.125, 10)
    assert pose_row["protected"] is True
    assert pose_row["expected"] == -5.125
    assert pose_row["delta"] == -0.125

    after["mount-position"]["value"] = -4.875
    failed = compare_evidence(contract, baseline, after, execution="completed")
    assert failed["execution"] == "completed"
    assert failed["verification"] == "failed"
    assert failed["failed_requirement_ids"] == ["mount-position"]
    assert failed["checks"][1]["delta"] == 0.25


@pytest.mark.parametrize(
    "comparator,value,margin,verification",
    [
        ("ge", 69.875, -0.125, "verified"),
        ("ge", 69.75, -0.25, "failed"),
        ("ge", 70.25, 0.25, "verified"),
        ("le", 70.125, -0.125, "verified"),
        ("le", 70.25, -0.25, "failed"),
        ("le", 69.75, 0.25, "verified"),
    ],
)
def test_limit_comparison_retains_raw_margin(comparator, value, margin, verification):
    width = _extent(comparator=comparator)
    contract = _contract(requirements=[width])
    compared = compare_evidence(
        contract,
        {"width": _observation(width, 60)},
        {"width": _observation(width, value)},
        execution="completed",
    )
    assert compared["verification"] == verification
    assert compared["checks"][0]["raw_margin"] == margin


@pytest.mark.parametrize(
    "comparator,boundary,away",
    [
        ("eq", 70.01, math.inf),
        ("ge", 69.99, -math.inf),
        ("le", 70.01, math.inf),
    ],
)
def test_decimal_tolerance_includes_boundary_without_rounding(comparator, boundary, away):
    width = _extent(
        baseline=70,
        baseline_tolerance=0.01,
        expected=70,
        tolerance=0.01,
        comparator=comparator,
    )
    contract = _contract(requirements=[width])
    baseline = {"width": _observation(width, 70.01)}
    assert compare_baseline(contract, baseline)["passed"] is True
    outside_baseline = {"width": _observation(width, math.nextafter(70.01, math.inf))}
    assert compare_baseline(contract, outside_baseline)["passed"] is False

    on_boundary = {"width": _observation(width, boundary)}
    outside = {"width": _observation(width, math.nextafter(boundary, away))}
    assert (
        compare_evidence(contract, baseline, on_boundary, execution="completed")["verification"]
        == "verified"
    )
    assert (
        compare_evidence(contract, baseline, outside, execution="completed")["verification"]
        == "failed"
    )


@pytest.mark.parametrize(
    "override,reason",
    [
        ({"metric": "body_volume"}, "measurement_scope_mismatch"),
        ({"entities": ["Other:1 / Shell"]}, "measurement_scope_mismatch"),
        ({"units": "cm"}, "measurement_scope_mismatch"),
        ({"frame": "local"}, "measurement_scope_mismatch"),
        ({"axis": "y"}, "measurement_scope_mismatch"),
        ({"classification": "heuristic"}, "exact_measurement_unavailable"),
        ({"classification": None}, "measurement_classification_unavailable"),
        ({"value": True}, "measurement_value_unavailable"),
        ({"value": float("nan")}, "measurement_value_unavailable"),
        ({"value": float("inf")}, "measurement_value_unavailable"),
        ({"value": 10**400}, "measurement_value_unavailable"),
        ({"value": -1}, "measurement_value_unavailable"),
        ({"reason": "body_ambiguous"}, "body_ambiguous"),
    ],
)
def test_unusable_or_misscoped_measurements_never_pass(override, reason):
    width = _extent()
    contract = _contract()
    invalid = _observation(width, 70)
    invalid.update(override)
    baseline = {"width": _observation(width, 60)}

    compared = compare_evidence(contract, baseline, {"width": invalid}, execution="completed")
    assert compared["verification"] == "incomplete"
    assert compared["unknown_requirement_ids"] == ["width"]
    assert compared["checks"][0]["reason"] == reason
    assert compared["checks"][0]["result"] is None
    assert compared["checks"][0]["delta"] is None
    json.dumps(compared, allow_nan=False)


def test_stale_baseline_fails_admission_and_cannot_verify():
    width = _extent()
    contract = _contract()
    stale = {"width": _observation(width, 60.25)}
    baseline_result = compare_baseline(contract, stale)
    assert baseline_result["passed"] is False
    assert baseline_result["failed_requirement_ids"] == ["width"]
    assert baseline_result["checks"][0]["reason"] == "baseline_mismatch"
    compared = compare_evidence(
        contract, stale, {"width": _observation(width, 70)}, execution="completed"
    )
    assert compared["verification"] == "incomplete"
    assert compared["checks"][0]["reason"] == "baseline_unconfirmed"


def test_execution_state_and_mixed_failed_missing_evidence_stay_separate():
    width, pose = _extent(), _pose()
    contract = _contract(protected=[pose])
    baseline = {"width": _observation(width, 60), "mount-position": _observation(pose, -5)}
    after = {"width": _observation(width, 71), "verified": True, "ok": True}
    compared = compare_evidence(contract, baseline, after, execution="completed")
    assert compared["verification"] == "failed"
    assert compared["failed_requirement_ids"] == ["width"]
    assert compared["unknown_requirement_ids"] == ["mount-position"]

    for execution, verification in [
        ("unknown", "incomplete"),
        ("refused", "not_run"),
        ("failed", "not_run"),
    ]:
        good = {"width": _observation(width, 70), "mount-position": _observation(pose, -5)}
        compared = compare_evidence(contract, baseline, good, execution=execution)
        assert (compared["execution"], compared["verification"]) == (execution, verification)


def _clearance(**overrides):
    spec = _extent(
        **{
            "id": "gap",
            "metric": "clearance",
            "entities": ["root / Shell", "Part:1 / Pin"],
            "baseline": 5,
            "comparator": "ge",
            "expected": 0,
            **overrides,
        }
    )
    del spec["axis"]
    return spec


@pytest.mark.parametrize("measured", [9, None])
def test_clearance_known_interference_fails_despite_distance(measured):
    width, gap = _extent(), _clearance()
    contract = _contract(requirements=[width, gap])
    baseline = {"width": _observation(width, 60), "gap": _observation(gap, 5, interference_count=0)}
    after = {
        "width": _observation(width, 70),
        "gap": _observation(gap, measured, interference_count=1),
    }

    assert compare_baseline(contract, baseline)["passed"] is True
    stale = {**baseline, "gap": _observation(gap, 5, interference_count=1)}
    assert compare_baseline(contract, stale)["failed_requirement_ids"] == ["gap"]
    compared = compare_evidence(contract, baseline, after, execution="completed")
    assert compared["verification"] == "failed"
    assert compared["failed_requirement_ids"] == ["gap"]
    assert compared["checks"][1]["classification"] == "exact"
    assert compared["checks"][1]["interference_count"] == 1
    assert compared["checks"][1]["reason"] == "interference_detected"
    del after["gap"]["interference_count"]
    assert (
        compare_evidence(contract, baseline, after, execution="completed")["verification"]
        == "incomplete"
    )


@pytest.mark.parametrize(
    "metric,selectors",
    [
        ("body_volume", ["root / Shell"]),
        ("interference", ["root / Shell", "Mount:1 / Pin"]),
    ],
)
def test_volume_metrics_require_cubic_units(metric, selectors):
    width = _extent()
    volume = _clearance(metric=metric, entities=selectors, baseline=5, expected=4, comparator="le")
    with pytest.raises(ValueError, match="mm\\^3"):
        _contract(requirements=[width, volume])
    volume["units"] = "mm^3"
    contract = _contract(requirements=[width, volume])
    baseline = {"width": _observation(width, 60), "gap": _observation(volume, 5)}
    after = {"width": _observation(width, 70), "gap": _observation(volume, 4)}
    compared = compare_evidence(contract, baseline, after, execution="completed")
    assert compared["verification"] == "verified"
    assert compared["checks"][1]["units"] == "mm^3"
    assert compared["checks"][1]["delta"] == -1


def test_zero_normalization_and_finite_delta():
    width = _extent(baseline=-0.0, expected=-0.0, tolerance=-0.0)
    pose = _pose(baseline=-1.7e308)
    contract = _contract(requirements=[width], protected=[pose])
    baseline = {"width": _observation(width, -0.0), "mount-position": _observation(pose, -1.7e308)}
    after = {"width": _observation(width, -0.0), "mount-position": _observation(pose, 1.7e308)}
    compared = compare_evidence(contract, baseline, after, execution="completed")
    width_row, pose_row = compared["checks"]
    for field in ("baseline", "result", "delta", "expected", "tolerance"):
        assert width_row[field] == 0.0 and math.copysign(1, width_row[field]) == 1
    assert pose_row["delta"] is None
    assert pose_row["status"] == "unknown"
    json.dumps(compared, allow_nan=False)


def test_unknown_native_diagnostic_is_bounded_and_printable():
    width = _extent()
    result = {
        "width": _observation(width, None, classification="unknown", reason="failure\n" + "x" * 300)
    }
    compared = compare_evidence(
        _contract(), {"width": _observation(width, 60)}, result, execution="completed"
    )
    reason = compared["checks"][0]["reason"]
    assert len(reason) == 160 and reason.isprintable() and reason.startswith("failure ")
