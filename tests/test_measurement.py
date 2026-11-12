"""Numerical witness checks use independently constructed point geometry."""

import json
import math
from types import SimpleNamespace as NS

import pytest

from fusion_cad_mcp.measurement import DISTANCE_EVIDENCE_SCRIPT


def evidence(result, anchor=None):
    namespace = {"math": math, "json": json}
    exec(DISTANCE_EVIDENCE_SCRIPT, namespace)
    return namespace["_distance_evidence"](result, anchor)


def point(x=0, y=0, z=0):
    return NS(x=x, y=y, z=z)


def test_independent_three_four_five_geometry_and_zero():
    result = evidence(NS(value=5, positionOne=point(), positionTwo=point(3, 4)))
    assert result["minimum_distance_mm"] == 50
    assert result["witness_consistency"]["separation_mm"] == 50
    assert result["witness_consistency"]["status"] == "consistent"
    assert evidence(NS(value=0, positionOne=point(), positionTwo=point()))["witness_consistency"]["status"] == "consistent"


def test_repeated_point_preserves_field_run_scalar_without_valid_witness():
    result = evidence(NS(value=0.0777739, positionOne=point(3, 5, 7), positionTwo=point(3, 5, 7)))
    assert result["scalar_valid"] is True
    assert result["minimum_distance_mm"] == pytest.approx(0.777739)
    assert result["closest_point_b_mm"] == [30, 50, 70]
    assert result["witness_consistency"]["status"] == "inconsistent"
    assert result["witness_consistency"]["scalar_delta_mm"] == pytest.approx(0.777739)


@pytest.mark.parametrize("native", [float("nan"), float("inf"), -1, True, "1", 1e308, 10**1000])
def test_malformed_scalar_is_json_safe(native):
    result = evidence(NS(value=native, positionOne=point(), positionTwo=point()))
    assert not result["scalar_valid"]
    assert result["minimum_distance_mm"] is None
    assert result["witness_consistency"]["status"] == "unavailable"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("bad", [None, point(float("nan")), point(float("inf")), point(True), point(1e308)])
def test_bad_points_do_not_discard_finite_scalar(bad):
    result = evidence(NS(value=2, positionOne=point(), positionTwo=bad))
    assert result["minimum_distance_mm"] == 20 and result["scalar_valid"]
    assert result["witness_consistency"]["status"] == "unavailable"
    json.dumps(result, allow_nan=False)


def test_invalid_native_result_and_missing_result_are_not_validated():
    result = evidence(NS(value=1, positionOne=point(), positionTwo=point(1), isValid=False))
    assert result["minimum_distance_mm"] == 10 and not result["scalar_valid"]
    assert result["witness_consistency"]["status"] == "unavailable"
    assert not evidence(None)["scalar_valid"]


def test_numerical_tolerance_is_small_fixed_floor_with_roundoff():
    near = evidence(NS(value=1, positionOne=point(), positionTwo=point(1 + 5e-8)))
    far = evidence(NS(value=1, positionOne=point(), positionTwo=point(1 + 2e-7)))
    assert near["witness_consistency"]["status"] == "consistent"
    assert far["witness_consistency"]["status"] == "inconsistent"
    assert near["witness_consistency"]["numerical_tolerance_mm"] == pytest.approx(1e-6)


def test_input_anchor_checked_even_when_scalar_separation_matches():
    result = evidence(NS(value=5, positionOne=point(1, 0), positionTwo=point(4, 4)), [0, 0, 0])
    assert result["witness_consistency"]["scalar_delta_mm"] == 0
    assert result["witness_consistency"]["input_point_delta_mm"] == 10
    assert result["witness_consistency"]["status"] == "inconsistent"
