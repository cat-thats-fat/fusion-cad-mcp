"""Opt-in regression checks against a running local Autodesk Fusion.

Creates its own unsaved document. Never opens, saves, or closes a user document.
Run with --run and optionally --report PATH. Reports contain no entity tokens,
user paths, project names, or account details. Each assertion uses real Fusion
geometry; unit tests remain necessary for unavailable API versions and faults.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import struct
import tempfile
import textwrap
import uuid
from pathlib import Path

from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.envelope import parse_stdout_json
from fusion_cad_mcp.tools import (
    assembly,
    features,
    handle_tools,
    parameters,
    sketch,
    verify,
    visualize,
)
from fusion_cad_mcp.tools import io as io_tools


def checked(env):
    if not env.ok:
        # Do not copy arbitrary exception messages (which can contain paths).
        code = (
            env.error
            if env.error and "\n" not in env.error and len(env.error) < 100
            else "fusion_script_error"
        )
        raise AssertionError(f"Tool failed: {code}")
    return env.result or {}


class _FixtureAdapter:
    """Guard typed scripts in the same Fusion call that performs their work."""

    def __init__(self, fixture):
        self.fixture = fixture

    def __getattr__(self, name):
        if name not in {"read_query", "list_tools"}:
            raise AttributeError(f"Fixture adapter does not expose {name}")
        return getattr(self.fixture.transport, name)

    def execute_script(self, script, **kwargs):
        expected = self.fixture.creation_id
        if expected is None:
            raise RuntimeError("Cannot execute a typed test without a confirmed fixture")
        tree = ast.parse(script)
        entries = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
        if len(entries) != 1:
            raise RuntimeError("Expected one generated run entry point")
        entry = entries[0]
        # Inserting at the first statement supports the generators' normal
        # multiline entry points without changing their code or stdout contract.
        first = entry.body[0]
        if first.lineno == entry.lineno:
            raise RuntimeError("Fixture guard requires a multiline run entry point")
        lines = script.splitlines(keepends=True)
        indent = " " * first.col_offset
        guard = (
            indent + "_fixture_doc = __import__('adsk.core', fromlist=['Application']).Application.get().activeDocument\n"
            + indent + f"if (_fixture_doc is None or _fixture_doc.creationId != {expected!r}\n"
            + indent + f"        or _fixture_doc.name != {self.fixture.name!r} or _fixture_doc.isSaved):\n"
            + indent + "    raise RuntimeError('Active document is not the test fixture')\n"
        )
        lines.insert(first.lineno - 1, guard)
        return self.fixture.transport.execute_script("".join(lines), **kwargs)


class Fixture:
    def __init__(self, adapter):
        self.transport = adapter
        self.adapter = _FixtureAdapter(self)
        self.name = "MCP Live Regression " + uuid.uuid4().hex[:8]
        self.creation_id = None
        self.previous_id = None
        self.start_attempted = False
        self.checks = []

    def script(self, body, *, guard=True):
        prefix = "import adsk.core, adsk.fusion, json, math\n\ndef run(_ctx):\n    app=adsk.core.Application.get()\n"
        prefix += "    if not __debug__: raise RuntimeError('Live validation requires assertions enabled in Fusion')\n"
        if guard:
            prefix += f"    if not app.activeDocument or app.activeDocument.creationId != {self.creation_id!r}: raise RuntimeError('fixture is not active')\n"
            prefix += f"    if app.activeDocument.name != {self.name!r} or app.activeDocument.isSaved: raise RuntimeError('fixture changed ownership')\n"
        env = self.transport.execute_script(
            prefix + textwrap.indent(textwrap.dedent(body).strip(), "    ")
        )
        checked(env)
        value = parse_stdout_json(env)
        if value is None:
            raise AssertionError("Live fixture script returned no JSON")
        return value

    def record(self, name, evidence):
        self.checks.append({"name": name, "status": "passed", "evidence": evidence})
        print(json.dumps(self.checks[-1]), flush=True)

    def blocked(self, name, reason):
        self.checks.append({"name": name, "status": "blocked", "reason": reason})
        print(json.dumps(self.checks[-1]), flush=True)

    def start(self):
        self.start_attempted = True
        info = self.script(
            f"""
            previous=app.activeDocument.creationId if app.activeDocument else None
            doc=app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            doc.name={self.name!r}
            print(json.dumps({{'creation_id':doc.creationId, 'previous_id':previous, 'version':app.version,
                              'rib_scriptable':hasattr(adsk.fusion.RibFeatures,'createInput')}}))
        """,
            guard=False,
        )
        self.creation_id = info.pop("creation_id")
        self.previous_id = info.pop("previous_id")
        self.info = info

    def close(self):
        if self.creation_id is None:
            if self.start_attempted:
                raise RuntimeError("Fixture creation outcome is unknown; cleanup cannot be verified")
            return
        result = self.script(
            f"""
            docs=[app.documents.item(i) for i in range(app.documents.count)]
            matches=[d for d in docs if d.creationId=={self.creation_id!r}]
            if len(matches)!=1 or matches[0].name!={self.name!r}:
                raise RuntimeError('fixture identity not confirmed')
            if matches[0].isSaved:
                raise RuntimeError('fixture was saved; refusing to discard it')
            previous=[d for d in docs if d.creationId=={self.previous_id!r} and d not in matches]
            if not matches[0].close(False):
                raise RuntimeError('Fusion refused to close fixture')
            if len(previous)==1:
                previous[0].activate()
            print(json.dumps({{'closed_fixture':True}}))
        """,
            guard=False,
        )
        if result.get("closed_fixture") is not True:
            raise RuntimeError("Fixture close acknowledgement was missing")

    def box(self, label, p1, p2, depth, *, direction="positive", component=None):
        checked(sketch.create_sketch(self.adapter, "xy", label + " Sketch", component))
        selector = f"{component} / {label} Sketch" if component else label + " Sketch"
        checked(sketch.add_rectangle(self.adapter, selector, "corner", p1=p1, p2=p2))
        checked(
            features.extrude(
                self.adapter, selector, expression=depth, direction=direction, name=label
            )
        )
        self.script(f"""
            design=adsk.fusion.Design.cast(app.activeProduct)
            matches=[f for c in design.allComponents for f in c.features if f.name=={label!r}]
            assert len(matches)==1
            matches[0].bodies.item(0).name={label!r}
            print(json.dumps({{'renamed':True}}))
        """)

    def run(self):
        self.start()
        self.box("Negative", [0, 50], [10, 60], "5 mm", direction="negative")
        bounds = checked(verify.bounding_box(self.adapter, "Negative"))["bodies"][0]["bbox_mm"]
        assert bounds == {"min": [0.0, 50.0, -5.0], "max": [10.0, 60.0, 0.0]}, bounds
        self.record("negative_extrusion", bounds)
        self.box("Case", [0, 0], [40, 40], "1 mm", direction="negative")
        self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            occ=root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
            occ.component.name='Board'
            occ.isGrounded=False
            occ.isGroundToParent=False
            print(json.dumps({'path':occ.fullPathName}))
        """)
        self.box("PCB", [0, 0], [10, 10], "1 mm", component="Board:1")
        moved = checked(
            assembly.move_component(self.adapter, "Board:1", translation_mm=[100, 0, 0])
        )
        bounds = checked(verify.bounding_box(self.adapter, "Board:1 / PCB"))["bodies"][0]["bbox_mm"]
        assert bounds == {"min": [100.0, 0.0, 0.0], "max": [110.0, 10.0, 1.0]}, bounds
        center = checked(verify.center_of_mass(self.adapter, "Board:1 / PCB"))["bodies"][0][
            "center_of_mass_mm"
        ]
        assert center == [105.0, 5.0, 0.5], center
        self.record(
            "component_authoring_and_world_measurements",
            {"bbox_mm": bounds, "center_mm": center, "moved": moved.get("moved")},
        )
        self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            board=root.occurrences.itemByName('Board:1').component
            t=adsk.core.Matrix3D.create()
            t.translation=adsk.core.Vector3D.create(12,0,0)
            root.occurrences.addExistingComponent(board,t)
            print(json.dumps({'added':True}))
        """)
        ambiguous = verify.bounding_box(self.adapter, "PCB")
        assert not ambiguous.ok and ambiguous.error == "body_ambiguous", ambiguous.error
        shared = sketch.create_sketch(self.adapter, "xy", "Must Not Create", "Board:1")
        assert not shared.ok and shared.error == "shared_component_edit_unsupported", shared.error
        self.record(
            "repeated_instance_ambiguity_and_shared_edit_refusal",
            {"body_error": ambiguous.error, "edit_error": shared.error},
        )
        a = checked(
            handle_tools.list_body_entities(
                self.adapter, "Board:1 / PCB", ["face"], face_normal_filter=[1, 0, 0]
            )
        )
        b = checked(
            handle_tools.list_body_entities(
                self.adapter, "Board:2 / PCB", ["face"], face_normal_filter=[-1, 0, 0]
            )
        )
        distance = checked(
            handle_tools.measure(self.adapter, a["faces"][0]["handle"], b["faces"][0]["handle"])
        )
        assert math.isclose(distance["value_mm"], 10, abs_tol=1e-5), distance
        self.record("proxy_handle_distance", {"distance_mm": distance["value_mm"]})
        self.document_scope(a["faces"][0]["handle"], b["faces"][0]["handle"])
        self.extended()
        health = checked(verify.audit_feature_health(self.adapter, include_healthy=True))
        assert health["summary"]["failed"] == 0 and health["summary"]["warning"] == 0, health[
            "summary"
        ]
        self.record("feature_health", health["summary"])
        image = visualize.screenshot(self.adapter, width=640, height=480)
        checked(image)
        assert (
            image.image
            and image.image["mime_type"] == "image/png"
            and len(image.image["data"]) > 100
        )
        self.record("viewport_capture", {"native_image_available": True})
        return {"fusion": self.info, "checks": self.checks}

    def extended(self):
        checked(
            parameters.add_parameters(
                self.adapter,
                [
                    {
                        "name": "fixture_depth",
                        "expression": "2 mm",
                        "units": "mm",
                        "comment": None,
                        "extra": False,
                    }
                ],
            )
        )
        self.box("Parameter Block", [30, 50], [40, 60], "fixture_depth")
        checked(parameters.update_parameter(self.adapter, "fixture_depth", "3 mm"))
        param_volume = checked(verify.volume(self.adapter, "Parameter Block"))["bodies"][0][
            "volume_mm3"
        ]
        assert param_volume == 300
        self.record("nullable_parameter_and_dependency_recompute", {"volume_mm3": param_volume})
        self.box("Protected", [0, 0], [40, 40], "1 mm", direction="negative")
        checked(features.move_body(self.adapter, "Protected", translation_mm=[0, 0, -4]))
        pre = checked(verify.volume(self.adapter, "Protected"))["bodies"][0]["volume_mm3"]
        checked(features.add_hole(self.adapter, "Case", [20, 20, 0], "4 mm", name="Mounting Hole"))
        post = checked(verify.volume(self.adapter, "Protected"))["bodies"][0]["volume_mm3"]
        case = checked(verify.volume(self.adapter, "Case"))["bodies"][0]["volume_mm3"]
        assert pre == post == 1600
        assert math.isclose(case, 1600 - 4 * math.pi, abs_tol=0.002), case
        self.record(
            "hole_participant_scope", {"case_volume_mm3": case, "protected_volume_mm3": post}
        )
        self.box("Side Hole Block", [60, 0], [70, 10], "10 mm")
        face = checked(
            handle_tools.list_body_entities(
                self.adapter, "Side Hole Block", ["face"], face_normal_filter=[1, 0, 0]
            )
        )["faces"][0]["handle"]
        checked(
            features.add_hole(self.adapter, "Side Hole Block", [70, 5, 5], "2 mm", face_handle=face)
        )
        side = checked(verify.volume(self.adapter, "Side Hole Block"))["bodies"][0]["volume_mm3"]
        assert math.isclose(side, 1000 - 10 * math.pi, abs_tol=0.002), side
        self.record("explicit_side_face_hole", {"volume_mm3": side})
        checked(assembly.unground_component(self.adapter, "Board:2"))
        none = checked(
            assembly.interference_check(self.adapter, ["Board:1 / PCB", "Board:2 / PCB"])
        )
        assert none["pair_count"] == 0
        checked(assembly.move_component(self.adapter, "Board:2", translation_mm=[-15, 0, 0]))
        overlap = checked(
            assembly.interference_check(self.adapter, ["Board:1 / PCB", "Board:2 / PCB"])
        )
        assert overlap["pair_count"] == 1, overlap
        assert math.isclose(
            overlap["interference_pairs"][0]["interference_volume_mm3"], 50, abs_tol=0.002
        ), overlap
        checked(assembly.move_component(self.adapter, "Board:2", translation_mm=[15, 0, 0]))
        rotated = checked(
            assembly.move_component(
                self.adapter,
                "Board:2",
                rotation_axis=[0, 0, 1],
                rotation_angle_deg=90,
                rotation_origin_mm=[125, 5, 0],
            )
        )
        assert rotated["moved"] is True
        checked(
            assembly.move_component(
                self.adapter,
                "Board:2",
                rotation_axis=[0, 0, 1],
                rotation_angle_deg=-90,
                rotation_origin_mm=[125, 5, 0],
            )
        )
        before_pin = checked(
            assembly.move_component(self.adapter, "Board:2", translation_mm=[2, 0, 0])
        )["after_transform"]
        bounds_before = checked(verify.bounding_box(self.adapter, "Board:2 / PCB"))["bodies"][0][
            "bbox_mm"
        ]
        assert bounds_before == {"min": [122.0, 0.0, 0.0], "max": [132.0, 10.0, 1.0]}
        pinned = checked(assembly.ground_component(self.adapter, "Board:2"))
        assert pinned["isGrounded"] is True and pinned["isGroundToParent"] is False
        after_pin = self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            occ=root.occurrences.itemByName('Board:2')
            print(json.dumps({'transform':list(occ.transform2.asArray())}))
        """)["transform"]
        bounds_after = checked(verify.bounding_box(self.adapter, "Board:2 / PCB"))["bodies"][0][
            "bbox_mm"
        ]
        assert bounds_before == bounds_after
        assert all(
            math.isclose(a, b, abs_tol=1e-7) for a, b in zip(before_pin, after_pin, strict=True)
        )
        refused = assembly.move_component(self.adapter, "Board:2", translation_mm=[1, 0, 0])
        assert not refused.ok and refused.error == "occurrence_grounded"
        checked(assembly.unground_component(self.adapter, "Board:2"))
        reset = checked(assembly.move_component(self.adapter, "Board:2", translation_mm=[-2, 0, 0]))
        assert reset["after_translation_mm"] == [120.0, 0.0, 0.0], reset
        self.record(
            "interference_rotation_and_grounding",
            {
                "overlap_volume_mm3": 50,
                "rotation_detected": True,
                "grounded_move_refused": True,
                "pinned_bounds_mm": bounds_after,
            },
        )
        self.script("""
            design=adsk.fusion.Design.cast(app.activeProduct)
            occ=design.rootComponent.occurrences.itemByName('Board:2')
            t=occ.transform2
            t.translation=adsk.core.Vector3D.create(12.1,0,0)
            occ.transform2=t
            print(json.dumps({'pending':design.snapshots.hasPendingSnapshot}))
        """)
        pending = assembly.move_component(self.adapter, "Board:1", translation_mm=[1, 0, 0])
        assert not pending.ok and pending.error == "pending_assembly_transforms"
        restored = self.script("""
            design=adsk.fusion.Design.cast(app.activeProduct)
            assert design.snapshots.revertPendingSnapshot()
            root=design.rootComponent
            print(json.dumps({'first_x':root.occurrences.itemByName('Board:1').transform2.translation.x*10,
                              'second_x':root.occurrences.itemByName('Board:2').transform2.translation.x*10,
                              'pending':design.snapshots.hasPendingSnapshot}))
        """)
        assert restored == {"first_x": 100.0, "second_x": 120.0, "pending": False}, restored
        self.record("pending_transform_refusal", restored)
        nested = self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            t=adsk.core.Matrix3D.create()
            t.translation=adsk.core.Vector3D.create(20,3,0)
            parent=root.occurrences.addNewComponent(t)
            parent.component.name='Carrier'
            child_t=adsk.core.Matrix3D.create()
            child_t.setToRotation(math.pi/2,adsk.core.Vector3D.create(0,0,1),adsk.core.Point3D.create(0,0,0))
            child_t.translation=adsk.core.Vector3D.create(1,0,2)
            child=parent.component.occurrences.addNewComponent(child_t)
            child.component.name='Sensor'
            print(json.dumps({'path':'Carrier:1+Sensor:1'}))
        """)["path"]
        self.box("Sensor PCB", [0, 0], [10, 20], "2 mm", component=nested)
        bounds = checked(verify.bounding_box(self.adapter, nested + " / Sensor PCB"))["bodies"][0][
            "bbox_mm"
        ]
        assert bounds == {"min": [190.0, 30.0, 20.0], "max": [210.0, 40.0, 22.0]}, bounds
        self.record("nested_rotated_component_authoring", bounds)
        self.authoring_checks()
        self.joint_check()
        no_rebuild = features.rebuild_feature(self.adapter, "Negative")
        assert not no_rebuild.ok and no_rebuild.error == "safe_rebuild_unavailable"
        assert checked(verify.volume(self.adapter, "Negative"))["bodies"][0]["volume_mm3"] == 500
        self.record("unsafe_rebuild_refused", {"original_volume_mm3": 500})
        if not self.info["rib_scriptable"]:
            result = features.rib(self.adapter, "Negative Sketch", "1 mm", target_body="Negative")
            assert not result.ok and result.error == "rib_not_scriptable"
            self.record("old_build_rib_capability_refusal", {"error": result.error})
        else:
            self.rib_check()
        self.io_checks()
        checked(visualize.set_view(self.adapter, "top"))
        camera = self.script("""
            cam=app.activeViewport.camera
            direction=cam.target.vectorTo(cam.eye)
            direction.normalize()
            print(json.dumps({'direction':direction.asArray(),'up':cam.upVector.asArray()}))
        """)
        # Fusion returns ArbitraryViewOrientation after fit; verify the actual basis.
        assert all(
            math.isclose(a, b, abs_tol=1e-6)
            for a, b in zip(camera["direction"], [0, 0, 1], strict=True)
        ), camera
        # ViewCube can preserve a user's roll; top requires a +Z view normal
        # with a unit screen-up vector in the XY plane, not a fixed roll.
        assert abs(camera["up"][2]) < 1e-6 and math.isclose(
            sum(v * v for v in camera["up"]), 1, abs_tol=1e-6
        ), camera
        marker = self.script(
            "print(json.dumps({'position':adsk.fusion.Design.cast(app.activeProduct).timeline.markerPosition}))"
        )["position"]
        comparison = checked(
            visualize.screenshot_compare_with_marker(self.adapter, 2, width=320, height=240)
        )
        assert comparison["after_marker"] == marker

        class FailingCapture:
            def execute_script(inner, script):
                return self.adapter.execute_script(script)

            def read_query(inner, *args, **kwargs):
                raise TimeoutError("injected screenshot timeout")

        failed = visualize.screenshot_compare_with_marker(
            FailingCapture(), 2, width=320, height=240
        )
        assert not failed.ok
        actual = self.script(
            "print(json.dumps({'position':adsk.fusion.Design.cast(app.activeProduct).timeline.markerPosition}))"
        )["position"]
        assert actual == marker
        self.record(
            "camera_and_timeline_restoration",
            {"top_camera_verified": True, "marker_restored_after_timeout": True},
        )

    def authoring_checks(self):
        plane = self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            print(json.dumps({'handle':'plane:root XY:'+root.xYConstructionPlane.entityToken}))
        """)["handle"]
        checked(sketch.create_sketch(self.adapter, plane, "Handle Plane Sketch"))
        checked(sketch.add_line(self.adapter, "Handle Plane Sketch", [0, 0], [10, 0]))
        checked(sketch.add_line(self.adapter, "Handle Plane Sketch", [0, 0], [5, 5]))
        checked(
            sketch.add_geometric_constraint(
                self.adapter, "Handle Plane Sketch", "perpendicular", ["line:0", "line:1"]
            )
        )
        directions = self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            lines=root.sketches.itemByName('Handle Plane Sketch').sketchCurves.sketchLines
            vectors=[l.startSketchPoint.geometry.vectorTo(l.endSketchPoint.geometry) for l in lines]
            for v in vectors: v.normalize()
            print(json.dumps({'dot':vectors[0].dotProduct(vectors[1])}))
        """)
        assert abs(directions["dot"]) < 1e-7, directions
        checked(sketch.create_sketch(self.adapter, "xy", "Projection Sketch"))
        edge = checked(
            handle_tools.list_body_entities(
                self.adapter, "Board:1 / PCB", ["edge"], edge_parallel_to="x"
            )
        )["edges"][0]["handle"]
        checked(handle_tools.project_to_sketch(self.adapter, "Projection Sketch", [edge]))
        geometry = self.script("""
            sk=adsk.fusion.Design.cast(app.activeProduct).rootComponent.sketches.itemByName('Projection Sketch')
            lines=sk.sketchCurves.sketchLines
            assert lines.count==1
            line=lines.item(0)
            print(json.dumps({'min_x_mm':min(line.startSketchPoint.worldGeometry.x,line.endSketchPoint.worldGeometry.x)*10,
                              'max_x_mm':max(line.startSketchPoint.worldGeometry.x,line.endSketchPoint.worldGeometry.x)*10,
                              'linked':line.isReference}))
        """)
        assert geometry == {"min_x_mm": 100.0, "max_x_mm": 110.0, "linked": True}, geometry
        self.record(
            "plane_handle_perpendicular_and_proxy_projection",
            {"perpendicular_dot": directions["dot"], "projection": geometry},
        )

    def rib_check(self):
        self.box("Rib Base", [80, -10], [100, 10], "2 mm")
        checked(sketch.create_sketch(self.adapter, "xy", "Rib Wall"))
        checked(sketch.add_rectangle(self.adapter, "Rib Wall", "corner", [80, -10], [82, 10]))
        checked(
            features.extrude(
                self.adapter,
                "Rib Wall",
                operation="join",
                expression="20 mm",
                participants=["Rib Base"],
            )
        )
        checked(sketch.create_sketch(self.adapter, "xz", "Rib Profile"))
        # Endpoints lie on the inner wall and base. Local sketch Y is world -Z;
        # The flipped direction was verified to fill the triangular brace region.
        checked(sketch.add_line(self.adapter, "Rib Profile", [92, -2], [82, -10]))
        checked(
            features.rib(
                self.adapter,
                "Rib Profile",
                "2 mm",
                target_body="Rib Base",
                name="Verified Rib",
                is_flipped=True,
            )
        )
        actual = checked(verify.volume(self.adapter, "Rib Base"))["bodies"][0]["volume_mm3"]
        # L bracket: 800 + 720 mm³. Rib: (10*8/2)*2 = 80 mm³.
        assert math.isclose(actual, 1600, abs_tol=0.002), actual
        self.record("modern_rib_creation", {"base_plus_rib_volume_mm3": actual})

    def joint_check(self):
        handles = self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            handles=[]
            for name,z in [('Joint Fixed',0),('Joint Moving',4)]:
                t=adsk.core.Matrix3D.create()
                t.translation=adsk.core.Vector3D.create(0,0,z)
                occ=root.occurrences.addNewComponent(t)
                occ.component.name=name
                occ.isGroundToParent=False
                occ.isGrounded=(z==0)
                sk=occ.component.sketches.add(occ.component.xYConstructionPlane)
                point=sk.originPoint.createForAssemblyContext(occ)
                handles.append('point:joint origin|occ='+occ.fullPathName+'|doc='+app.activeDocument.creationId.encode().hex()+':'+point.entityToken)
            print(json.dumps({'handles':handles}))
        """)["handles"]
        checked(
            assembly.create_joint(
                self.adapter, handles[1], handles[0], motion_type="revolute", name="Joint Fixture"
            )
        )
        checked(
            assembly.set_joint_limits(
                self.adapter, "Joint Fixture", min_value="0 deg", max_value="90 deg"
            )
        )
        invalid = assembly.set_joint_limits(
            self.adapter,
            "Joint Fixture",
            min_value="10 deg",
            max_value="missing_parameter_for_test",
        )
        assert not invalid.ok and invalid.error == "expression_invalid"
        state = self.script("""
            root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
            joint=root.joints.itemByName('Joint Fixture')
            lim=joint.jointMotion.rotationLimits
            print(json.dumps({'count':root.joints.count,'healthy':joint.healthState==adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState,
                'min':lim.minimumValue,'max':lim.maximumValue,
                'moving_origin_mm':[v*10 for v in root.occurrences.itemByName('Joint Moving:1').transform2.translation.asArray()]}))
        """)
        assert state["count"] == 1 and state["healthy"] and state["min"] == 0
        assert math.isclose(state["max"], math.pi / 2, abs_tol=1e-9)
        assert all(abs(v) < 1e-6 for v in state["moving_origin_mm"]), state
        self.record(
            "point_joint_and_limit_prevalidation",
            {"min_deg": 0, "max_deg": 90, "invalid_batch_preserved_limits": True},
        )

    def document_scope(self, handle_a, handle_b):
        # A separate owned fixture lets the same-call guard protect this read
        # while deliberately testing handles from another document.
        second = Fixture(self.transport)
        try:
            second.start()
            result = handle_tools.measure(second.adapter, handle_a, handle_b)
            assert not result.ok and result.error == "handle_invalid"
        finally:
            second.close()
        self.record("cross_document_handle_refusal", {"error": "handle_invalid"})

    def io_checks(self):
        with tempfile.TemporaryDirectory(
            prefix="fusion-live-", dir=Path(__file__).resolve().parent.parent.parent
        ) as folder:
            folder = Path(folder)
            available = []
            for fmt in ("step", "iges", "sat", "smt", "f3d", "stl", "obj", "3mf"):
                path = folder / ("board." + fmt)
                env = io_tools.export(self.adapter, fmt, str(path), component="Board:1")
                if not env.ok and "user is restricted" in str(env.error):
                    self.blocked(
                        "component_export_" + fmt,
                        "Installed Fusion capability restricts this format",
                    )
                    continue
                checked(env)
                assert path.is_file() and path.stat().st_size > 0
                available.append(fmt)
                self.record("component_export_" + fmt, {"bytes": path.stat().st_size})
            data = (folder / "board.stl").read_bytes()
            n = struct.unpack_from("<I", data, 80)[0]
            assert len(data) == 84 + n * 50
            vertices = [
                struct.unpack_from("<fff", data, 84 + i * 50 + 12 + j * 12)
                for i in range(n)
                for j in range(3)
            ]
            extents = [
                max(v[axis] for v in vertices) - min(v[axis] for v in vertices) for axis in range(3)
            ]
            assert all(
                math.isclose(a, b, abs_tol=0.002) for a, b in zip(extents, [10, 10, 1], strict=True)
            ), extents
            self.record("stl_millimeter_scale", {"extent_mm": extents})
            self.script(f"""
                root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
                meshes=root.meshBodies.add({str(folder / "board.stl")!r},adsk.fusion.MeshUnits.MillimeterMeshUnit)
                assert meshes and meshes.count==1
                meshes.item(0).name='Ray Test Mesh'
                print(json.dumps({{'imported_mesh':True}}))
            """)
            # A non-diagonal ray avoids shared triangle seams in the box mesh.
            hits = checked(handle_tools.find_mesh_using_ray(self.adapter, [3, 4, 10], [0, 0, -1]))
            assert hits["meshes_found"] == 1 and hits["names"] == ["Ray Test Mesh"]
            points = checked(
                handle_tools.ray_collision_with_mesh(
                    self.adapter, hits["mesh_handles"][0], [3, 4, 10], [0, 0, -1]
                )
            )
            assert points["collisions"] == 2, points
            assert sorted(round(p[2], 5) for p in points["points_mm"]) == [0, 1], points
            self.record("mesh_handle_ray_roundtrip", {"hit_z_mm": [0, 1]})
            self.script(f"""
                root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
                t=adsk.core.Matrix3D.create()
                t.translation=adsk.core.Vector3D.create(30,0,2)
                one=root.occurrences.addNewComponent(t)
                one.component.name='Mesh Board'
                meshes=one.component.meshBodies.add({str(folder / "board.stl")!r},adsk.fusion.MeshUnits.MillimeterMeshUnit)
                assert meshes and meshes.count==1
                meshes.item(0).name='Scoped Mesh'
                t.translation=adsk.core.Vector3D.create(32,0,2)
                root.occurrences.addExistingComponent(one.component,t)
                print(json.dumps({{'created':True}}))
            """)
            scoped = checked(
                handle_tools.find_mesh_using_ray(
                    self.adapter, [303, 4, 30], [0, 0, -1], "Mesh Board:1"
                )
            )
            excluded = checked(
                handle_tools.find_mesh_using_ray(
                    self.adapter, [303, 4, 30], [0, 0, -1], "Mesh Board:2"
                )
            )
            ambiguous = handle_tools.find_mesh_using_ray(
                self.adapter, [303, 4, 30], [0, 0, -1], "Mesh Board"
            )
            assert scoped["meshes_found"] == 1 and excluded["meshes_found"] == 0
            assert not ambiguous.ok and ambiguous.error == "component_ambiguous"
            collision = checked(
                handle_tools.ray_collision_with_mesh(
                    self.adapter, scoped["mesh_handles"][0], [303, 4, 30], [0, 0, -1]
                )
            )
            assert sorted(round(p[2], 4) for p in collision["points_mm"]) == [20, 21], collision
            self.record(
                "scoped_repeated_mesh_world_rays",
                {"hit_z_mm": [20, 21], "other_instance_excluded": True},
            )
            self.script("""
                root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
                t=adsk.core.Matrix3D.create()
                t.translation=adsk.core.Vector3D.create(0,10,0)
                occ=root.occurrences.addNewComponent(t)
                occ.component.name='Import Target'
                print(json.dumps({'created':True}))
            """)
            imported_count = 0
            for fmt in ("step", "iges", "sat", "smt", "f3d"):
                if fmt not in available:
                    self.blocked(
                        "scoped_import_" + fmt, "No exported fixture available on this installation"
                    )
                    continue
                path = folder / ("board." + fmt)
                result = checked(
                    io_tools.import_geometry(self.adapter, fmt, str(path), "Import Target:1")
                )
                assert result["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
                assert (
                    result["imported_objects"] and result["coordinate_frame"] == "target_component"
                )
                evidence = self.script("""
                    root=adsk.fusion.Design.cast(app.activeProduct).rootComponent
                    target=root.occurrences.itemByName('Import Target:1').component
                    def volumes(comp):
                        return [b.volume*1000 for b in comp.bRepBodies] + [v for o in comp.occurrences for v in volumes(o.component)]
                    print(json.dumps({'volumes_mm3':volumes(target)}))
                """)
                imported_count += 1
                assert len(evidence["volumes_mm3"]) == imported_count, evidence
                assert all(math.isclose(v, 100, abs_tol=0.01) for v in evidence["volumes_mm3"]), (
                    evidence
                )
                self.record(
                    "scoped_import_" + fmt,
                    {
                        "object_count": len(result["imported_objects"]),
                        "volumes_mm3": evidence["volumes_mm3"],
                    },
                )


def main():
    if not __debug__:
        raise RuntimeError("Live validation cannot run with Python optimization enabled")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Authorize creating an unsaved regression fixture"
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--keep-open", action="store_true", help="Leave only this fixture open for inspection"
    )
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; these checks create an unsaved Fusion document")
    with FusionAdapter() as adapter:
        fixture = Fixture(adapter)
        status = "failed"
        try:
            report = fixture.run()
            status = "completed"
        finally:
            try:
                if not args.keep_open:
                    fixture.close()
            except Exception:
                status = "cleanup_failed"
                raise
            finally:
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(
                        json.dumps(
                            {
                                "status": status,
                                "fusion": getattr(fixture, "info", {}),
                                "checks": fixture.checks,
                            },
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
    print(
        json.dumps(
            {
                "passed": sum(c["status"] == "passed" for c in report["checks"]),
                "blocked": sum(c["status"] == "blocked" for c in report["checks"]),
                "fusion": report["fusion"],
            }
        )
    )


if __name__ == "__main__":
    main()
