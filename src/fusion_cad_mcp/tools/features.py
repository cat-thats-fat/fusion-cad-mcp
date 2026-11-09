"""Group 3 / feature tools (first slice).

extrude, fillet_edges_by_geometry, chamfer_edges_by_geometry, mirror_feature,
pattern_rectangular, pattern_circular, combine.

Addressing model (until V2 Section 5a handles land):
- sketches addressed by name
- profiles addressed by (sketch_name, profile_index)
- bodies addressed by name (set when extrude creates them; we also surface body_index)
- features addressed by name
- construction planes / axes addressed by 'xy'|'xz'|'yz'|'x'|'y'|'z' (principal) or name
- edges/faces are NOT addressable here — the `_by_geometry` variants of fillet/chamfer
  iterate edges programmatically and filter by axis direction + optional length
"""

from __future__ import annotations

import json
import textwrap

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from ..handles import emit_resolve, parse_handle
from ..health import FEATURE_HEALTH_CHECK, FEATURE_HEALTH_SCRIPT
from ..resolver import RESOLVER_SCRIPT
from .sketch import SKETCH_RESOLVER_SCRIPT

# ---------- shared helpers ----------

OPERATION_ENUM = {
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
    "new_component": "NewComponentFeatureOperation",
}

PRINCIPAL_PLANES = {
    "xy": "root.xYConstructionPlane",
    "xz": "root.xZConstructionPlane",
    "yz": "root.yZConstructionPlane",
}

PRINCIPAL_AXES = {
    "x": "root.xConstructionAxis",
    "y": "root.yConstructionAxis",
    "z": "root.zConstructionAxis",
}


_FIND_BODY_HELPER = RESOLVER_SCRIPT + """
def _select_root_body(root, name):
    body, error = _select_body(root, name)
    if error: return None, error
    if getattr(body, 'assemblyContext', None) is not None or getattr(body, 'parentComponent', root) != root:
        return None, {'ok': False, 'error': 'child_component_edit_unsupported', 'name': name}
    return body, None
"""

_FIND_FEATURE_OR_BODY_HELPER = _FIND_BODY_HELPER + """
def _find_feature_or_body(root, name):
    feature, owner, feature_error = _select_feature(root, name)
    body, body_error = _select_body(root, name)
    if feature_error and feature_error['error'] != 'feature_not_found': return None, None, feature_error
    if body_error and body_error['error'] != 'body_not_found': return None, None, body_error
    if feature is not None and body is not None:
        return None, None, {'ok': False, 'error': 'target_ambiguous', 'name': name}
    if feature is not None:
        if owner != root: return None, None, {'ok': False, 'error': 'child_component_edit_unsupported', 'name': name}
        return 'feature', feature, None
    if body is not None:
        body, error = _select_root_body(root, name)
        if error: return None, None, error
        return 'body', body, None
    return None, None, {'ok': False, 'error': 'target_not_found', 'name': name}
"""

def _resolve_plane_expr(plane: str) -> str:
    """Return Python expression evaluating to the named plane.
    'xy'/'xz'/'yz' -> principal; else assumed to be a construction plane name and looked up."""
    plane_lc = plane.lower()
    if plane_lc in PRINCIPAL_PLANES:
        return PRINCIPAL_PLANES[plane_lc]
    # Otherwise: resolve at runtime via name lookup in constructionPlanes
    return f"""(
        (lambda m: m[0] if len(m) == 1 else None)([root.constructionPlanes.item(i) for i in range(root.constructionPlanes.count)
              if root.constructionPlanes.item(i).name == {json.dumps(plane)}])
    )"""


def _resolve_axis_expr(axis: str) -> str:
    axis_lc = axis.lower()
    if axis_lc in PRINCIPAL_AXES:
        return PRINCIPAL_AXES[axis_lc]
    return f"""(
        (lambda m: m[0] if len(m) == 1 else None)([root.constructionAxes.item(i) for i in range(root.constructionAxes.count)
              if root.constructionAxes.item(i).name == {json.dumps(axis)}])
    )"""


def _emit_header() -> str:
    return """\
import adsk.core, adsk.fusion
import json
""" + FEATURE_HEALTH_SCRIPT


def _ok_runner(adapter: FusionAdapter, script: str, label: str) -> Envelope:
    env = execute_typed_script(adapter, script)
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return script_parse_failure(env, f"{label}_parse_failed")
    if parsed.get("ok") is False:
        return Envelope(state=env.state,
            ok=False,
            error=parsed.get("error", f"{label}_failed"),
            message=env.message,
            result=parsed,
        )
    return Envelope(state=env.state, ok=True, message=env.message, result=parsed)


# ---------- extrude ----------

VALID_EXTENTS = {"distance", "symmetric", "all_positive", "all_negative"}


def build_extrude(
    sketch: str,
    profile_index: int = 0,
    operation: str = "new_body",
    extent_kind: str = "distance",
    expression: str | None = None,
    direction: str = "positive",
    is_full_length: bool = True,
    participants: list[str] | None = None,
    name: str | None = None,
) -> str:
    if operation not in OPERATION_ENUM:
        raise ValueError(f"operation must be one of {sorted(OPERATION_ENUM)}, got {operation!r}")
    if extent_kind not in VALID_EXTENTS:
        raise ValueError(f"extent_kind must be one of {sorted(VALID_EXTENTS)}, got {extent_kind!r}")
    if extent_kind in {"distance", "symmetric"} and not expression:
        raise ValueError(f"extent_kind={extent_kind} requires an expression")
    if direction not in {"positive", "negative"}:
        raise ValueError(f"direction must be positive or negative, got {direction!r}")

    op_expr = f"adsk.fusion.FeatureOperations.{OPERATION_ENUM[operation]}"

    # extent setter
    if extent_kind == "distance":
        direction_enum = "NegativeExtentDirection" if direction == "negative" else "PositiveExtentDirection"
        extent_setter = (
            f"ext_in.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create("
            f"adsk.core.ValueInput.createByString({json.dumps(expression)})), "
            f"adsk.fusion.ExtentDirections.{direction_enum})"
        )
    elif extent_kind == "symmetric":
        is_full = "True" if is_full_length else "False"
        extent_setter = (
            f"ext_in.setSymmetricExtent("
            f"adsk.core.ValueInput.createByString({json.dumps(expression)}), {is_full})"
        )
    elif extent_kind == "all_positive":
        extent_setter = "ext_in.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)"
    else:  # all_negative
        extent_setter = "ext_in.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)"

    # participants
    parts_block = ""
    parts_helper = ""
    if participants:
        parts_block = "\n".join(
            [
                "    _parts = []",
                *[
                    f"    _b, _error = _select_body(root, {json.dumps(p, ensure_ascii=False)})"
                    + "\n    if _error: print(json.dumps(_error)); return"
                    + "\n    if _b.parentComponent != owner: print(json.dumps({'ok': False, 'error': 'participant_wrong_component'})); return"
                    + "\n    _parts.append(_b.nativeObject or _b)"
                    for p in participants
                ],
                "    ext_in.participantBodies = _parts",
            ]
        )

    name_block = ""
    if name:
        name_block = f"    feat.name = {json.dumps(name)}\n"

    return f"""\
{_emit_header()}{SKETCH_RESOLVER_SCRIPT}{parts_helper}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    sk, owner, error = _select_sketch(root, {sketch!r})
    if error:
        print(json.dumps(error)); return
    if {profile_index} < 0 or {profile_index} >= sk.profiles.count:
        print(json.dumps({{"ok": False, "error": "profile_index_out_of_range",
                           "got": {profile_index}, "count": sk.profiles.count}})); return

    ext_in = owner.features.extrudeFeatures.createInput(sk.profiles.item({profile_index}), {op_expr})
    {extent_setter}
{parts_block}
    feat = owner.features.extrudeFeatures.add(ext_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    body_names = [feat.bodies.item(i).name for i in range(feat.bodies.count)] if feat.bodies.count > 0 else []
    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "operation": {json.dumps(operation)},
        "extent_kind": {json.dumps(extent_kind)},
        "bodies_added": body_names,
    }}))
"""


def extrude(
    adapter: FusionAdapter,
    sketch: str,
    profile_index: int = 0,
    operation: str = "new_body",
    extent_kind: str = "distance",
    expression: str | None = None,
    direction: str = "positive",
    is_full_length: bool = True,
    participants: list[str] | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_extrude(
            sketch,
            profile_index,
            operation,
            extent_kind,
            expression,
            direction,
            is_full_length,
            participants,
            name,
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "extrude")


# ---------- fillet_edges_by_geometry ----------

EDGE_PARALLEL = {"x", "y", "z", "any"}


def build_fillet_edges_by_geometry(
    body: str,
    radius: str,
    parallel_to: str = "z",
    min_length_mm: float | None = None,
    is_tangent_chain: bool = True,
    name: str | None = None,
) -> str:
    if parallel_to not in EDGE_PARALLEL:
        raise ValueError(f"parallel_to must be one of {sorted(EDGE_PARALLEL)}, got {parallel_to!r}")
    if not radius or not isinstance(radius, str):
        raise ValueError("radius must be a non-empty expression string")

    min_len_check = "True"
    if min_length_mm is not None:
        if min_length_mm < 0:
            raise ValueError("min_length_mm must be >= 0")
        min_len_check = f"(edge_length_cm >= {min_length_mm / 10.0})"

    if parallel_to == "any":
        axis_check = "True"
    else:
        # parallel to axis A means the other two coords match between endpoints
        if parallel_to == "x":
            axis_check = (
                "abs(sp.y - ep.y) < 1e-6 and abs(sp.z - ep.z) < 1e-6 and abs(sp.x - ep.x) > 1e-6"
            )
        elif parallel_to == "y":
            axis_check = (
                "abs(sp.x - ep.x) < 1e-6 and abs(sp.z - ep.z) < 1e-6 and abs(sp.y - ep.y) > 1e-6"
            )
        else:  # z
            axis_check = (
                "abs(sp.x - ep.x) < 1e-6 and abs(sp.y - ep.y) < 1e-6 and abs(sp.z - ep.z) > 1e-6"
            )

    is_tan = "True" if is_tangent_chain else "False"
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    body, error = _select_root_body(root, {json.dumps(body)})
    if error:
        print(json.dumps(error)); return

    edges = adsk.core.ObjectCollection.create()
    matched = 0
    for i in range(body.edges.count):
        e = body.edges.item(i)
        if {parallel_to!r} != 'any' and adsk.core.Line3D.cast(e.geometry) is None:
            continue
        edge_length_cm = e.length
        if {parallel_to!r} != 'any':
            if e.startVertex is None or e.endVertex is None: continue
            sp = e.startVertex.geometry
            ep = e.endVertex.geometry
        if {axis_check} and {min_len_check}:
            edges.add(e)
            matched += 1

    if matched == 0:
        print(json.dumps({{"ok": False, "error": "no_edges_matched",
                           "parallel_to": {json.dumps(parallel_to)},
                           "min_length_mm": {repr(min_length_mm)}}})); return

    fil_in = root.features.filletFeatures.createInput()
    fil_in.addConstantRadiusEdgeSet(edges, adsk.core.ValueInput.createByString({json.dumps(radius)}), {is_tan})
    feat = root.features.filletFeatures.add(fil_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "edges_filleted": matched,
        "radius": {json.dumps(radius)},
    }}))
"""


def fillet_edges_by_geometry(
    adapter: FusionAdapter,
    body: str,
    radius: str,
    parallel_to: str = "z",
    min_length_mm: float | None = None,
    is_tangent_chain: bool = True,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_fillet_edges_by_geometry(
            body, radius, parallel_to, min_length_mm, is_tangent_chain, name
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "fillet_edges_by_geometry")


# ---------- chamfer_edges_by_geometry ----------

CHAMFER_KINDS = {"equal", "two_dist", "dist_angle"}


def build_chamfer_edges_by_geometry(
    body: str,
    distance: str,
    parallel_to: str = "z",
    kind: str = "equal",
    distance2: str | None = None,
    angle: str | None = None,
    min_length_mm: float | None = None,
    name: str | None = None,
) -> str:
    if parallel_to not in EDGE_PARALLEL:
        raise ValueError(f"parallel_to must be one of {sorted(EDGE_PARALLEL)}, got {parallel_to!r}")
    if kind not in CHAMFER_KINDS:
        raise ValueError(f"kind must be one of {sorted(CHAMFER_KINDS)}, got {kind!r}")
    if not distance:
        raise ValueError("distance is required")
    if kind == "two_dist" and not distance2:
        raise ValueError("kind=two_dist requires distance2")
    if kind == "dist_angle" and not angle:
        raise ValueError("kind=dist_angle requires angle")

    min_len_check = "True"
    if min_length_mm is not None:
        if min_length_mm < 0:
            raise ValueError("min_length_mm must be >= 0")
        min_len_check = f"(edge_length_cm >= {min_length_mm / 10.0})"

    if parallel_to == "any":
        axis_check = "True"
    elif parallel_to == "x":
        axis_check = (
            "abs(sp.y - ep.y) < 1e-6 and abs(sp.z - ep.z) < 1e-6 and abs(sp.x - ep.x) > 1e-6"
        )
    elif parallel_to == "y":
        axis_check = (
            "abs(sp.x - ep.x) < 1e-6 and abs(sp.z - ep.z) < 1e-6 and abs(sp.y - ep.y) > 1e-6"
        )
    else:
        axis_check = (
            "abs(sp.x - ep.x) < 1e-6 and abs(sp.y - ep.y) < 1e-6 and abs(sp.z - ep.z) > 1e-6"
        )

    # createInput2() takes no args. Edge sets are added via
    # chamferEdgeSets.add*ChamferEdgeSet; add*ChamferEdges no longer exists.
    if kind == "equal":
        chm_call = (
            f"chm_in.chamferEdgeSets.addEqualDistanceChamferEdgeSet(edges, "
            f"adsk.core.ValueInput.createByString({json.dumps(distance)}), False)"
        )
    elif kind == "two_dist":
        chm_call = (
            f"chm_in.chamferEdgeSets.addTwoDistancesChamferEdgeSet(edges, "
            f"adsk.core.ValueInput.createByString({json.dumps(distance)}), "
            f"adsk.core.ValueInput.createByString({json.dumps(distance2)}), False, False)"
        )
    else:  # dist_angle
        chm_call = (
            f"chm_in.chamferEdgeSets.addDistanceAndAngleChamferEdgeSet(edges, "
            f"adsk.core.ValueInput.createByString({json.dumps(distance)}), "
            f"adsk.core.ValueInput.createByString({json.dumps(angle)}), False, False)"
        )

    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    body, error = _select_root_body(root, {json.dumps(body)})
    if error:
        print(json.dumps(error)); return

    edges = adsk.core.ObjectCollection.create()
    matched = 0
    for i in range(body.edges.count):
        e = body.edges.item(i)
        if {parallel_to!r} != 'any' and adsk.core.Line3D.cast(e.geometry) is None:
            continue
        edge_length_cm = e.length
        if {parallel_to!r} != 'any':
            if e.startVertex is None or e.endVertex is None: continue
            sp = e.startVertex.geometry
            ep = e.endVertex.geometry
        if {axis_check} and {min_len_check}:
            edges.add(e)
            matched += 1

    if matched == 0:
        print(json.dumps({{"ok": False, "error": "no_edges_matched"}})); return

    chm_in = root.features.chamferFeatures.createInput2()
    {chm_call}
    feat = root.features.chamferFeatures.add(chm_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "edges_chamfered": matched,
        "kind": {json.dumps(kind)},
    }}))
"""


def chamfer_edges_by_geometry(
    adapter: FusionAdapter,
    body: str,
    distance: str,
    parallel_to: str = "z",
    kind: str = "equal",
    distance2: str | None = None,
    angle: str | None = None,
    min_length_mm: float | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_chamfer_edges_by_geometry(
            body, distance, parallel_to, kind, distance2, angle, min_length_mm, name
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "chamfer_edges_by_geometry")


# ---------- mirror_feature ----------


def build_mirror_feature(
    feature_or_body: str,
    plane: str,
    name: str | None = None,
) -> str:
    plane_expr = _resolve_plane_expr(plane)
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""
    return f"""\
{_emit_header()}{_FIND_FEATURE_OR_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    kind, target, error = _find_feature_or_body(root, {json.dumps(feature_or_body)})
    if error:
        print(json.dumps(error)); return

    plane = {plane_expr}
    if plane is None:
        print(json.dumps({{"ok": False, "error": "plane_not_found", "plane": {json.dumps(plane)}}})); return

    items = adsk.core.ObjectCollection.create()
    items.add(target)

    mir_in = root.features.mirrorFeatures.createInput(items, plane)
    feat = root.features.mirrorFeatures.add(mir_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "mirrored_kind": kind,
        "source": {json.dumps(feature_or_body)},
        "plane": {json.dumps(plane)},
    }}))
"""


def mirror_feature(
    adapter: FusionAdapter,
    feature_or_body: str,
    plane: str,
    name: str | None = None,
) -> Envelope:
    return _ok_runner(adapter, build_mirror_feature(feature_or_body, plane, name), "mirror_feature")


# ---------- pattern_rectangular ----------


def build_pattern_rectangular(
    feature_or_body: str,
    x_axis: str = "x",
    x_count: int = 2,
    x_distance: str | None = None,
    y_axis: str | None = None,
    y_count: int = 1,
    y_distance: str | None = None,
    name: str | None = None,
) -> str:
    if x_count < 1:
        raise ValueError("x_count must be >= 1")
    if y_count < 1:
        raise ValueError("y_count must be >= 1")
    if x_count > 1 and not x_distance:
        raise ValueError("x_distance required when x_count > 1")
    if y_axis and y_count > 1 and not y_distance:
        raise ValueError("y_distance required when y_axis given and y_count > 1")

    x_axis_expr = _resolve_axis_expr(x_axis)
    y_axis_expr = _resolve_axis_expr(y_axis) if y_axis else "None"

    # setDirectionTwo must always be called: an unset direction two yields
    # coincident duplicate bodies, not one row. Single-direction patterns pin it
    # to quantity 1 / 0 mm. The fallback axis must differ from the primary axis
    # or Fusion rejects the input; a named axis falls back to Y.
    _fallback = "z" if str(x_axis).lower() == "y" else "y"
    dir_two_fallback_expr = PRINCIPAL_AXES[_fallback]

    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_FEATURE_OR_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    _kind, target, error = _find_feature_or_body(root, {json.dumps(feature_or_body)})
    if error:
        print(json.dumps(error)); return

    # repr(), not json: None must render as Python None, not null.
    x_axis = {x_axis_expr}
    y_axis = {y_axis_expr}
    if x_axis is None:
        print(json.dumps({{"ok": False, "error": "x_axis_not_found", "axis": {json.dumps(x_axis)}}})); return
    if {y_axis is not None!r} and y_axis is None:
        print(json.dumps({{"ok": False, "error": "y_axis_not_found", "axis": {repr(y_axis)}}})); return

    items = adsk.core.ObjectCollection.create()
    items.add(target)

    pat_in = root.features.rectangularPatternFeatures.createInput(
        items,
        x_axis,
        adsk.core.ValueInput.createByReal({x_count}),
        adsk.core.ValueInput.createByString({json.dumps(x_distance or "0 mm")}),
        adsk.fusion.PatternDistanceType.ExtentPatternDistanceType,
    )
    # Direction two is always set; see build_pattern_rectangular for why.
    if y_axis is not None:
        pat_in.setDirectionTwo(
            y_axis,
            adsk.core.ValueInput.createByReal({y_count}),
            adsk.core.ValueInput.createByString({json.dumps(y_distance or "0 mm")}),
        )
    else:
        pat_in.setDirectionTwo(
            {dir_two_fallback_expr},
            adsk.core.ValueInput.createByReal(1),
            adsk.core.ValueInput.createByString("0 mm"),
        )

    feat = root.features.rectangularPatternFeatures.add(pat_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "x_count": {x_count},
        "y_count": {y_count} if {y_axis is not None!r} else 1,
    }}))
"""


def pattern_rectangular(
    adapter: FusionAdapter,
    feature_or_body: str,
    x_axis: str = "x",
    x_count: int = 2,
    x_distance: str | None = None,
    y_axis: str | None = None,
    y_count: int = 1,
    y_distance: str | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_pattern_rectangular(
            feature_or_body,
            x_axis,
            x_count,
            x_distance,
            y_axis,
            y_count,
            y_distance,
            name,
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "pattern_rectangular")


# ---------- pattern_circular ----------


def build_pattern_circular(
    feature_or_body: str,
    axis: str = "z",
    count: int = 6,
    total_angle: str = "360 deg",
    name: str | None = None,
) -> str:
    if count < 2:
        raise ValueError("count must be >= 2")
    if not total_angle:
        raise ValueError("total_angle required")

    axis_expr = _resolve_axis_expr(axis)
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_FEATURE_OR_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    _kind, target, error = _find_feature_or_body(root, {json.dumps(feature_or_body)})
    if error:
        print(json.dumps(error)); return

    axis = {axis_expr}
    if axis is None:
        print(json.dumps({{"ok": False, "error": "axis_not_found", "axis": {json.dumps(axis)}}})); return

    items = adsk.core.ObjectCollection.create()
    items.add(target)

    pat_in = root.features.circularPatternFeatures.createInput(items, axis)
    pat_in.quantity = adsk.core.ValueInput.createByReal({count})
    pat_in.totalAngle = adsk.core.ValueInput.createByString({json.dumps(total_angle)})
    feat = root.features.circularPatternFeatures.add(pat_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "count": {count},
        "total_angle": {json.dumps(total_angle)},
    }}))
"""


def pattern_circular(
    adapter: FusionAdapter,
    feature_or_body: str,
    axis: str = "z",
    count: int = 6,
    total_angle: str = "360 deg",
    name: str | None = None,
) -> Envelope:
    try:
        script = build_pattern_circular(feature_or_body, axis, count, total_angle, name)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "pattern_circular")


# ---------- combine ----------

COMBINE_OPS = {
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
}


def build_combine(
    target_body: str,
    tool_bodies: list[str],
    operation: str = "join",
    keep_tools: bool = False,
    name: str | None = None,
) -> str:
    if operation not in COMBINE_OPS:
        raise ValueError(f"operation must be one of {sorted(COMBINE_OPS)}, got {operation!r}")
    if not tool_bodies:
        raise ValueError("tool_bodies must be non-empty")

    op_expr = f"adsk.fusion.FeatureOperations.{COMBINE_OPS[operation]}"
    keep = "True" if keep_tools else "False"
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    target, error = _select_root_body(root, {json.dumps(target_body)})
    if error:
        print(json.dumps(error)); return

    tools = adsk.core.ObjectCollection.create()
    for nm in {json.dumps(tool_bodies)}:
        b, error = _select_root_body(root, nm)
        if error:
            print(json.dumps(error)); return
        tools.add(b)

    combo_in = root.features.combineFeatures.createInput(target, tools)
    combo_in.operation = {op_expr}
    combo_in.isKeepToolBodies = {keep}
    feat = root.features.combineFeatures.add(combo_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "operation": {json.dumps(operation)},
        "keep_tools": {keep},
    }}))
"""


def combine(
    adapter: FusionAdapter,
    target_body: str,
    tool_bodies: list[str],
    operation: str = "join",
    keep_tools: bool = False,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_combine(target_body, tool_bodies, operation, keep_tools, name)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "combine")


# ---------- revolve ----------

REVOLVE_EXTENTS = {"angle", "full"}


def build_revolve(
    sketch: str,
    profile_index: int = 0,
    axis: str = "z",
    operation: str = "new_body",
    extent_kind: str = "full",
    angle: str | None = None,
    is_symmetric: bool = False,
    participants: list[str] | None = None,
    name: str | None = None,
) -> str:
    if operation not in OPERATION_ENUM:
        raise ValueError(f"operation must be one of {sorted(OPERATION_ENUM)}, got {operation!r}")
    if extent_kind not in REVOLVE_EXTENTS:
        raise ValueError(
            f"extent_kind must be one of {sorted(REVOLVE_EXTENTS)}, got {extent_kind!r}"
        )
    if extent_kind == "angle" and not angle:
        raise ValueError("extent_kind=angle requires angle expression")

    axis_expr = _resolve_axis_expr(axis)
    op_expr = f"adsk.fusion.FeatureOperations.{OPERATION_ENUM[operation]}"

    if extent_kind == "full":
        extent_setter = (
            "rev_in.setAngleExtent(False, adsk.core.ValueInput.createByString('360 deg'))"
        )
    else:
        sym = "True" if is_symmetric else "False"
        extent_setter = f"rev_in.setAngleExtent({sym}, adsk.core.ValueInput.createByString({json.dumps(angle)}))"

    parts_block = ""
    parts_helper = SKETCH_RESOLVER_SCRIPT
    if participants:
        parts_helper = SKETCH_RESOLVER_SCRIPT + _FIND_BODY_HELPER
        parts_block = "\n".join(
            [
                "    _parts = []",
                *[
                    f"    _b, _error = _select_root_body(root, {json.dumps(p)})"
                    + "\n    if _error: print(json.dumps(_error)); return"
                    + "\n    _parts.append(_b)"
                    for p in participants
                ],
                "    rev_in.participantBodies = _parts",
            ]
        )

    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{parts_helper}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    sk, owner, error = _select_sketch(root, {json.dumps(sketch)})
    if error:
        print(json.dumps(error)); return
    if owner != root:
        print(json.dumps({{"ok": False, "error": "child_component_edit_unsupported", "name": {json.dumps(sketch)}}})); return
    if {profile_index} < 0 or {profile_index} >= sk.profiles.count:
        print(json.dumps({{"ok": False, "error": "profile_index_out_of_range",
                           "got": {profile_index}, "count": sk.profiles.count}})); return

    axis = {axis_expr}
    if axis is None:
        print(json.dumps({{"ok": False, "error": "axis_not_found", "axis": {json.dumps(axis)}}})); return

    rev_in = root.features.revolveFeatures.createInput(sk.profiles.item({profile_index}), axis, {op_expr})
    {extent_setter}
{parts_block}
    feat = root.features.revolveFeatures.add(rev_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    body_names = [feat.bodies.item(i).name for i in range(feat.bodies.count)] if feat.bodies.count > 0 else []
    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "operation": {json.dumps(operation)},
        "extent_kind": {json.dumps(extent_kind)},
        "bodies_added": body_names,
    }}))
"""


def revolve(
    adapter: FusionAdapter,
    sketch: str,
    profile_index: int = 0,
    axis: str = "z",
    operation: str = "new_body",
    extent_kind: str = "full",
    angle: str | None = None,
    is_symmetric: bool = False,
    participants: list[str] | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_revolve(
            sketch,
            profile_index,
            axis,
            operation,
            extent_kind,
            angle,
            is_symmetric,
            participants,
            name,
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "revolve")


# ---------- shell ----------


def build_shell(
    body: str,
    thickness: str,
    face_normals_to_remove: list[list[float]] | None = None,
    direction: str = "inside",
    name: str | None = None,
) -> str:
    if not thickness:
        raise ValueError("thickness expression required")
    if direction not in {"inside", "outside", "both"}:
        raise ValueError(f"direction must be inside/outside/both, got {direction!r}")

    # ShellFeatureInput has no direction enum (ThicknessDirections does not
    # exist). Direction comes from which thickness property is set.
    if direction == "inside":
        thickness_setters = f"shell_in.insideThickness = adsk.core.ValueInput.createByString({json.dumps(thickness)})"
    elif direction == "outside":
        thickness_setters = f"shell_in.outsideThickness = adsk.core.ValueInput.createByString({json.dumps(thickness)})"
    else:  # both: same expression applied to each side
        thickness_setters = (
            f"shell_in.insideThickness = adsk.core.ValueInput.createByString({json.dumps(thickness)})\n"
            f"    shell_in.outsideThickness = adsk.core.ValueInput.createByString({json.dumps(thickness)})"
        )

    if face_normals_to_remove is None:
        face_normals_to_remove = []

    face_finder = ""
    if face_normals_to_remove:
        normals_str = ", ".join(f"({n[0]}, {n[1]}, {n[2]})" for n in face_normals_to_remove)
        face_finder = f"""    target_faces = adsk.core.ObjectCollection.create()
    target_normals = [{normals_str}]
    for i in range(body.faces.count):
        f = body.faces.item(i)
        ok, n = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if not ok: continue
        for tn in target_normals:
            if abs(n.x - tn[0]) < 1e-3 and abs(n.y - tn[1]) < 1e-3 and abs(n.z - tn[2]) < 1e-3:
                target_faces.add(f); break
    if target_faces.count == 0:
        print(json.dumps({{'ok': False, 'error': 'no_faces_matched_normals',
                           'normals': {json.dumps(face_normals_to_remove)}}})); return
    removed_count = target_faces.count
"""
    else:
        # Closed shell: input entities must hold the body itself; ShellFeatures.add
        # rejects an empty collection.
        face_finder = (
            "    target_faces = adsk.core.ObjectCollection.create()\n"
            "    target_faces.add(body)  # no faces removed = closed (hollow) shell\n"
            "    removed_count = 0\n"
        )

    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    body, error = _select_root_body(root, {json.dumps(body)})
    if error:
        print(json.dumps(error)); return

{face_finder}
    shell_in = root.features.shellFeatures.createInput(target_faces, False)
    {thickness_setters}
    feat = root.features.shellFeatures.add(shell_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "faces_removed": removed_count,
        "thickness": {json.dumps(thickness)},
        "direction": {json.dumps(direction)},
    }}))
"""


def shell(
    adapter: FusionAdapter,
    body: str,
    thickness: str,
    face_normals_to_remove: list[list[float]] | None = None,
    direction: str = "inside",
    name: str | None = None,
) -> Envelope:
    try:
        script = build_shell(body, thickness, face_normals_to_remove, direction, name)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "shell")


# ---------- move_body ----------


def build_move_body(
    body: str,
    translation_mm: list[float] | None = None,
    rotation_axis: list[float] | None = None,
    rotation_angle_deg: float | None = None,
    rotation_origin_mm: list[float] | None = None,
    name: str | None = None,
) -> str:
    if translation_mm is None and rotation_axis is None:
        raise ValueError(
            "must provide at least translation_mm or rotation_axis + rotation_angle_deg"
        )
    if rotation_axis is not None and rotation_angle_deg is None:
        raise ValueError("rotation_axis requires rotation_angle_deg")
    if translation_mm is not None and len(translation_mm) != 3:
        raise ValueError("translation_mm must be [tx, ty, tz] in mm")

    tx, ty, tz = (0.0, 0.0, 0.0)
    if translation_mm:
        tx, ty, tz = (v / 10.0 for v in translation_mm)

    rotation_block = ""
    if rotation_axis is not None:
        rx, ry, rz = rotation_axis
        origin = rotation_origin_mm or [0, 0, 0]
        ox, oy, oz = (v / 10.0 for v in origin)
        import math

        angle_rad = (rotation_angle_deg or 0) * math.pi / 180.0
        rotation_block = f"""
    rot = adsk.core.Matrix3D.create()
    rot.setToRotation({angle_rad}, adsk.core.Vector3D.create({rx}, {ry}, {rz}), adsk.core.Point3D.create({ox}, {oy}, {oz}))
    matrix.transformBy(rot)
"""

    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    return f"""\
{_emit_header()}{_FIND_BODY_HELPER}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    body, error = _select_root_body(root, {json.dumps(body)})
    if error:
        print(json.dumps(error)); return

    matrix = adsk.core.Matrix3D.create()
    matrix.translation = adsk.core.Vector3D.create({tx}, {ty}, {tz})
{rotation_block}
    bodies = adsk.core.ObjectCollection.create()
    bodies.add(body)

    mv_in = root.features.moveFeatures.createInput(bodies, matrix)
    feat = root.features.moveFeatures.add(mv_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "body": {json.dumps(body)},
    }}))
"""


def move_body(
    adapter: FusionAdapter,
    body: str,
    translation_mm: list[float] | None = None,
    rotation_axis: list[float] | None = None,
    rotation_angle_deg: float | None = None,
    rotation_origin_mm: list[float] | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_move_body(
            body, translation_mm, rotation_axis, rotation_angle_deg, rotation_origin_mm, name
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "move_body")


# ---------- rib ----------

RIB_SIDES = {"left", "right", "symmetric"}


def build_rib(
    sketch: str, thickness: str, side: str = "symmetric", extend_profile: bool = True,
    name: str | None = None, target_body: str | None = None, is_flipped: bool = False,
) -> str:
    if not thickness:
        raise ValueError("thickness expression required")
    if side not in RIB_SIDES:
        raise ValueError(f"side must be one of {sorted(RIB_SIDES)}, got {side!r}")
    if side != "symmetric" or not extend_profile:
        raise ValueError("Only symmetric ribs extending to the next face are supported")
    if not target_body:
        raise ValueError("target_body is required for a rib")
    if not isinstance(is_flipped, bool):
        raise ValueError("is_flipped must be a bool")
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""
    return f"""\
{_emit_header()}{SKETCH_RESOLVER_SCRIPT}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent
    ribs = root.features.ribFeatures
    if not hasattr(ribs, 'createInput') or not hasattr(ribs, 'add'):
        print(json.dumps({{"ok": False, "error": "rib_not_scriptable"}})); return
    sk, owner, error = _select_sketch(root, {json.dumps(sketch)})
    if error:
        print(json.dumps(error)); return
    if owner != root:
        print(json.dumps({{"ok": False, "error": "child_component_edit_unsupported", "name": {json.dumps(sketch)}}})); return
    body, error = _select_body(root, {target_body!r})
    if error:
        print(json.dumps(error)); return
    if body.parentComponent != root:
        print(json.dumps({{"ok": False, "error": "rib_component_unsupported"}})); return
    curves = [sk.sketchCurves.item(i) for i in range(sk.sketchCurves.count)]
    if not curves:
        print(json.dumps({{"ok": False, "error": "no_sketch_curves"}})); return
    rib_in = ribs.createInput(curves, adsk.core.ValueInput.createByString({json.dumps(thickness)}), body)
    if rib_in is None:
        print(json.dumps({{"ok": False, "error": "rib_input_failed"}})); return
    rib_in.isSymmetric = True
    rib_in.isFlipped = {is_flipped!r}
    if not rib_in.setToNextExtent():
        print(json.dumps({{"ok": False, "error": "rib_extent_failed"}})); return
    feat = ribs.add(rib_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "rib_creation_failed"}})); return
{name_block}    _health_error = _feature_health_error(feat)
    if _health_error:
        if _health_error['error'] == 'feature_unhealthy':
            _health_error['error'] = 'rib_feature_unhealthy'
        print(json.dumps(_health_error)); return
    print(json.dumps({{"ok": True, "feature_name": feat.name, "thickness": {json.dumps(thickness)}, "side": "symmetric"}}))
"""


def rib(
    adapter: FusionAdapter, sketch: str, thickness: str, side: str = "symmetric",
    extend_profile: bool = True, name: str | None = None, target_body: str | None = None,
    is_flipped: bool = False,
) -> Envelope:
    try:
        script = build_rib(sketch, thickness, side, extend_profile, name, target_body, is_flipped)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "rib")


# ---------- add_hole (simple, counterbore, countersink) ----------

HOLE_KINDS = {"simple", "counterbore", "countersink"}


def build_add_hole(
    body: str,
    position_mm: list[float],
    diameter: str,
    kind: str = "simple",
    cbore_diameter: str | None = None,
    cbore_depth: str | None = None,
    csink_diameter: str | None = None,
    csink_angle: str | None = None,
    extent_kind: str = "all",
    depth_expression: str | None = None,
    name: str | None = None,
    face_handle: str | None = None,
) -> str:
    if len(position_mm) != 3:
        raise ValueError("position_mm must be [x, y, z] in mm")
    if not diameter:
        raise ValueError("diameter expression required")
    if kind not in HOLE_KINDS:
        raise ValueError(f"kind must be one of {sorted(HOLE_KINDS)}, got {kind!r}")
    if extent_kind not in {"all", "distance"}:
        raise ValueError(f"extent_kind must be all or distance, got {extent_kind!r}")
    if extent_kind == "distance" and not depth_expression:
        raise ValueError("extent_kind=distance requires depth_expression")
    if kind == "counterbore" and not (cbore_diameter and cbore_depth):
        raise ValueError("kind=counterbore requires cbore_diameter + cbore_depth")
    if kind == "countersink" and not (csink_diameter and csink_angle):
        raise ValueError("kind=countersink requires csink_diameter + csink_angle")

    target_x, target_y, target_z = position_mm
    token = None
    if face_handle is not None:
        parsed_handle = parse_handle(face_handle)
        if parsed_handle["kind"] != "face":
            raise ValueError("face_handle must be a face handle")
        token = parsed_handle["token"]
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

    resolve_face = textwrap.indent(emit_resolve(face_handle, var="face"), "        ") if face_handle else ""

    if extent_kind == "all":
        extent_setter = "hole_in.setAllExtent(adsk.fusion.ExtentDirections.PositiveExtentDirection)"
    else:
        extent_setter = f"hole_in.setDistanceExtent(adsk.core.ValueInput.createByString({json.dumps(depth_expression)}))"

    if kind == "simple":
        input_create = (
            f"hole_in = body.parentComponent.features.holeFeatures.createSimpleInput("
            f"adsk.core.ValueInput.createByString({json.dumps(diameter)}))"
        )
    elif kind == "counterbore":
        input_create = (
            f"hole_in = body.parentComponent.features.holeFeatures.createCounterboreInput(\n"
            f"        adsk.core.ValueInput.createByString({json.dumps(diameter)}),\n"
            f"        adsk.core.ValueInput.createByString({json.dumps(cbore_diameter)}),\n"
            f"        adsk.core.ValueInput.createByString({json.dumps(cbore_depth)}))"
        )
    else:  # countersink
        input_create = (
            f"hole_in = body.parentComponent.features.holeFeatures.createCountersinkInput(\n"
            f"        adsk.core.ValueInput.createByString({json.dumps(diameter)}),\n"
            f"        adsk.core.ValueInput.createByString({json.dumps(csink_diameter)}),\n"
            f"        adsk.core.ValueInput.createByString({json.dumps(csink_angle)}))"
        )

    return f"""\
{_emit_header()}{RESOLVER_SCRIPT}
P = adsk.core.Point3D.create

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    body, error = _select_body(root, {body!r})
    if error:
        print(json.dumps(error)); return
    if body.parentComponent != root:
        print(json.dumps({{"ok": False, "error": "hole_component_unsupported"}})); return

    point = P({target_x / 10}, {target_y / 10}, {target_z / 10})
    face = None
    face_token = {repr(token)}
    if face_token is not None:
{resolve_face}        if face.body != body:
            print(json.dumps({{"ok": False, "error": "face_wrong_body"}})); return
        if adsk.core.Plane.cast(face.geometry) is None or not face.isPointOnFace(point):
            print(json.dumps({{"ok": False, "error": "point_not_on_planar_face"}})); return
    else:
        # Preserve the top-face convenience, while requiring the point's XY to
        # be within the actual trimmed face, not just its bounding box.
        candidates = []
        for i in range(body.faces.count):
            f = body.faces.item(i)
            if adsk.core.Plane.cast(f.geometry) is None: continue
            ok, normal = f.evaluator.getNormalAtPoint(f.pointOnFace)
            if not ok or abs(normal.z - 1.0) > 1e-3: continue
            projected = P(point.x, point.y, f.pointOnFace.z)
            if f.isPointOnFace(projected):
                candidates.append((abs(projected.z - point.z), f, projected))
        candidates.sort(key=lambda entry: entry[0])
        if not candidates:
            print(json.dumps({{"ok": False, "error": "no_top_face_at_position"}})); return
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1e-7:
            print(json.dumps({{"ok": False, "error": "face_ambiguous"}})); return
        _, face, point = candidates[0]

    {input_create}
    if not hole_in.setPositionByPoint(face, point):
        print(json.dumps({{"ok": False, "error": "hole_position_failed"}})); return
    {extent_setter}
    hole_in.participantBodies = [body]
    feat = body.parentComponent.features.holeFeatures.add(hole_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "kind": {json.dumps(kind)},
        "diameter": {json.dumps(diameter)},
        "position_mm": {json.dumps(position_mm)},
        "actual_position_mm": [point.x * 10, point.y * 10, point.z * 10],
    }}))
"""


def add_hole(
    adapter: FusionAdapter,
    body: str,
    position_mm: list[float],
    diameter: str,
    kind: str = "simple",
    cbore_diameter: str | None = None,
    cbore_depth: str | None = None,
    csink_diameter: str | None = None,
    csink_angle: str | None = None,
    extent_kind: str = "all",
    depth_expression: str | None = None,
    name: str | None = None,
    face_handle: str | None = None,
) -> Envelope:
    try:
        script = build_add_hole(
            body,
            position_mm,
            diameter,
            kind,
            cbore_diameter,
            cbore_depth,
            csink_diameter,
            csink_angle,
            extent_kind,
            depth_expression,
            name,
            face_handle,
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "add_hole")


# Back-compat alias
build_add_hole_simple = build_add_hole
add_hole_simple = add_hole


# ---------- rebuild_feature ----------


def build_rebuild_feature(feature_name: str, component_name: str | None = None) -> str:
    """Refuse destructive recreation until dependent references can be preserved."""
    return f"""\
import json

def run(_ctx):
    print(json.dumps({{"ok": False, "error": "safe_rebuild_unavailable",
        "feature_name": {json.dumps(feature_name)}, "component": {repr(component_name)},
        "detail": "Automatic recreation cannot safely preserve the feature and its dependent references."}}))
"""


def rebuild_feature(
    adapter: FusionAdapter,
    feature_name: str,
    component_name: str | None = None,
) -> Envelope:
    """Run rebuild_feature; returns envelope with old/new health states + the captured inputs."""
    return _ok_runner(
        adapter,
        build_rebuild_feature(feature_name, component_name),
        "rebuild_feature",
    )


__all__ = [
    "add_hole_simple",
    "build_add_hole_simple",
    "build_chamfer_edges_by_geometry",
    "build_combine",
    "build_extrude",
    "build_fillet_edges_by_geometry",
    "build_mirror_feature",
    "build_pattern_circular",
    "build_pattern_rectangular",
    "build_rebuild_feature",
    "build_revolve",
    "build_shell",
    "chamfer_edges_by_geometry",
    "combine",
    "extrude",
    "fillet_edges_by_geometry",
    "mirror_feature",
    "pattern_circular",
    "pattern_rectangular",
    "rebuild_feature",
    "revolve",
    "shell",
]
