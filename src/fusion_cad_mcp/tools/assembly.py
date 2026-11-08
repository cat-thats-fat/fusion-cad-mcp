"""Group 5 / assembly tools (first slice, no joints).

bodies_to_components(map)  — turn root-level bodies into named components, preserving world position.
move_component(name, translation_mm[, rotation_axis, rotation_angle_deg])  — reposition an occurrence.
ground_component(name) / unground_component(name)  — toggle the ground flag.
create_rigid_group(component_names)  — lock components together as a rigid group.
create_contact_set(body_names)  — define a contact set for physics / motion.
interference_check(occurrence_names | body_names)  — find overlaps; returns count + per-pair volumes.

Joint creation tools (create_joint, create_as_built_joint, set_joint_limits, drive_joint)
deferred until entity handle resolution lands; they need face/edge refs that the
current name-based addressing can't express.
"""

from __future__ import annotations

import json

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_stdout_json, script_parse_failure
from ..resolver import RESOLVER_SCRIPT

_FIND_BODY_HELPER = RESOLVER_SCRIPT
_FIND_OCC_HELPER = RESOLVER_SCRIPT


def _ok_runner(adapter: FusionAdapter, script: str, label: str, *, read_only: bool = False) -> Envelope:
    env = adapter.execute_script(script, read_only=True) if read_only else adapter.execute_script(script)
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is None:
        return script_parse_failure(env, f"{label}_parse_failed", read_only=read_only)
    if parsed.get("ok") is False:
        return Envelope(
            ok=False,
            error=parsed.get("error", f"{label}_failed"),
            message=env.message,
            result=parsed,
        )
    return Envelope(ok=True, message=env.message, result=parsed)


def _header() -> str:
    return "import adsk.core, adsk.fusion\nimport json\n"


# ---------- bodies_to_components ----------


def build_bodies_to_components(mapping: dict[str, str]) -> str:
    """Convert root-level bodies to named components (one per entry).

    Preserves world position via createComponentFromBody.
    """
    if not mapping or not isinstance(mapping, dict):
        raise ValueError("mapping must be a non-empty dict of body_name -> component_name")
    return f"""\
{_header()}{_FIND_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    pending = []
    for body_name, comp_name in {mapping!r}.items():
        b, error = _select_body(root, body_name)
        if error:
            print(json.dumps(error)); return
        if getattr(b, "assemblyContext", None) is not None:
            print(json.dumps({{"ok": False, "error": "body_not_in_root", "name": body_name}})); return
        pending.append((b, body_name, comp_name))

    converted = []
    for b, body_name, comp_name in pending:
        # BRepBody.createComponent() preserves world position.
        # Features.createComponentFromBodyFeatures does not exist.
        new_body = b.createComponent()
        if new_body is None:
            print(json.dumps({{"ok": False, "error": "create_component_failed", "name": body_name, "converted": converted}})); return
        new_body.parentComponent.name = comp_name
        # createComponent() renames the body to the component default. Restore
        # the original so name-based addressing survives the conversion.
        new_body.name = body_name
        converted.append({{"from_body": body_name, "to_component": comp_name}})

    print(json.dumps({{"ok": True, "converted": converted, "total_components": root.occurrences.count}}))
"""


def bodies_to_components(adapter: FusionAdapter, mapping: dict[str, str]) -> Envelope:
    try:
        script = build_bodies_to_components(mapping)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "bodies_to_components")


# ---------- move_component ----------


def build_move_component(
    name: str,
    translation_mm: list[float] | None = None,
    rotation_axis: list[float] | None = None,
    rotation_angle_deg: float | None = None,
    rotation_origin_mm: list[float] | None = None,
) -> str:
    if translation_mm is None and rotation_axis is None:
        raise ValueError(
            "must provide at least translation_mm or (rotation_axis + rotation_angle_deg)"
        )
    if rotation_axis is not None and rotation_angle_deg is None:
        raise ValueError("rotation_axis requires rotation_angle_deg")
    if translation_mm is not None and len(translation_mm) != 3:
        raise ValueError("translation_mm must be [tx, ty, tz] in mm")
    if rotation_axis is not None and len(rotation_axis) != 3:
        raise ValueError("rotation_axis must be [x, y, z]")

    tx, ty, tz = (0.0, 0.0, 0.0)
    if translation_mm:
        tx, ty, tz = (v / 10.0 for v in translation_mm)

    rotation_block = ""
    if rotation_axis is not None:
        rx, ry, rz = rotation_axis
        origin = rotation_origin_mm or [0, 0, 0]
        ox, oy, oz = (v / 10.0 for v in origin)
        angle_rad = (rotation_angle_deg or 0) * 3.141592653589793 / 180.0
        rotation_block = f"""
    rot = adsk.core.Matrix3D.create()
    rot.setToRotation({angle_rad}, adsk.core.Vector3D.create({rx}, {ry}, {rz}), adsk.core.Point3D.create({ox}, {oy}, {oz}))
    matrix.transformBy(rot)
"""

    return f"""\
{_header()}{_FIND_OCC_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    occ, error = _select_occurrence(root, {name!r})
    if error:
        print(json.dumps(error)); return

    matrix = adsk.core.Matrix3D.create()
    matrix.translation = adsk.core.Vector3D.create({tx}, {ty}, {tz})
{rotation_block}
    if getattr(occ, 'isGroundToParent', False) or getattr(occ, 'isGrounded', False):
        print(json.dumps({{"ok": False, "error": "occurrence_grounded", "name": {name!r}}})); return
    snapshots = design.snapshots
    # A snapshot captures all pending occurrence changes in the design.
    if snapshots.hasPendingSnapshot:
        print(json.dumps({{"ok": False, "error": "pending_assembly_transforms"}})); return

    original = occ.transform2
    before_transform = list(original.asArray())
    before = original.translation
    before_mm = [before.x * 10, before.y * 10, before.z * 10]
    current = occ.transform2
    current.transformBy(matrix)
    expected_transform = list(current.asArray())

    def matches(actual, expected):
        return all(abs(a - b) <= 1e-7 for a, b in zip(actual, expected))

    snapshot = None
    after_transform = None
    error = 'move_not_achieved'
    try:
        occ.transform2 = current
        after_transform = list(occ.transform2.asArray())
        if not matches(after_transform, expected_transform):
            raise RuntimeError('Fusion did not apply the requested transform')
        if snapshots.hasPendingSnapshot:
            error = 'move_snapshot_failed'
            snapshot = snapshots.add()
            if snapshot is None or snapshots.hasPendingSnapshot:
                raise RuntimeError('Fusion did not capture the requested transform')
        error = 'move_not_achieved'
        actual = occ.transform2
        after_transform = list(actual.asArray())
        if not matches(after_transform, expected_transform):
            raise RuntimeError('Fusion changed the transform while capturing it')
    except Exception as exc:
        try:
            deleted = snapshot is None or snapshot.deleteMe()
            if not snapshots.hasPendingSnapshot and not matches(occ.transform2.asArray(), before_transform):
                occ.transform2 = original
            reverted = not snapshots.hasPendingSnapshot or snapshots.revertPendingSnapshot()
            rolled_back = bool(deleted and reverted and not snapshots.hasPendingSnapshot
                               and matches(occ.transform2.asArray(), before_transform))
        except Exception:
            rolled_back = False
        print(json.dumps({{"ok": False, "error": error, "detail": str(exc),
            "before_transform": before_transform, "expected_transform": expected_transform,
            "after_transform": after_transform, "rolled_back": rolled_back}})); return

    after = actual.translation
    after_mm = [after.x * 10, after.y * 10, after.z * 10]
    moved = any(abs(a - b) > 1e-8 for a, b in zip(after_transform, before_transform))

    # repr(), not json: None must render as Python None, not null.
    print(json.dumps({{
        "ok": True,
        "component": {repr(name)},
        "translation_mm": {repr(translation_mm) if translation_mm else repr([0, 0, 0])},
        "rotation_axis": {repr(rotation_axis)},
        "rotation_angle_deg": {repr(rotation_angle_deg)},
        "before_translation_mm": [round(v, 4) for v in before_mm],
        "after_translation_mm": [round(v, 4) for v in after_mm],
        "moved": moved,
        "before_transform": before_transform,
        "after_transform": after_transform,
    }}))
"""


def move_component(
    adapter: FusionAdapter,
    name: str,
    translation_mm: list[float] | None = None,
    rotation_axis: list[float] | None = None,
    rotation_angle_deg: float | None = None,
    rotation_origin_mm: list[float] | None = None,
) -> Envelope:
    try:
        script = build_move_component(
            name, translation_mm, rotation_axis, rotation_angle_deg, rotation_origin_mm
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "move_component")


# ---------- ground / unground ----------


def build_ground_component(name: str, grounded: bool) -> str:
    return f"""\
{_header()}{_FIND_OCC_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    occ, error = _select_occurrence(root, {name!r})
    if error:
        print(json.dumps(error)); return

    pin = {grounded!r}
    was_pinned = occ.isGrounded
    was_parent_grounded = getattr(occ, 'isGroundToParent', None)
    if pin and was_parent_grounded:
        print(json.dumps({{"ok": False, "error": "occurrence_grounded_to_parent", "component": {name!r}}})); return

    # Pin preserves the current pose. Ground To Parent resets the insertion pose.
    before = list(occ.transform2.asArray()) if pin else None
    if was_pinned != pin:
        occ.isGrounded = pin
    if not pin and was_parent_grounded:
        occ.isGroundToParent = False

    pinned = occ.isGrounded
    parent_grounded = getattr(occ, 'isGroundToParent', None)
    result = {{"ok": True, "component": {name!r}, "isGrounded": pinned, "isGroundToParent": parent_grounded}}
    if pin:
        after = list(occ.transform2.asArray())
        if any(abs(a - b) > 1e-7 for a, b in zip(before, after)):
            result.update(ok=False, error="pin_changed_transform", before_transform=before,
                          after_transform=after, model_may_have_changed=True)
    if result['ok'] and (pinned != pin or parent_grounded):
        result.update(ok=False, error="grounding_change_not_achieved")
    print(json.dumps(result))
"""


def ground_component(adapter: FusionAdapter, name: str) -> Envelope:
    if not name:
        return Envelope(ok=False, error="invalid_input", message="name required")
    return _ok_runner(adapter, build_ground_component(name, True), "ground_component")


def unground_component(adapter: FusionAdapter, name: str) -> Envelope:
    if not name:
        return Envelope(ok=False, error="invalid_input", message="name required")
    return _ok_runner(adapter, build_ground_component(name, False), "unground_component")


# ---------- create_rigid_group ----------


def build_create_rigid_group(component_names: list[str], name: str | None = None) -> str:
    if not component_names or len(component_names) < 2:
        raise ValueError("rigid group requires at least 2 component names")
    name_block = f"    rg.name = {json.dumps(name)}\n" if name else ""
    return f"""\
{_header()}{_FIND_OCC_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    occs = adsk.core.ObjectCollection.create()
    found = []
    for nm in {json.dumps(component_names)}:
        occ, error = _select_occurrence(root, nm)
        if error:
            print(json.dumps(error)); return
        occs.add(occ)
        found.append(nm)

    # RigidGroups has no createInput; add(occurrences, includeChildren) goes
    # straight on the collection. Raises if an existing joint over-constrains it.
    try:
        rg = root.rigidGroups.add(occs, True)
    except Exception as e:
        print(json.dumps({{"ok": False, "error": "rigid_group_add_failed",
                           "detail": str(e), "components": found}})); return
    if rg is None:
        print(json.dumps({{"ok": False, "error": "rigid_group_add_failed", "components": found}})); return
{name_block}    print(json.dumps({{
        "ok": True,
        "rigid_group_name": rg.name,
        "components": found,
    }}))
"""


def create_rigid_group(
    adapter: FusionAdapter, component_names: list[str], name: str | None = None
) -> Envelope:
    try:
        script = build_create_rigid_group(component_names, name)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "create_rigid_group")


# ---------- create_contact_set ----------


def build_create_contact_set(body_names: list[str]) -> str:
    if not body_names or len(body_names) < 2:
        raise ValueError("contact set requires at least 2 body names")
    return f"""\
{_header()}
{RESOLVER_SCRIPT}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    bodies = []
    found = []
    for nm in {json.dumps(body_names)}:
        b, error = _select_body(root, nm)
        if error:
            print(json.dumps(error)); return
        bodies.append(b)
        found.append(nm)

    # ContactSets lives on the Design, not the Component, and add() takes the
    # array directly. There is no createInput.
    cs = design.contactSets.add(bodies)
    if cs is None:
        print(json.dumps({{"ok": False, "error": "contact_set_add_failed", "bodies": found}})); return
    print(json.dumps({{"ok": True, "contact_set_name": cs.name, "bodies": found}}))
"""


def create_contact_set(adapter: FusionAdapter, body_names: list[str]) -> Envelope:
    try:
        script = build_create_contact_set(body_names)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "create_contact_set")


# ---------- interference_check ----------


def build_interference_check(entity_names: list[str]) -> str:
    if not entity_names or len(entity_names) < 2:
        raise ValueError(
            "interference check requires at least 2 entity names (bodies or components)"
        )
    return f"""\
{_header()}{_FIND_BODY_HELPER}{_FIND_OCC_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    entities = adsk.core.ObjectCollection.create()
    found = []
    for nm in {json.dumps(entity_names)}:
        item, body_error = _select_body(root, nm)
        occ, occ_error = _select_occurrence(root, nm)
        if body_error and body_error['error'] == 'body_ambiguous':
            print(json.dumps(body_error)); return
        if occ_error and occ_error['error'] == 'occurrence_ambiguous':
            print(json.dumps(occ_error)); return
        if item is not None and occ is not None:
            print(json.dumps({{"ok": False, "error": "entity_ambiguous", "name": nm}})); return
        kind = 'body' if item is not None else 'occurrence'
        item = item if item is not None else occ
        if item is None:
            print(json.dumps({{"ok": False, "error": "entity_not_found", "name": nm}})); return
        entities.add(item)
        found.append({{"name": nm, "kind": kind}})

    inp = design.createInterferenceInput(entities)
    inp.areCoincidentFacesIncluded = False
    results = design.analyzeInterference(inp)

    pairs = []
    for i in range(results.count):
        r = results.item(i)
        pairs.append({{
            "entity_one": r.entityOne.name if hasattr(r.entityOne, 'name') else None,
            "entity_two": r.entityTwo.name if hasattr(r.entityTwo, 'name') else None,
            "interference_volume_cm3": round(r.interferenceBody.volume, 6) if r.interferenceBody else 0.0,
            "interference_volume_mm3": round(r.interferenceBody.volume * 1000, 3) if r.interferenceBody else 0.0,
        }})

    print(json.dumps({{
        "ok": True,
        "checked": found,
        "interference_pairs": pairs,
        "pair_count": len(pairs),
    }}))
"""


def interference_check(adapter: FusionAdapter, entity_names: list[str]) -> Envelope:
    try:
        script = build_interference_check(entity_names)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "interference_check", read_only=True)


# ---------- set_joint_limits ----------


def build_set_joint_limits(
    joint_name: str,
    min_value: str | None = None,
    max_value: str | None = None,
    rest_value: str | None = None,
) -> str:
    """Set limits / rest position on an existing joint.

    Per May 2026 release: limits and rest position are first-class joint motion controls.
    For revolute joints values are angles ('45 deg'); for slider joints they are distances.
    Pass None to leave a limit unset. rest_value (NEW May 2026) is where the joint
    snaps back when dragged.
    """
    if min_value is None and max_value is None and rest_value is None:
        raise ValueError("must provide at least one of min_value, max_value, rest_value")

    # Limits hang off the motion's rotationLimits (revolute) or slideLimits
    # (slider); Cylindrical has both and rotation wins. Values are internal
    # units (radians / cm), so expressions go through
    # unitsManager.evaluateExpression with deg/mm as the measure.
    # ValueInput.createByString(...).realValue raises on string inputs.
    requested = [(flag, attr, expr) for flag, attr, expr in (
        ("isMinimumValueEnabled", "minimumValue", min_value),
        ("isMaximumValueEnabled", "maximumValue", max_value),
        ("isRestValueEnabled", "restValue", rest_value),
    ) if expr is not None]

    return f"""\
{_header()}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    matches = [root.joints.item(i) for i in range(root.joints.count)
               if root.joints.item(i).name == {json.dumps(joint_name)}]
    if len(matches) != 1:
        print(json.dumps({{"ok": False, "error": "joint_not_found" if not matches else "joint_ambiguous", "name": {json.dumps(joint_name)}}})); return
    joint = matches[0]

    jm = joint.jointMotion
    if jm is None:
        print(json.dumps({{"ok": False, "error": "joint_has_no_motion", "name": {json.dumps(joint_name)}}})); return

    lim = getattr(jm, 'rotationLimits', None)
    limits_kind = 'rotation'
    if lim is None:
        lim = getattr(jm, 'slideLimits', None)
        limits_kind = 'slide'
    if lim is None:
        print(json.dumps({{"ok": False, "error": "joint_has_no_limits",
                           "name": {json.dumps(joint_name)},
                           "motion_class": jm.classType()}})); return
    units = 'deg' if limits_kind == 'rotation' else 'mm'

    evaluated = []
    manager = design.unitsManager
    for flag, attr, expr in {requested!r}:
        try:
            if not manager.isValidExpression(expr, units):
                raise ValueError('Fusion could not evaluate the expression in the expected units')
            evaluated.append((flag, attr, manager.evaluateExpression(expr, units)))
        except Exception as e:
            print(json.dumps({{"ok": False, "error": "expression_invalid",
                               "expression": expr, "expected_units": units,
                               "detail": str(e)}}))
            return

    for flag, attr, value in evaluated:
        setattr(lim, flag, True)
        setattr(lim, attr, value)

    # Read back what Fusion actually stored (internal units: radians or cm)
    # so callers can verify the limits took effect.
    print(json.dumps({{
        "ok": True,
        "joint_name": {json.dumps(joint_name)},
        "limits_kind": limits_kind,
        "applied": {{
            "min_enabled": lim.isMinimumValueEnabled,
            "min_value_internal": lim.minimumValue if lim.isMinimumValueEnabled else None,
            "max_enabled": lim.isMaximumValueEnabled,
            "max_value_internal": lim.maximumValue if lim.isMaximumValueEnabled else None,
            "rest_enabled": lim.isRestValueEnabled,
            "rest_value_internal": lim.restValue if lim.isRestValueEnabled else None,
        }},
    }}))
"""


def set_joint_limits(
    adapter: FusionAdapter,
    joint_name: str,
    min_value: str | None = None,
    max_value: str | None = None,
    rest_value: str | None = None,
) -> Envelope:
    try:
        script = build_set_joint_limits(joint_name, min_value, max_value, rest_value)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "set_joint_limits")


# ---------- drive_joint ----------


def build_drive_joint(joint_name: str, value: str) -> str:
    if not value:
        raise ValueError("value expression required")
    return f"""\
{_header()}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    matches = [root.joints.item(i) for i in range(root.joints.count)
               if root.joints.item(i).name == {json.dumps(joint_name)}]
    if len(matches) != 1:
        print(json.dumps({{"ok": False, "error": "joint_not_found" if not matches else "joint_ambiguous", "name": {json.dumps(joint_name)}}})); return
    joint = matches[0]

    jm = joint.jointMotion
    if jm is None:
        print(json.dumps({{"ok": False, "error": "joint_has_no_motion", "name": {json.dumps(joint_name)}}})); return

    # Drive via the first available property: rotationValue, slideValue, etc.
    # Evaluated per-attribute with the matching measure (deg rotary, mm slide).
    # ValueInput.createByString(...).realValue raises on string inputs.
    _units_by_attr = {{'rotationValue': 'deg', 'slideValue': 'mm',
                       'rollValue': 'deg', 'pitchValue': 'deg', 'yawValue': 'deg'}}
    set_attrs = []
    last_err = None
    for attr in ('rotationValue', 'slideValue', 'rollValue', 'pitchValue', 'yawValue'):
        if hasattr(jm, attr):
            try:
                target_val = design.unitsManager.evaluateExpression(
                    {json.dumps(value)}, _units_by_attr[attr])
                setattr(jm, attr, target_val)
                set_attrs.append(attr)
                break
            except Exception as e:
                last_err = str(e)

    if not set_attrs:
        print(json.dumps({{"ok": False, "error": "no_drivable_motion_attribute",
                           "motion_class": jm.classType(), "detail": last_err}})); return

    # A drive beyond the joint's limits is ignored silently, so read back the
    # actual value and report whether the target was applied.
    actual = getattr(jm, set_attrs[0])
    print(json.dumps({{
        "ok": True,
        "joint_name": {json.dumps(joint_name)},
        "drove": set_attrs[0],
        "value": {json.dumps(value)},
        "value_internal": actual,
        "requested_internal": target_val,
        "applied": abs(actual - target_val) < 1e-9,
    }}))
"""


def drive_joint(adapter: FusionAdapter, joint_name: str, value: str) -> Envelope:
    try:
        script = build_drive_joint(joint_name, value)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "drive_joint")


# ---------- create_joint ----------

VALID_MOTION_TYPES = {"rigid", "revolute", "slider", "cylindrical", "ball"}
VALID_AXES = {"x", "y", "z"}


def _joint_geometry_for_handle(handle_var: str, kind: str) -> str:
    """Emit Python that turns a resolved Fusion entity (in `handle_var`) into a
    JointGeometry, assigning the result to `<handle_var>_geom`. Returns the
    code block as a string. Generator-time kind dispatch keeps the runtime
    branch tight."""
    geom_var = f"{handle_var}_geom"
    if kind == "face":
        # Try planar first; fall back to non-planar (cylindrical, conical).
        return (
            f"    try:\n"
            f"        {geom_var} = adsk.fusion.JointGeometry.createByPlanarFace(\n"
            f"            {handle_var}, None, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)\n"
            f"    except Exception:\n"
            f"        {geom_var} = None\n"
            f"    if {geom_var} is None:\n"
            f"        {geom_var} = adsk.fusion.JointGeometry.createByNonPlanarFace(\n"
            f"            {handle_var}, adsk.fusion.JointKeyPointTypes.CenterKeyPoint)\n"
            f"    if {geom_var} is None:\n"
            f"        print(json.dumps({{'ok': False, 'error': 'joint_geometry_failed',\n"
            f"            'kind': 'face', 'detail': 'Could not create joint geometry from face'}})); return\n"
        )
    if kind == "edge":
        return (
            f"    {geom_var} = adsk.fusion.JointGeometry.createByCurve(\n"
            f"        {handle_var}, adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)\n"
            f"    if {geom_var} is None:\n"
            f"        print(json.dumps({{'ok': False, 'error': 'joint_geometry_failed',\n"
            f"            'kind': 'edge', 'detail': 'createByCurve returned None'}})); return\n"
        )
    if kind == "vertex":
        return (
            f"    {geom_var} = adsk.fusion.JointGeometry.createByPoint({handle_var})\n"
            f"    if {geom_var} is None:\n"
            f"        print(json.dumps({{'ok': False, 'error': 'joint_geometry_failed',\n"
            f"            'kind': 'vertex', 'detail': 'createByPoint returned None'}})); return\n"
        )
    if kind == "point":  # sketch point
        return (
            f"    {geom_var} = adsk.fusion.JointGeometry.createByPoint({handle_var})\n"
            f"    if {geom_var} is None:\n"
            f"        print(json.dumps({{'ok': False, 'error': 'joint_geometry_failed',\n"
            f"            'kind': 'point', 'detail': 'createByPoint returned None'}})); return\n"
        )
    raise ValueError(
        f"handle kind {kind!r} cannot be used as a joint origin; "
        f"supported kinds: face, edge, vertex, point"
    )


def build_create_joint(
    geometry_one: str,
    geometry_two: str,
    motion_type: str = "rigid",
    axis: str = "z",
    offset_mm: str | None = None,
    angle_deg: str | None = None,
    name: str | None = None,
) -> str:
    """Generate a script that creates a Joint between two entity-handle origins.

    Supported motion types: rigid, revolute, slider, cylindrical, ball.
    The `axis` argument is the joint motion axis relative to the first joint
    geometry's local frame. Use 'z' for the typical 'pin coming out of the
    face' configuration; revolutes about the face normal need 'z'.

    Use entity handles (kind:path:token) from probe_sketch_dimensions or
    list_body_entities. Acceptable kinds: face, edge, vertex, point.

    For a snap-hinge between two mating flanges, a typical setup is
      geometry_one = face center of one hinge knuckle's mating face
      geometry_two = face center of the other hinge knuckle's mating face
      motion_type = "revolute"
      axis = "z"  (face normal)
      offset_mm = "9 mm"  (the joint offset that preserves the gap)
    """
    if motion_type not in VALID_MOTION_TYPES:
        raise ValueError(
            f"motion_type must be one of {sorted(VALID_MOTION_TYPES)}, got {motion_type!r}"
        )
    if axis not in VALID_AXES:
        raise ValueError(f"axis must be one of {sorted(VALID_AXES)}, got {axis!r}")

    import textwrap

    from ..handles import emit_resolve, parse_handle  # local import keeps module load light

    h1 = parse_handle(geometry_one)
    h2 = parse_handle(geometry_two)
    if h1["kind"] not in ("face", "edge", "vertex", "point"):
        raise ValueError(
            f"geometry_one kind {h1['kind']!r} unsupported; need face/edge/vertex/point"
        )
    if h2["kind"] not in ("face", "edge", "vertex", "point"):
        raise ValueError(
            f"geometry_two kind {h2['kind']!r} unsupported; need face/edge/vertex/point"
        )

    # emit_resolve produces top-level Python; indent each block to 4 spaces
    # so it sits cleanly inside run().
    resolve_one = textwrap.indent(emit_resolve(geometry_one, var="_ent1"), "    ")
    resolve_two = textwrap.indent(emit_resolve(geometry_two, var="_ent2"), "    ")
    geom_one_block = _joint_geometry_for_handle("_ent1", h1["kind"])
    geom_two_block = _joint_geometry_for_handle("_ent2", h2["kind"])

    axis_enum = {
        "x": "XAxisJointDirection",
        "y": "YAxisJointDirection",
        "z": "ZAxisJointDirection",
    }[axis]

    if motion_type == "rigid":
        motion_block = "    joint_input.setAsRigidJointMotion()\n"
    elif motion_type == "revolute":
        motion_block = (
            f"    joint_input.setAsRevoluteJointMotion(\n"
            f"        adsk.fusion.JointDirections.{axis_enum})\n"
        )
    elif motion_type == "slider":
        motion_block = (
            f"    joint_input.setAsSliderJointMotion(\n"
            f"        adsk.fusion.JointDirections.{axis_enum})\n"
        )
    elif motion_type == "cylindrical":
        motion_block = (
            f"    joint_input.setAsCylindricalJointMotion(\n"
            f"        adsk.fusion.JointDirections.{axis_enum})\n"
        )
    elif motion_type == "ball":
        # setAsBallJointMotion accepts only pitch=Z, yaw=X. Every other
        # principal-axis pair raises "Invalid parameter".
        motion_block = (
            "    joint_input.setAsBallJointMotion(\n"
            "        adsk.fusion.JointDirections.ZAxisJointDirection,\n"
            "        adsk.fusion.JointDirections.XAxisJointDirection)\n"
        )
    else:
        raise ValueError(f"motion_type {motion_type!r} not handled")  # defensive

    offset_block = ""
    if offset_mm is not None:
        offset_block = f"    joint_input.offset = adsk.core.ValueInput.createByString({json.dumps(offset_mm)})\n"
    angle_block = ""
    if angle_deg is not None:
        angle_block = f"    joint_input.angle = adsk.core.ValueInput.createByString({json.dumps(angle_deg)})\n"

    rename_block = ""
    if name is not None:
        rename_block = f"    joint.name = {json.dumps(name)}\n"

    return f"""\
{_header()}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    # Resolve geometry handles
{resolve_one}
{resolve_two}

    # Build JointGeometry for each
{geom_one_block}{geom_two_block}

    joints = root.joints
    joint_input = joints.createInput(_ent1_geom, _ent2_geom)
    if joint_input is None:
        print(json.dumps({{"ok": False, "error": "joint_input_failed"}})); return

    # Motion type
{motion_block}

    # Optional inputs
{offset_block}{angle_block}
    try:
        joint = joints.add(joint_input)
    except Exception as e:
        print(json.dumps({{
            "ok": False, "error": "joints_add_failed",
            "detail": str(e),
            "motion_type": {repr(motion_type)},
            "axis": {repr(axis)},
        }})); return
    if joint is None:
        print(json.dumps({{
            "ok": False, "error": "joints_add_failed", "motion_type": {repr(motion_type)},
            "axis": {repr(axis)},
        }})); return
{rename_block}
    # repr(), not json: None must render as Python None, not null.
    print(json.dumps({{
        "ok": True,
        "joint_name": joint.name,
        "joint_token": joint.entityToken,
        "motion_type": {repr(motion_type)},
        "axis": {repr(axis)},
        "offset_mm": {repr(offset_mm)},
        "angle_deg": {repr(angle_deg)},
    }}))
"""


def create_joint(
    adapter: FusionAdapter,
    geometry_one: str,
    geometry_two: str,
    motion_type: str = "rigid",
    axis: str = "z",
    offset_mm: str | None = None,
    angle_deg: str | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_create_joint(
            geometry_one,
            geometry_two,
            motion_type,
            axis,
            offset_mm,
            angle_deg,
            name,
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "create_joint")


__all__ = [
    "bodies_to_components",
    "build_bodies_to_components",
    "build_create_contact_set",
    "build_create_joint",
    "build_create_rigid_group",
    "build_drive_joint",
    "build_ground_component",
    "build_interference_check",
    "build_move_component",
    "build_set_joint_limits",
    "create_contact_set",
    "create_joint",
    "create_rigid_group",
    "drive_joint",
    "ground_component",
    "interference_check",
    "move_component",
    "set_joint_limits",
    "unground_component",
]
