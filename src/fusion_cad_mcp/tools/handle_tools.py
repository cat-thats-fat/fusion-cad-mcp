"""Tools that consume entity handles (V2 spec Section 5a).

measure(entity_a, entity_b, kind)            — distance / min_distance / angle
find_mesh_using_ray(component, origin, dir)  — NEW May 2026: Component.findMeshUsingRay
ray_collision_with_mesh(mesh_body, ...)      — NEW May 2026: MeshBody.calculateCollisionsWithRay
fillet_edges(edges[], radius)                — UI-selection-style; takes edge handles
chamfer_edges(edges[], distance, ...)        — UI-selection-style; takes edge handles
project_to_sketch(sketch, entities[])        — project handles onto a named sketch

Entity arguments use the handle resolver. Component and sketch selectors use
names. Mesh ray searches return scoped mesh handles for collision checks.
"""

from __future__ import annotations

import json
import textwrap

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_stdout_json, script_parse_failure
from ..handles import (
    emit_make_handle,
    emit_resolve,
    emit_resolve_many,
    emit_scope_expr,
    parse_handle,
)
from ..health import FEATURE_HEALTH_CHECK, FEATURE_HEALTH_SCRIPT
from ..resolver import RESOLVER_SCRIPT
from .sketch import SKETCH_RESOLVER_SCRIPT


def _indent4(s: str) -> str:
    """Indent each line by 4 spaces so multi-line emit_resolve* output slots cleanly
    inside a `def run(_ctx):` body without IndentationError on subsequent lines.
    """
    return textwrap.indent(
        s, "    "
    ).lstrip()  # lstrip the FIRST line; the f-string already supplies indent for it


MEASURE_KINDS = {"distance", "min_distance", "angle"}


def _header() -> str:
    return "import adsk.core, adsk.fusion\nimport json\n" + FEATURE_HEALTH_SCRIPT


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


# ---------- measure ----------


def build_measure(entity_a: str, entity_b: str, kind: str = "distance") -> str:
    if kind not in MEASURE_KINDS:
        raise ValueError(f"kind must be one of {sorted(MEASURE_KINDS)}, got {kind!r}")
    resolve_a = _indent4(emit_resolve(entity_a, var="_a"))
    resolve_b = _indent4(emit_resolve(entity_b, var="_b"))

    if kind == "distance":
        measure_call = "result = app.measureManager.measureMinimumDistance(_a, _b)"
        report = (
            "{\n"
            "        'ok': True, 'kind': 'distance',\n"
            "        'value_cm': result.value, 'value_mm': result.value * 10.0,\n"
            "        'point_a_cm': [result.positionOne.x, result.positionOne.y, result.positionOne.z],\n"
            "        'point_b_cm': [result.positionTwo.x, result.positionTwo.y, result.positionTwo.z],\n"
            "    }"
        )
    elif kind == "min_distance":
        measure_call = "result = app.measureManager.measureMinimumDistance(_a, _b)"
        report = (
            "{\n"
            "        'ok': True, 'kind': 'min_distance',\n"
            "        'value_cm': result.value, 'value_mm': result.value * 10.0,\n"
            "    }"
        )
    else:  # angle
        measure_call = "result = app.measureManager.measureAngle(_a, _b)"
        report = (
            "{\n"
            "        'ok': True, 'kind': 'angle',\n"
            "        'value_radians': result.value, 'value_degrees': result.value * 180.0 / 3.141592653589793,\n"
            "    }"
        )

    return f"""\
{_header()}
def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return

    {resolve_a}
    {resolve_b}
    {measure_call}
    print(json.dumps({report}))
"""


def measure(
    adapter: FusionAdapter, entity_a: str, entity_b: str, kind: str = "distance"
) -> Envelope:
    try:
        parse_handle(entity_a)
        parse_handle(entity_b)
        script = build_measure(entity_a, entity_b, kind)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "measure", read_only=True)


# ---------- find_mesh_using_ray (NEW May 2026) ----------


def build_find_mesh_using_ray(
    component_name: str | None,
    origin_mm: list[float],
    direction: list[float],
) -> str:
    if len(origin_mm) != 3:
        raise ValueError("origin_mm must be [x, y, z] in mm")
    if len(direction) != 3:
        raise ValueError("direction must be [dx, dy, dz]")
    ox, oy, oz = (v / 10.0 for v in origin_mm)
    dx, dy, dz = direction

    return f"""\
{_header()}
{RESOLVER_SCRIPT}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent
    selector = {component_name!r}
    scope = None
    if selector:
        occ, error = _select_occurrence(root, selector)
        if error and error['error'] == 'occurrence_ambiguous':
            error['error'] = 'component_ambiguous'
            print(json.dumps(error)); return
        if selector == root.name and occ is not None:
            print(json.dumps({{'ok': False, 'error': 'component_ambiguous', 'name': selector,
                              'matches': [root.name, occ.fullPathName]}})); return
        if selector != root.name:
            if error:
                error['error'] = 'component_not_found'
                print(json.dumps(error)); return
            scope = occ.fullPathName

    origin = adsk.core.Point3D.create(float({ox}), float({oy}), float({oz}))
    direction_vec = adsk.core.Vector3D.create(float({dx}), float({dy}), float({dz}))

    # Cast in root coordinates so proxy meshes and the ray share a frame.
    meshes, _hit_points = root.findMeshUsingRay(origin, direction_vec)
    names = []
    mesh_handles = []
    for mesh in meshes:
        occ = mesh.assemblyContext
        occ_path = occ.fullPathName if occ is not None else None
        if scope is not None and not (occ_path == scope or (occ_path and occ_path.startswith(scope + '+'))):
            continue
        names.append(mesh.name)
        path = (occ_path + ' / ' + mesh.name + '|occ=' + occ_path if occ_path else mesh.name)
        mesh_handles.append({emit_make_handle("mesh_body", "path", "mesh")})
    print(json.dumps({{
        "ok": True,
        "meshes_found": len(names),
        "names": names,
        "mesh_handles": mesh_handles,
    }}))
"""


def find_mesh_using_ray(
    adapter: FusionAdapter,
    origin_mm: list[float],
    direction: list[float],
    component_name: str | None = None,
) -> Envelope:
    try:
        script = build_find_mesh_using_ray(component_name, origin_mm, direction)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "find_mesh_using_ray", read_only=True)


# ---------- ray_collision_with_mesh (NEW May 2026) ----------


def build_ray_collision_with_mesh(
    mesh_handle: str,
    origin_mm: list[float],
    direction: list[float],
) -> str:
    if len(origin_mm) != 3:
        raise ValueError("origin_mm must be [x, y, z] in mm")
    if len(direction) != 3:
        raise ValueError("direction must be [dx, dy, dz]")
    ox, oy, oz = (v / 10.0 for v in origin_mm)
    dx, dy, dz = direction
    kind = parse_handle(mesh_handle)["kind"]
    if kind not in {"mesh_body", "body"}:
        raise ValueError("mesh_handle must be a mesh_body handle")
    # Older callers supplied mesh tokens using the generic body prefix.
    if kind == "body":
        mesh_handle = "mesh_body:" + mesh_handle.split(":", 1)[1]
    resolve = _indent4(emit_resolve(mesh_handle, var="_mesh"))

    return f"""\
{_header()}
def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return

    {resolve}
    origin = adsk.core.Point3D.create({ox}, {oy}, {oz})
    direction_vec = adsk.core.Vector3D.create({dx}, {dy}, {dz})

    points = _mesh.calculateCollisionsWithRay(origin, direction_vec)
    pts_mm = [[p.x * 10.0, p.y * 10.0, p.z * 10.0] for p in points]
    print(json.dumps({{
        "ok": True,
        "collisions": len(pts_mm),
        "points_mm": pts_mm,
    }}))
"""


def ray_collision_with_mesh(
    adapter: FusionAdapter,
    mesh_handle: str,
    origin_mm: list[float],
    direction: list[float],
) -> Envelope:
    try:
        parse_handle(mesh_handle)
        script = build_ray_collision_with_mesh(mesh_handle, origin_mm, direction)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "ray_collision_with_mesh", read_only=True)


# ---------- fillet_edges (UI selection style) ----------


def build_fillet_edges(
    edge_handles: list[str], radius: str, is_tangent_chain: bool = True, name: str | None = None
) -> str:
    if not edge_handles:
        raise ValueError("edge_handles must be non-empty")
    if any(parse_handle(handle)['kind'] != 'edge' for handle in edge_handles):
        raise ValueError("edge_handles must contain only edge handles")
    if not radius:
        raise ValueError("radius expression required")
    resolve = _indent4(emit_resolve_many(edge_handles, collection_var="_resolved_edges"))
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""
    is_tan = "True" if is_tangent_chain else "False"

    return f"""\
{_header()}
def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    {resolve}
    edges = adsk.core.ObjectCollection.create()
    for e in _resolved_edges:
        edges.add(e)

    fil_in = root.features.filletFeatures.createInput()
    fil_in.addConstantRadiusEdgeSet(edges, adsk.core.ValueInput.createByString({json.dumps(radius)}), {is_tan})
    feat = root.features.filletFeatures.add(fil_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "edges_filleted": edges.count,
        "radius": {json.dumps(radius)},
    }}))
"""


def fillet_edges(
    adapter: FusionAdapter,
    edge_handles: list[str],
    radius: str,
    is_tangent_chain: bool = True,
    name: str | None = None,
) -> Envelope:
    try:
        for h in edge_handles:
            parse_handle(h)
        script = build_fillet_edges(edge_handles, radius, is_tangent_chain, name)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "fillet_edges")


# ---------- chamfer_edges (UI selection style) ----------

CHAMFER_KINDS = {"equal", "two_dist", "dist_angle"}


def build_chamfer_edges(
    edge_handles: list[str],
    distance: str,
    kind: str = "equal",
    distance2: str | None = None,
    angle: str | None = None,
    name: str | None = None,
) -> str:
    if not edge_handles:
        raise ValueError("edge_handles must be non-empty")
    if any(parse_handle(handle)['kind'] != 'edge' for handle in edge_handles):
        raise ValueError("edge_handles must contain only edge handles")
    if kind not in CHAMFER_KINDS:
        raise ValueError(f"kind must be one of {sorted(CHAMFER_KINDS)}, got {kind!r}")
    if kind == "two_dist" and not distance2:
        raise ValueError("kind=two_dist requires distance2")
    if kind == "dist_angle" and not angle:
        raise ValueError("kind=dist_angle requires angle")

    resolve = _indent4(emit_resolve_many(edge_handles, collection_var="_resolved_edges"))
    name_block = f"    feat.name = {json.dumps(name)}\n" if name else ""

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

    return f"""\
{_header()}
def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    {resolve}
    edges = adsk.core.ObjectCollection.create()
    for e in _resolved_edges:
        edges.add(e)

    chm_in = root.features.chamferFeatures.createInput2()
    {chm_call}
    feat = root.features.chamferFeatures.add(chm_in)
    if feat is None:
        print(json.dumps({{"ok": False, "error": "feature_creation_failed", "model_may_have_changed": True}})); return
{FEATURE_HEALTH_CHECK}{name_block}    print(json.dumps({{
        "ok": True,
        "feature_name": feat.name,
        "edges_chamfered": edges.count,
        "kind": {json.dumps(kind)},
    }}))
"""


def chamfer_edges(
    adapter: FusionAdapter,
    edge_handles: list[str],
    distance: str,
    kind: str = "equal",
    distance2: str | None = None,
    angle: str | None = None,
    name: str | None = None,
) -> Envelope:
    try:
        for h in edge_handles:
            parse_handle(h)
        script = build_chamfer_edges(edge_handles, distance, kind, distance2, angle, name)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "chamfer_edges")


# ---------- project_to_sketch ----------


def build_project_to_sketch(sketch_name: str, entity_handles: list[str]) -> str:
    if not entity_handles:
        raise ValueError("entity_handles must be non-empty")
    resolve = _indent4(emit_resolve_many(entity_handles, collection_var="_to_project"))
    return f"""\
{_header()}
{SKETCH_RESOLVER_SCRIPT}
def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    sk, owner, error = _select_sketch(root, {json.dumps(sketch_name)})
    if error:
        print(json.dumps(error)); return
    if owner != root:
        for i in range(root.allOccurrences.count):
            occ = root.allOccurrences.item(i)
            if occ.component == owner:
                sk = sk.createForAssemblyContext(occ)
                break
        if sk is None:
            print(json.dumps({{"ok": False, "error": "sketch_proxy_unavailable", "name": {json.dumps(sketch_name)}}})); return

    {resolve}
    result = sk.project2(_to_project, True)
    if result is None or len(result) == 0:
        print(json.dumps({{"ok": False, "error": "projection_no_curves", "mutation_state": "unknown"}})); return

    print(json.dumps({{
        "ok": True,
        "sketch": {json.dumps(sketch_name)},
        "entities_projected": len(_to_project),
        "curves_added": len(result) if result is not None else 0,
    }}))
"""


def project_to_sketch(adapter: FusionAdapter, sketch: str, entity_handles: list[str]) -> Envelope:
    try:
        for h in entity_handles:
            parse_handle(h)
        script = build_project_to_sketch(sketch, entity_handles)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "project_to_sketch")


# ---------- list_body_entities (bridge from name-addressing to handle-addressing) ----------

ENTITY_KINDS = {"face", "edge", "vertex"}


def build_list_body_entities(
    body_name: str,
    kinds: list[str] | None = None,
    face_normal_filter: list[float] | None = None,
    edge_parallel_to: str | None = None,
    min_edge_length_mm: float | None = None,
) -> str:
    if kinds is None:
        kinds = ["face", "edge", "vertex"]
    for k in kinds:
        if k not in ENTITY_KINDS:
            raise ValueError(f"unknown entity kind {k!r}; must be one of {sorted(ENTITY_KINDS)}")
    do_faces = "face" in kinds
    do_edges = "edge" in kinds
    do_verts = "vertex" in kinds

    # Filters sit in a `for` inside an `if`, so statements indent 12 and their
    # `continue` indents 16.
    face_filter_block = ""
    if face_normal_filter is not None:
        if len(face_normal_filter) != 3:
            raise ValueError("face_normal_filter must be [nx, ny, nz]")
        nx, ny, nz = face_normal_filter
        face_filter_block = (
            f"            ok_n, n = f.evaluator.getNormalAtPoint(f.pointOnFace)\n"
            f"            if not ok_n or not (abs(n.x - {nx}) < 1e-3 and abs(n.y - {ny}) < 1e-3 and abs(n.z - {nz}) < 1e-3):\n"
            f"                continue\n"
        )

    edge_filter_block = ""
    if edge_parallel_to is not None:
        ep = edge_parallel_to.lower()
        if ep not in {"x", "y", "z"}:
            raise ValueError("edge_parallel_to must be x, y, or z")
        if ep == "x":
            cond = (
                "abs(sp.y - ep_.y) < 1e-6 and abs(sp.z - ep_.z) < 1e-6 and abs(sp.x - ep_.x) > 1e-6"
            )
        elif ep == "y":
            cond = (
                "abs(sp.x - ep_.x) < 1e-6 and abs(sp.z - ep_.z) < 1e-6 and abs(sp.y - ep_.y) > 1e-6"
            )
        else:
            cond = (
                "abs(sp.x - ep_.x) < 1e-6 and abs(sp.y - ep_.y) < 1e-6 and abs(sp.z - ep_.z) > 1e-6"
            )
        edge_filter_block = (
            "            if adsk.core.Line3D.cast(e.geometry) is None:\n"
            "                continue\n"
            "            sp = e.startVertex.geometry\n"
            "            ep_ = e.endVertex.geometry\n"
            f"            if not ({cond}):\n"
            "                continue\n"
        )

    if min_edge_length_mm is not None:
        if min_edge_length_mm < 0:
            raise ValueError("min_edge_length_mm must be >= 0")
        edge_filter_block += (
            f"            if e.length < {min_edge_length_mm / 10.0}:\n"
            f"                continue\n"
        )

    return f"""\
{_header()}

{RESOLVER_SCRIPT}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}})); return
    root = design.rootComponent

    selector = {body_name!r}
    body, error = _select_body(root, selector)
    if error:
        print(json.dumps(error)); return

    _occ = getattr(body, "assemblyContext", None)
    _occ_suffix = ("|occ=" + _occ.fullPathName) if _occ else ""
    _scope_suffix = {emit_scope_expr()}
    faces = []
    edges = []
    vertices = []

    if {do_faces}:
        for i in range(body.faces.count):
            f = body.faces.item(i)
{face_filter_block}            faces.append({{
                'index': i,
                'handle': 'face:' + selector + '/face[' + str(i) + ']' + _occ_suffix + _scope_suffix + ':' + f.entityToken,
                'pointOnFace_mm': [f.pointOnFace.x * 10.0, f.pointOnFace.y * 10.0, f.pointOnFace.z * 10.0],
            }})

    if {do_edges}:
        for i in range(body.edges.count):
            e = body.edges.item(i)
{edge_filter_block}            edges.append({{
                'index': i,
                'handle': 'edge:' + selector + '/edge[' + str(i) + ']' + _occ_suffix + _scope_suffix + ':' + e.entityToken,
                'length_mm': e.length * 10.0,
            }})

    if {do_verts}:
        for i in range(body.vertices.count):
            v = body.vertices.item(i)
            vertices.append({{
                'index': i,
                'handle': 'vertex:' + selector + '/vertex[' + str(i) + ']' + _occ_suffix + _scope_suffix + ':' + v.entityToken,
                'position_mm': [v.geometry.x * 10.0, v.geometry.y * 10.0, v.geometry.z * 10.0],
            }})

    print(json.dumps({{
        "ok": True,
        "body": selector,
        "face_count": len(faces),
        "edge_count": len(edges),
        "vertex_count": len(vertices),
        "faces": faces,
        "edges": edges,
        "vertices": vertices,
    }}))
"""


def list_body_entities(
    adapter: FusionAdapter,
    body_name: str,
    kinds: list[str] | None = None,
    face_normal_filter: list[float] | None = None,
    edge_parallel_to: str | None = None,
    min_edge_length_mm: float | None = None,
) -> Envelope:
    try:
        script = build_list_body_entities(
            body_name, kinds, face_normal_filter, edge_parallel_to, min_edge_length_mm
        )
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    return _ok_runner(adapter, script, "list_body_entities", read_only=True)


__all__ = [
    "build_chamfer_edges",
    "build_find_mesh_using_ray",
    "build_fillet_edges",
    "build_list_body_entities",
    "build_measure",
    "build_project_to_sketch",
    "build_ray_collision_with_mesh",
    "chamfer_edges",
    "fillet_edges",
    "find_mesh_using_ray",
    "list_body_entities",
    "measure",
    "project_to_sketch",
    "ray_collision_with_mesh",
]
