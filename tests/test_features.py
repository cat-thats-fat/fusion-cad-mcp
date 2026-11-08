"""Tier 1a/1c tests for Group 3 feature generators and run wrappers."""

from __future__ import annotations

import ast
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import features as f
from tests._assembly_fakes import Collection, ScriptAdapter, point, root_with

# ---------- extrude ----------


def test_build_extrude_distance_emits_setDistanceExtent():
    src = f.build_extrude("outer", 0, "new_body", "distance", "thickness")
    ast.parse(src)
    assert "PositiveExtentDirection" in src
    assert 'createByString("thickness")' in src
    assert "NewBodyFeatureOperation" in src


def test_build_extrude_distance_negative_direction():
    src = f.build_extrude("outer", 0, "cut", "distance", "5 mm", direction="negative")
    assert "NegativeExtentDirection" in src
    assert "CutFeatureOperation" in src


def test_build_extrude_symmetric_full_length():
    src = f.build_extrude("outer", 0, "new_body", "symmetric", "10 mm", is_full_length=True)
    assert "setSymmetricExtent" in src
    assert 'createByString("10 mm")' in src


def test_build_extrude_all_positive():
    src = f.build_extrude("outer", 0, "cut", "all_positive")
    assert "setAllExtent" in src
    assert "PositiveExtentDirection" in src


def test_build_extrude_all_negative():
    src = f.build_extrude("outer", 0, "cut", "all_negative")
    assert "NegativeExtentDirection" in src


def test_build_extrude_participants_wires_find_body():
    src = f.build_extrude("outer", 0, "cut", "all_negative", participants=["plate", "rib"])
    ast.parse(src)
    assert "_select_body" in src
    assert '"plate"' in src
    assert '"rib"' in src
    assert "participantBodies" in src


def test_build_extrude_rejects_unknown_operation():
    with pytest.raises(ValueError):
        f.build_extrude("outer", 0, "morph", "distance", "5 mm")


def test_build_extrude_distance_requires_expression():
    with pytest.raises(ValueError):
        f.build_extrude("outer", 0, "new_body", "distance")


def test_build_extrude_invalid_extent_kind():
    with pytest.raises(ValueError):
        f.build_extrude("outer", 0, "new_body", "until_face", "5 mm")


def test_build_extrude_invalid_direction():
    with pytest.raises(ValueError):
        f.build_extrude("outer", 0, "new_body", "distance", "5 mm", direction="up")


# ---------- fillet by geometry ----------


def test_build_fillet_z_parallel_emits_correct_axis_check():
    src = f.build_fillet_edges_by_geometry("plate", "corner_r", parallel_to="z")
    ast.parse(src)
    # Z-parallel: x and y match, z differs
    assert "abs(sp.x - ep.x) < 1e-6 and abs(sp.y - ep.y) < 1e-6 and abs(sp.z - ep.z) > 1e-6" in src
    assert 'createByString("corner_r")' in src


def test_build_fillet_y_parallel():
    src = f.build_fillet_edges_by_geometry("plate", "5 mm", parallel_to="y")
    assert "abs(sp.x - ep.x) < 1e-6 and abs(sp.z - ep.z) < 1e-6 and abs(sp.y - ep.y) > 1e-6" in src


def test_build_fillet_any_parallel_skips_axis_filter(monkeypatch):
    diagonal = SimpleNamespace(
        length=3 ** 0.5,
        startVertex=SimpleNamespace(geometry=point()),
        endVertex=SimpleNamespace(geometry=point(1, 1, 1)),
    )
    body = SimpleNamespace(name="plate", edges=Collection([diagonal]))
    edge_set = Mock()
    fillets = SimpleNamespace(
        createInput=lambda: SimpleNamespace(addConstantRadiusEdgeSet=edge_set),
        add=lambda _: SimpleNamespace(name="Fillet", healthState=0),
    )
    root = root_with(bodies=[body], features=SimpleNamespace(filletFeatures=fillets))
    adapter = ScriptAdapter(
        monkeypatch,
        SimpleNamespace(rootComponent=root),
        core={
            "ObjectCollection": SimpleNamespace(create=Collection),
            "ValueInput": SimpleNamespace(createByString=lambda value: value),
        },
    )

    env = f.fillet_edges_by_geometry(adapter, "plate", "1 mm", parallel_to="any")

    assert env.ok and env.result["edges_filleted"] == 1
    edge_set.assert_called_once()
    assert edge_set.call_args.args[0].items == [diagonal]


def test_build_fillet_min_length_filter():
    src = f.build_fillet_edges_by_geometry("plate", "1 mm", parallel_to="z", min_length_mm=3.0)
    # 3 mm = 0.3 cm
    assert "edge_length_cm >= 0.3" in src


def test_build_fillet_rejects_unknown_axis():
    with pytest.raises(ValueError):
        f.build_fillet_edges_by_geometry("plate", "1 mm", parallel_to="diagonal")


def test_build_fillet_rejects_empty_radius():
    with pytest.raises(ValueError):
        f.build_fillet_edges_by_geometry("plate", "", parallel_to="z")


# ---------- chamfer by geometry ----------


def test_build_chamfer_equal_kind():
    src = f.build_chamfer_edges_by_geometry("plate", "1 mm", kind="equal")
    ast.parse(src)
    # Current API: createInput2() takes no args; edge sets are added through
    # chamferEdgeSets (the old add*ChamferEdges methods no longer exist).
    assert "chamferEdgeSets.addEqualDistanceChamferEdgeSet" in src
    assert "createInput2()" in src


def test_build_chamfer_two_dist_requires_distance2():
    with pytest.raises(ValueError):
        f.build_chamfer_edges_by_geometry("plate", "1 mm", kind="two_dist")


def test_build_chamfer_dist_angle_emits_correct_method():
    src = f.build_chamfer_edges_by_geometry("plate", "2 mm", kind="dist_angle", angle="45 deg")
    assert "chamferEdgeSets.addDistanceAndAngleChamferEdgeSet" in src
    assert 'createByString("45 deg")' in src


def test_build_chamfer_rejects_unknown_kind():
    with pytest.raises(ValueError):
        f.build_chamfer_edges_by_geometry("plate", "1 mm", kind="diagonal")


# ---------- mirror_feature ----------


def test_build_mirror_xy_plane():
    src = f.build_mirror_feature("plate_extrude", "xy")
    ast.parse(src)
    assert "root.xYConstructionPlane" in src


def test_build_mirror_named_plane_uses_lookup():
    src = f.build_mirror_feature("plate_extrude", "mid_plane")
    assert "root.constructionPlanes.item" in src
    assert '"mid_plane"' in src


# ---------- pattern_rectangular ----------


def test_build_pattern_rect_x_only_no_y():
    src = f.build_pattern_rectangular("rib", x_axis="x", x_count=5, x_distance="40 mm")
    ast.parse(src)
    assert "createByReal(5)" in src
    assert 'createByString("40 mm")' in src


def test_build_pattern_rect_two_directions():
    src = f.build_pattern_rectangular(
        "rib", x_axis="x", x_count=3, x_distance="30 mm", y_axis="y", y_count=2, y_distance="20 mm"
    )
    assert "setDirectionTwo" in src


def test_pattern_rect_requires_x_distance_when_count_gt_1():
    with pytest.raises(ValueError):
        f.build_pattern_rectangular("rib", x_count=3)  # no x_distance


def test_pattern_rect_rejects_count_zero():
    with pytest.raises(ValueError):
        f.build_pattern_rectangular("rib", x_count=0, x_distance="10 mm")


# ---------- pattern_circular ----------


def test_build_pattern_circular_default_z_axis():
    src = f.build_pattern_circular("hole", axis="z", count=6, total_angle="360 deg")
    ast.parse(src)
    assert "root.zConstructionAxis" in src
    assert "createByReal(6)" in src
    assert 'createByString("360 deg")' in src


def test_pattern_circular_rejects_count_less_than_2():
    with pytest.raises(ValueError):
        f.build_pattern_circular("hole", count=1)


# ---------- combine ----------


def test_build_combine_join():
    src = f.build_combine("plate", ["lip"], operation="join")
    ast.parse(src)
    assert "JoinFeatureOperation" in src
    assert "isKeepToolBodies = False" in src


def test_build_combine_cut_keep_tools():
    src = f.build_combine("plate", ["pocket1", "pocket2"], operation="cut", keep_tools=True)
    assert "CutFeatureOperation" in src
    assert "isKeepToolBodies = True" in src


def test_combine_rejects_empty_tools():
    with pytest.raises(ValueError):
        f.build_combine("plate", [], operation="join")


def test_combine_rejects_unknown_op():
    with pytest.raises(ValueError):
        f.build_combine("plate", ["lip"], operation="merge")


# ---------- run wrappers ----------


class FakeAdapter:
    def __init__(self, message: str = '{"ok": true}'):
        self.scripts: list[str] = []
        self.message = message

    def execute_script(self, s: str) -> Envelope:
        self.scripts.append(s)
        return Envelope(ok=True, message=self.message)


def test_extrude_short_circuits_on_invalid_input():
    a = FakeAdapter()
    env = f.extrude(a, "outer", 0, "morph", "distance", "5 mm")
    assert env.ok is False
    assert env.error == "invalid_input"
    assert a.scripts == []


def test_extrude_passes_through_payload():
    payload = {
        "ok": True,
        "feature_name": "Extrude1",
        "operation": "new_body",
        "extent_kind": "distance",
        "bodies_added": ["Body1"],
    }
    a = FakeAdapter(message=json.dumps(payload))
    env = f.extrude(a, "outer", 0, "new_body", "distance", "thickness")
    assert env.ok is True
    assert env.result["bodies_added"] == ["Body1"]


def test_fillet_surfaces_no_edges_matched_error():
    payload = {"ok": False, "error": "no_edges_matched", "parallel_to": "z", "min_length_mm": None}
    a = FakeAdapter(message=json.dumps(payload))
    env = f.fillet_edges_by_geometry(a, "plate", "5 mm", parallel_to="z")
    assert env.ok is False
    assert env.error == "no_edges_matched"


# ---------- rebuild_feature ----------






















# ---------- rebuild_feature review fixes (multi-profile, preflight) ----------




















# ---------- rebuild_feature duplicate-sketch-name resolution ----------
