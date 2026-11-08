"""Opt-in analytic verification of capability upgrades in a disposable Fusion design.

Run with --run. Only this script's unsaved fixture is closed. Reports omit tokens,
session identifiers, document names and local paths. No benchmark is imported.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from live_regression import Fixture, checked

from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.tools import (
    assembly,
    inspection,
    native_features,
    parameters,
    placement,
    primitives,
    references,
    sketch,
    validation,
    verify,
)


def near(actual, expected, tolerance=1e-5):
    if isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected, strict=True):
            near(a, b, tolerance)
    else:
        assert math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), (actual, expected)


class CapabilityFixture(Fixture):
    def run(self):
        self.start()
        adapter = self.adapter
        carrier = checked(placement.create_component(adapter, "Carrier"))["occurrence"]["path"]
        checked(assembly.unground_component(adapter, carrier))
        checked(placement.place_component(adapter, carrier, [100, 30, 10], [0, 1, 0], [-1, 0, 0]))
        child = checked(placement.create_component(adapter, "Board", carrier))["occurrence"]["path"]
        checked(assembly.unground_component(adapter, child))
        hierarchy = checked(inspection.inspect_assembly(adapter, include=["occurrences"]))["sections"]["occurrences"]
        paths = {row["path"]: row for row in hierarchy["items"]}
        assert paths[carrier]["parent_path"] is None
        assert paths[child]["parent_path"] == carrier
        subtree = checked(inspection.inspect_assembly(adapter, carrier, ["occurrences"]))["sections"]["occurrences"]
        assert [row["path"] for row in subtree["items"]] == [child]
        self.record("occurrence_hierarchy", {"root_count": hierarchy["count"], "subtree_count": subtree["count"],
                                            "child_parent_correct": True})
        self.box("Board Solid", [0, 0], [12, 8], "2 mm", component=child)
        raw = checked(verify.bounding_box(adapter, child + " / Board Solid"))["bodies"][0]["bbox_mm"]
        near(raw["min"], [92, 30, 10])
        near(raw["max"], [100, 42, 12])
        summary = checked(inspection.inspect_assembly(adapter, child, ["bodies", "sketches", "features"]))
        row = summary["sections"]["bodies"]["items"][0]
        near(row["bounding_box_mm"]["min"], [92, 30, 10])
        near(row["bounding_box_mm"]["max"], [100, 42, 12])
        self.record("nested_rotated_inspection", {"bounds_mm": raw, "body_count": summary["sections"]["bodies"]["count"]})

        metadata = {"schema_version": 1, "units": "mm", "reference_id": "synthetic-board",
                    "mounting_points": [{"id": "mount-a", "position_mm": [2, 2, 0], "axis": [0, 0, 1], "hole_diameter_mm": 2}],
                    "connectors": [{"id": "port-a", "origin_mm": [12, 4, 1], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}],
                    "keepouts": [{"id": "service-volume", "min_mm": [12, 2, 0], "max_mm": [18, 6, 3]}]}
        checked(references.set_reference_metadata(adapter, child, metadata))
        placed = checked(placement.place_component(adapter, child, [10, 20, 3], relative_to=carrier))
        assert placed["changed"]
        bounds = checked(verify.bounding_box(adapter, child + " / Board Solid"))["bodies"][0]["bbox_mm"]
        near(bounds["min"], [72, 40, 13])
        near(bounds["max"], [80, 52, 15])
        unchanged = checked(placement.place_component(adapter, child, [10, 20, 3], relative_to=carrier))
        assert unchanged["changed"] is False
        self.record("absolute_nested_relative_placement", {"bounds_mm": bounds, "repeat_changed": unchanged["changed"]})

        top = checked(inspection.query_geometry(adapter, child + " / Board Solid", "face",
                      {"type": "Plane", "normal": [0, 0, 1]}, expected_count=1))
        near(top["items"][0]["sample_point_mm"][2], 15)
        many = inspection.query_geometry(adapter, child + " / Board Solid", "face", {"type": "Plane"}, expected_count=1)
        assert not many.ok and many.error == "count_mismatch"
        feature = checked(inspection.inspect_feature(adapter, child + " / Board Solid"))
        assert feature["health"]["health_state"] == 0 and feature["parameters"]["count"] >= 1
        self.record("semantic_geometry_and_feature_inspection", {"top_face_z_mm": 15, "ambiguous_count": many.result["count"],
                    "feature_parameter_count": feature["parameters"]["count"]})

        inserted = checked(placement.insert_component(adapter, child, "spare", carrier))
        spare = inserted["occurrence"]["path"]
        assert inserted["occurrence"]["shared_definition"]
        duplicate = placement.insert_component(adapter, child, "spare", carrier)
        assert not duplicate.ok and duplicate.error == "operation_id_exists"
        checked(assembly.unground_component(adapter, spare))
        checked(placement.place_component(adapter, spare, [30, 20, 3], relative_to=carrier))
        spare_meta = checked(references.get_reference_metadata(adapter, spare))
        assert spare_meta["metadata"] == metadata
        near(spare_meta["body_bounds"][0]["min_mm"], [72, 60, 13])
        near(spare_meta["body_bounds"][0]["max_mm"], [80, 72, 15])
        refused = references.set_reference_metadata(adapter, spare, metadata)
        assert not refused.ok and refused.error == "shared_component_edit_unsupported"
        self.record("shared_reference_insertion_and_retry_refusal", {"duplicate_error": duplicate.error,
                    "shared_edit_error": refused.error, "metadata_roundtrip": True,
                    "spare_bounds_mm": {"min": [72, 60, 13], "max": [80, 72, 15]}})

        a, b = child + " / Board Solid", spare + " / Board Solid"
        report = checked(validation.validate_design(adapter, [
            {"type": "clearance", "body_a": a, "body_b": b, "min_mm": 7},
            {"type": "clearance", "body_a": a, "body_b": b, "min_mm": 9},
            {"type": "interference", "body_a": a, "body_b": b},
            {"type": "bounds", "body": a, "min_mm": [72, 40, 13], "max_mm": [80, 52, 15], "tolerance_mm": 0.0001},
        ]))
        statuses = [c["status"] for c in report["checks"]]
        assert statuses == ["pass", "violation", "pass", "pass"], report
        near(report["checks"][0]["evidence"]["minimum_distance_mm"], 8)
        self.record("exact_clearance_and_bounded_fit_report", {"minimum_distance_mm": 8, "statuses": statuses})

        # Body wholly contained in another still interferes despite a positive
        # distance between their surfaces; this is not a hollow-cavity check.
        self.box("Outer", [200, 0], [220, 20], "20 mm")
        self.box("Inner", [205, 5], [210, 10], "5 mm")
        report = checked(validation.validate_design(adapter, [
            {"type": "clearance", "body_a": "Outer", "body_b": "Inner", "min_mm": 0},
            {"type": "interference", "body_a": "Outer", "body_b": "Inner"},
        ]))
        assert [c["status"] for c in report["checks"]] == ["violation", "violation"], report
        near(report["checks"][1]["evidence"]["overlap_mm3"], 125)
        self.record("contained_solid_interference", {"overlap_mm3": 125, "clearance_violation": True})
        copied = checked(placement.insert_component(adapter, child, "editable-copy", independent=True))
        copied_path = copied["occurrence"]["path"]
        assert copied["occurrence"]["shared_definition"] is False
        copy_meta = {**metadata, "reference_id": "independent-board"}
        checked(references.set_reference_metadata(adapter, copied_path, copy_meta))
        assert checked(references.get_reference_metadata(adapter, child))["metadata"] == metadata
        assert checked(references.get_reference_metadata(adapter, copied_path))["metadata"] == copy_meta
        copied_handle = copied["occurrence"]["handle"]
        checked(assembly.unground_component(adapter, copied_path))
        checked(placement.place_component(adapter, copied_handle, [150, 80, 10]))
        copy_info = checked(inspection.inspect_assembly(adapter, copied_path, ["bodies"]))
        copied_body = copy_info["sections"]["bodies"]["items"][0]
        near(copied_body["bounding_box_mm"]["min"], [150, 80, 10])
        near(copied_body["bounding_box_mm"]["max"], [162, 88, 12])
        handle_report = checked(validation.validate_design(adapter, [{"type": "bounds",
            "body": copied_body["handle"], "min_mm": [150, 80, 10], "max_mm": [162, 88, 12],
            "tolerance_mm": 0.0001}]))
        assert handle_report["checks"][0]["status"] == "pass"
        self.record("independent_component_copy_and_scoped_handle_roundtrip", {
            "metadata_definition_independent": True, "body_bounds_mm": copied_body["bounding_box_mm"],
            "handle_bounds_status": "pass"})
        self.parametric_primitives()
        self.native_sweep()
        health = checked(verify.audit_feature_health(adapter, include_healthy=True))["summary"]
        assert all(health[k] == 0 for k in ("warning", "failed", "unknown")), health
        self.record("capability_fixture_health", health)
        return {"status": "passed", "fusion": self.info, "checks": self.checks}

    def parametric_primitives(self):
        adapter = self.adapter
        checked(parameters.add_parameters(adapter, [
            {"name": "case_width", "expression": "60 mm", "units": "mm"},
            {"name": "case_depth", "expression": "40 mm", "units": "mm"},
            {"name": "case_height", "expression": "20 mm", "units": "mm"},
            {"name": "case_wall", "expression": "2 mm", "units": "mm"},
            {"name": "case_floor", "expression": "2 mm", "units": "mm"},
            {"name": "boss_x", "expression": "-20 mm", "units": "mm"},
        ]))
        case = checked(placement.create_component(adapter, "Case"))["occurrence"]["path"]
        result = checked(primitives.create_parametric_enclosure(adapter, "Housing", "case_width", "case_depth",
                         "case_height", "case_wall", "case_floor", case))
        near(result["volume_mm3"], 11712)
        near(result["bounds_mm"]["min"], [-30, -20, 0])
        near(result["bounds_mm"]["max"], [30, 20, 20])
        body = result["body"]
        duplicate = primitives.create_parametric_enclosure(adapter, "Housing", "60 mm", "40 mm", "20 mm", "2 mm", "2 mm", case)
        assert not duplicate.ok and duplicate.error == "primitive_name_exists"
        checked(parameters.update_parameter(adapter, "case_width", "70 mm"))
        near(checked(verify.volume(adapter, body))["bodies"][0]["volume_mm3"], 13232)
        bounds = checked(verify.bounding_box(adapter, body))["bodies"][0]["bbox_mm"]
        near(bounds["min"], [-35, -20, 0])
        near(bounds["max"], [35, 20, 20])
        self.record("parametric_enclosure_and_dimension_propagation", {"initial_volume_mm3": 11712,
                    "edited_volume_mm3": 13232, "edited_bounds_mm": bounds, "retry_error": duplicate.error})

        boss = checked(primitives.create_mounting_boss(adapter, "Mount", "boss_x", "-10 mm", "case_floor",
                       "6 mm", "3 mm", "8 mm", case, body))
        near(boss["added_volume_mm3"], 54 * math.pi)
        cut = checked(primitives.create_cutout(adapter, "Port", body, "-4 mm", "-20 mm", "10 mm",
                      "8 mm", "4 mm", "2 mm", "xz", "positive", case))
        near(cut["removed_volume_mm3"], 64)
        near(cut["cut_direction_component"], [0, 1, 0])
        near(cut["profile_corners_component_mm"], [[-4, -20, 10], [4, -20, 10],
                                                 [-4, -20, 6], [4, -20, 6]])
        near(checked(verify.volume(adapter, body))["bodies"][0]["volume_mm3"], 13232 + 54*math.pi - 64)
        checked(parameters.update_parameter(adapter, "boss_x", "-18 mm"))
        cylinders = checked(inspection.query_geometry(adapter, body, "face", {"type": "Cylinder", "radius": 3}, expected_count=1))
        sample = cylinders["items"][0]["sample_point_mm"]
        near((sample[0] + 18)**2 + (sample[1] + 10)**2, 9)
        self.record("annular_boss_side_cutout_and_signed_anchor", {"boss_added_mm3": 54*math.pi,
                    "port_removed_mm3": 64, "edited_boss_center_mm": [-18, -10]})

        before = self.script("""
            design=adsk.fusion.Design.cast(app.activeProduct)
            print(json.dumps({'timeline':design.timeline.count, 'bodies':sum(c.bRepBodies.count for c in design.allComponents)}))
        """)
        missed = primitives.create_cutout(adapter, "Missed", body, "500 mm", "500 mm", "2 mm",
                                          "4 mm", "4 mm", "2 mm", "xy", "negative", case)
        assert not missed.ok and missed.result["rollback"]["status"] == "verified", missed.result
        after = self.script("""
            design=adsk.fusion.Design.cast(app.activeProduct)
            print(json.dumps({'timeline':design.timeline.count, 'bodies':sum(c.bRepBodies.count for c in design.allComponents)}))
        """)
        assert after == before, (before, after)
        near(checked(verify.volume(adapter, body))["bodies"][0]["volume_mm3"], 13232 + 54*math.pi - 64)
        self.record("missed_cut_targeted_recovery", {"rollback": missed.result["rollback"]["status"],
                    "timeline_restored": True, "body_inventory_restored": True})

        second = checked(placement.create_component(adapter, "SecondCase"))["occurrence"]["path"]
        variant = checked(primitives.create_parametric_enclosure(adapter, "Variant", "80 mm", "50 mm", "25 mm", "3 mm", "2 mm", second))
        near(variant["volume_mm3"], 25112)
        self.record("independent_enclosure_variant", {"volume_mm3": 25112, "bounds_mm": variant["bounds_mm"]})
        # Exercise opposite signs and a different native origin-plane frame.
        # The two profiles are on separate walls, so their removed volumes add.
        back = checked(primitives.create_cutout(adapter, "Back", variant["body"], "-4 mm", "25 mm", "12 mm",
                       "8 mm", "4 mm", "3 mm", "xz", "negative", second))
        near(back["cut_direction_component"], [0, -1, 0])
        near(back["profile_corners_component_mm"], [[-4, 25, 12], [4, 25, 12],
                                                   [-4, 25, 8], [4, 25, 8]])
        near(back["removed_volume_mm3"], 96)
        right = checked(primitives.create_cutout(adapter, "Right", variant["body"], "40 mm", "-4 mm", "12 mm",
                        "4 mm", "8 mm", "3 mm", "yz", "negative", second))
        near(right["cut_direction_component"], [-1, 0, 0])
        near(right["profile_corners_component_mm"], [[40, -4, 12], [40, -4, 8],
                                                    [40, 4, 12], [40, 4, 8]])
        near(right["removed_volume_mm3"], 96)
        near(checked(verify.volume(adapter, variant["body"]))["bodies"][0]["volume_mm3"], 25112-192)
        self.record("opposite_direction_and_yz_side_cutouts", {"removed_mm3": [96, 96],
                    "directions": [[0, -1, 0], [-1, 0, 0]],
                    "yz_corners_component_mm": right["profile_corners_component_mm"]})

    def native_sweep(self):
        adapter = self.adapter
        cable = checked(placement.create_component(adapter, "CableEnvelope"))["occurrence"]["path"]
        checked(parameters.add_parameters(adapter, [{"name": "cable_radius", "expression": "2 mm", "units": "mm"}]))
        checked(sketch.create_sketch(adapter, "xy", "Cable Profile", cable))
        profile = cable + " / Cable Profile"
        checked(sketch.add_circle(adapter, profile, "center_radius", center=[0, 0], radius_mm=2))
        checked(sketch.add_geometric_constraint(adapter, profile, "coincident", ["circle:0:center", "origin"]))
        checked(sketch.add_dimension(adapter, profile, "diameter", ["circle:0"], "2 * cable_radius", [5, 5]))
        checked(sketch.create_sketch(adapter, "xz", "Cable Path", cable))
        path = cable + " / Cable Path"
        checked(sketch.add_line(adapter, path, [0, 0], [0, -20]))
        result = checked(native_features.sweep(adapter, profile, path, ["line:0"], name="Cable Sweep"))
        assert len(result["bodies"]) == 1
        near(result["bodies"][0]["volume_mm3"], 80 * math.pi)
        near(result["bodies"][0]["bounds_mm"]["min"], [-2, -2, 0])
        near(result["bodies"][0]["bounds_mm"]["max"], [2, 2, 20])
        measured = checked(validation.validate_design(adapter, [{"type": "bounds",
            "body": result["bodies"][0]["handle"], "min_mm": [-2, -2, 0], "max_mm": [2, 2, 20],
            "tolerance_mm": 0.0001}]))
        assert measured["checks"][0]["status"] == "pass"
        checked(parameters.update_parameter(adapter, "cable_radius", "3 mm"))
        native_body = cable + " / " + result["bodies"][0]["name"]
        near(checked(verify.volume(adapter, native_body))["bodies"][0]["volume_mm3"], 180 * math.pi)
        resized_bounds = checked(verify.bounding_box(adapter, native_body))["bodies"][0]["bbox_mm"]
        near(resized_bounds["min"], [-3, -3, 0])
        near(resized_bounds["max"], [3, 3, 20])
        duplicate = native_features.sweep(adapter, profile, path, ["line:0"], name="Cable Sweep")
        assert not duplicate.ok and duplicate.error == "feature_name_taken"
        self.record("native_child_sweep_edit_and_handle_roundtrip", {"initial_volume_mm3": 80*math.pi,
                    "edited_volume_mm3": 180*math.pi, "retry_error": duplicate.error})


def main():
    if not __debug__:
        raise RuntimeError("Live validation cannot run with Python optimization enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="create and close a disposable Fusion fixture")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required")
    report = {"status": "failed"}
    with FusionAdapter() as adapter:
        fixture = CapabilityFixture(adapter)
        try:
            report = fixture.run()
        finally:
            report.setdefault("checks", fixture.checks)
            try:
                fixture.close()
                report["cleanup"] = "fixture_closed"
            except Exception:
                report["status"] = "failed"
                report["cleanup"] = "unverified"
                raise
            finally:
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": len(fixture.checks), "cleanup": report["cleanup"]}))


if __name__ == "__main__":
    main()
