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

import hashlib
import json
import math
import re
import textwrap

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from ..handles import (
    emit_make_handle,
    emit_resolve,
    emit_resolve_many,
    emit_scope_expr,
    parse_handle,
)
from ..health import FEATURE_HEALTH_CHECK, FEATURE_HEALTH_SCRIPT
from ..native_mesh import MESH_EVIDENCE_SCRIPT
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
    env = execute_typed_script(adapter, script, read_only=True) if read_only else execute_typed_script(adapter, script)
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return script_parse_failure(env, f"{label}_parse_failed", read_only=read_only)
    if parsed.get("ok") is False:
        return Envelope(state=env.state,
            ok=False,
            error=parsed.get("error", f"{label}_failed"),
            message=env.message,
            result=parsed,
        )
    return Envelope(state=env.state, ok=True, message=env.message, result=parsed)


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

_RAY_METHOD = "fusion_mesh_ray_v1"
_RAY_DEFAULT_TOLERANCE_MM = 1e-6
_RAY_SOURCE_FIELDS = {
    "document_sha256", "mesh_name", "occurrence_path", "entity_token_sha256", "geometry_sha256",
    "transform_sha256",
}
_RAY_LIMITS = {
    "queries": 32, "hits_per_query": 32, "native_hits_per_query": 4096,
    "mesh_nodes": 1_000_000, "mesh_facets": 1_000_000, "facet_queries": 8_000_000,
}


def _ray_number(value, label):
    try:
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
    except OverflowError:
        pass
    raise ValueError(f"{label} must be a finite number")


def _ray_vector(value, label):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must have three coordinates")
    return [_ray_number(coordinate, label) for coordinate in value]


def _ray_query(query):
    required = {"origin_mm", "direction", "start_mm", "end_mm"}
    if not isinstance(query, dict) or not required <= query.keys() <= required | {"selection"}:
        raise ValueError("each ray requires origin_mm, direction, start_mm, and end_mm")
    origin = _ray_vector(query["origin_mm"], "origin_mm")
    direction = _ray_vector(query["direction"], "direction")
    if not any(direction):
        raise ValueError("direction must be nonzero")
    start = _ray_number(query["start_mm"], "start_mm")
    end = _ray_number(query["end_mm"], "end_mm")
    if not 0 <= start < end:
        raise ValueError("ray interval must satisfy 0 <= start_mm < end_mm")
    selection = query.get("selection", "all")
    if selection not in ("all", "nearest", "farthest"):
        raise ValueError("selection must be all, nearest, or farthest")
    return {"origin_mm": origin, "direction": direction, "start_mm": start, "end_mm": end, "selection": selection}


def _prepare_ray_source(source):
    if not isinstance(source, dict) or source.keys() != _RAY_SOURCE_FIELDS:
        raise ValueError("replay requires a returned mesh source identity")
    for key in ("document_sha256", "entity_token_sha256", "geometry_sha256", "transform_sha256"):
        if not isinstance(source[key], str) or not re.fullmatch(r"[a-f0-9]{64}", source[key]):
            raise ValueError(f"invalid source {key}")
    for key in ("mesh_name", "occurrence_path"):
        if not isinstance(source[key], str) or len(source[key]) > 4096:
            raise ValueError(f"invalid source {key}")
    return source


def _ray_witness_digest(source, frame, tolerance_mm, query):
    binding = [_RAY_METHOD, source, frame, tolerance_mm, query]
    return hashlib.sha256(json.dumps(binding, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def _build_batch_rays(mesh_handle, queries, frame, tolerance_mm, replay):
    expected = None
    if replay is not None:
        if queries is not None:
            raise ValueError("supply queries or replay")
        if not isinstance(replay, dict) or replay.get("method") != _RAY_METHOD:
            raise ValueError("replay requires a returned ray witness")
        expected = _prepare_ray_source(replay.get("source"))
        queries = [replay.get("query")]
        frame, tolerance_mm = replay.get("frame"), replay.get("tolerance_mm")
    if frame not in ("root", "local"):
        raise ValueError("frame must be root or local")
    tolerance_mm = _ray_number(tolerance_mm, "tolerance_mm")
    if tolerance_mm < 0:
        raise ValueError("tolerance_mm must be nonnegative")
    if not isinstance(queries, list) or not 1 <= len(queries) <= _RAY_LIMITS["queries"]:
        raise ValueError(f"queries must contain between 1 and {_RAY_LIMITS['queries']} rays")
    prepared = [_ray_query(query) for query in queries]
    if replay is not None and replay.get("query_sha256") != _ray_witness_digest(expected, frame, tolerance_mm, prepared[0]):
        raise ValueError("replay source, frame, tolerance, and query must match the retained witness")
    scope = parse_handle(mesh_handle)["path"].split("|")[1:]
    if not any(part.startswith("doc=") for part in scope):
        raise ValueError("bounded rays require a document-scoped mesh handle")
    occurrence_path = next((part[4:] for part in scope if part.startswith("occ=")), None)
    resolver = textwrap.indent(emit_resolve(mesh_handle, var="mesh"), "    ")
    return (
        _header() + "\nimport hashlib, math, struct\n" + MESH_EVIDENCE_SCRIPT
        + f"\n_QUERIES = {prepared!r}\n_FRAME = {frame!r}"
        + f"\n_TOLERANCE_MM = {tolerance_mm!r}\n_EXPECTED_SOURCE = {expected!r}"
        + f"\n_OCCURRENCE_PATH = {occurrence_path!r}\n_METHOD = {_RAY_METHOD!r}\n_LIMITS = {_RAY_LIMITS!r}\n"
        + "\ndef _resolve_ray_mesh(design, errors):\n    def print(value): errors.append(json.loads(value))\n"
        + resolver + "    return mesh\n" + _BATCH_RAY_SCRIPT
    )


_BATCH_RAY_SCRIPT = '''
def _ray_unit(direction):
    scale = max(map(abs, direction))
    scaled = [value / scale for value in direction]
    length = math.hypot(*scaled)
    return [value / length for value in scaled]

def _ray_document(app):
    doc = app.activeDocument
    if not doc.creationId: raise ValueError('active document identity unavailable')
    key = [str(doc.creationId), str(doc.dataFile.id) if doc.dataFile else None]
    return hashlib.sha256(json.dumps(key).encode('utf-8')).hexdigest()

def _ray_source(app, design, mesh):
    occurrence = mesh.assemblyContext
    if getattr(occurrence, 'fullPathName', None) != _OCCURRENCE_PATH:
        raise ValueError('mesh occurrence scope mismatch')
    if occurrence is None and mesh.parentComponent != design.rootComponent:
        raise ValueError('child mesh requires its occurrence-scoped handle')
    native = mesh.nativeObject if occurrence is not None else mesh
    if native is None or not native.isValid: raise ValueError('native mesh unavailable')
    token = native.entityToken
    if not isinstance(token, str) or not token: raise ValueError('native mesh identity unavailable')
    collection = native.parentComponent.meshBodies
    names = (collection.item(i).name for i in range(collection.count))
    if sum(name == native.name for name in names) != 1:
        raise ValueError('mesh name is missing or ambiguous in its component')
    original = native.mesh
    display = native.displayMesh
    facets = max(original.triangleCount + original.quadCount + original.polygonCount, display.triangleCount)
    if original.nodeCount > _LIMITS['mesh_nodes'] or facets > _LIMITS['mesh_facets'] or facets * len(_QUERIES) > _LIMITS['facet_queries']:
        raise ValueError('mesh exceeds bounded ray work limits')
    witness = _mesh_witness(native, occurrence)
    pose = witness['occurrence_transform_cm']
    source = {'document_sha256': _ray_document(app), 'mesh_name': native.name,
              'occurrence_path': _OCCURRENCE_PATH or '',
              'entity_token_sha256': hashlib.sha256(token.encode('utf-8')).hexdigest(),
              'geometry_sha256': witness['geometry_sha256'],
              'transform_sha256': hashlib.sha256(struct.pack('>16d', *pose)).hexdigest()}
    return native, source, pose

def _ray_pose(pose):
    if _FRAME == 'local' or _OCCURRENCE_PATH is None:
        return None, None
    matrix = adsk.core.Matrix3D.create()
    if not matrix.setWithArray(pose): raise ValueError('mesh transform unavailable')
    inverse = matrix.copy()
    if not inverse.invert(): raise ValueError('mesh transform cannot be inverted')
    return matrix, inverse

def _ray_points(mesh, query, unit, pose, inverse):
    origin = adsk.core.Point3D.create(*(value / 10 for value in query['origin_mm']))
    direction = adsk.core.Vector3D.create(*unit)
    if inverse is not None and (not origin.transformBy(inverse) or not direction.transformBy(inverse)):
        raise ValueError('could not transform query into mesh-local frame')
    points = mesh.calculateCollisionsWithRay(origin, direction)
    if points is None: raise ValueError('Fusion returned no native ray result')
    if len(points) > _LIMITS['native_hits_per_query']:
        return None
    candidates = []
    for point in points:
        if pose is not None and not point.transformBy(pose):
            raise ValueError('could not transform hit into root frame')
        point_mm = [point.x * 10, point.y * 10, point.z * 10]
        offset = [p - o for p, o in zip(point_mm, query['origin_mm'])]
        travel = math.fsum(p * d for p, d in zip(offset, unit))
        if not math.isfinite(travel) or not all(map(math.isfinite, point_mm)):
            raise ValueError('nonfinite native ray hit')
        if not query['start_mm'] <= travel <= query['end_mm']:
            continue
        if math.hypot(*(p - travel * d for p, d in zip(offset, unit))) > _TOLERANCE_MM:
            raise ValueError('native hit lies outside the ray tolerance')
        candidates.append((travel, point_mm))
    return sorted(candidates)

def _ray_observation(mesh, query, pose, inverse):
    unit = _ray_unit(query['direction'])
    result = {'direction_unit': unit, 'hits': [], 'selected_hit_indices': [],
              'contact_classification': 'unavailable'}
    try:
        points = _ray_points(mesh, query, unit, pose, inverse)
    except Exception as exc:
        return dict(result, status='unavailable', reason=str(exc))
    if points is None:
        return dict(result, status='truncated', reason='native_hit_limit')
    hits = result['hits']
    for travel, point_mm in points:
        if hits and travel - hits[-1]['travel_mm'] <= _TOLERANCE_MM:
            hits[-1]['coincident_returns'] += 1
        elif len(hits) == _LIMITS['hits_per_query']:
            return dict(result, status='truncated', reason='reported_hit_limit')
        else:
            hits.append({'travel_mm': travel, 'point_mm': point_mm, 'coincident_returns': 1})
    if not hits:
        return dict(result, status='no_hit')
    if any(hit['coincident_returns'] > 1 for hit in hits):
        return dict(result, status='ambiguous', reason='coincident_native_returns')
    selection = query['selection']
    indices = {'all': list(range(len(hits))), 'nearest': [0], 'farthest': [len(hits) - 1]}
    return dict(result, status='hits', selected_hit_indices=indices[selection])

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({'ok': False, 'error': 'no_active_design'})); return
    errors = []
    try:
        mesh = _resolve_ray_mesh(design, errors)
        if mesh is None or not mesh.isValid:
            detail = errors[0] if errors else {'error': 'handle_invalid'}
            if _EXPECTED_SOURCE is not None:
                detail = {'ok': True, 'replay': {'status': 'stale', 'reason': 'mesh_handle_invalid', 'detail': detail}}
            else:
                detail['ok'] = False
            print(json.dumps(detail)); return
        native, source, matrix = _ray_source(app, design, mesh)
        if _EXPECTED_SOURCE is not None and source != _EXPECTED_SOURCE:
            differences = [field for field in source if source[field] != _EXPECTED_SOURCE[field]]
            print(json.dumps({'ok': True, 'replay': {'status': 'stale', 'reason': 'source_changed',
                                                    'differences': differences}, 'source': source})); return
        pose, inverse = _ray_pose(matrix)
    except Exception as exc:
        detail = {'reason': 'mesh_source_unavailable', 'detail': str(exc)}
        if _EXPECTED_SOURCE is not None:
            print(json.dumps({'ok': True, 'replay': {'status': 'unavailable', **detail}})); return
        print(json.dumps({'ok': False, 'error': 'mesh_source_unavailable', **detail})); return
    counts = dict.fromkeys(('hits', 'no_hit', 'ambiguous', 'unavailable', 'truncated'), 0)
    witnesses = []
    for query in _QUERIES:
        observation = _ray_observation(native, query, pose, inverse)
        counts[observation['status']] += 1
        binding = [_METHOD, source, _FRAME, _TOLERANCE_MM, query]
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()
        witnesses.append({'method': _METHOD, 'source': source, 'frame': _FRAME,
                          'tolerance_mm': _TOLERANCE_MM, 'query': query, 'query_sha256': digest,
                          'evidence': observation})
    result = {'ok': True, 'source': source, 'frame': _FRAME, 'units': 'mm',
              'occurrence_transform_cm': matrix, 'tolerance_mm': _TOLERANCE_MM,
              'limits': _LIMITS, 'coverage': {'queried': len(witnesses), **counts}, 'queries': witnesses}
    if _EXPECTED_SOURCE is not None:
        status = witnesses[0]['evidence']['status']
        result['replay'] = {'status': 'remeasured' if status in ('hits', 'no_hit', 'ambiguous') else 'unavailable'}
    print(json.dumps(result))
'''


def build_ray_collision_with_mesh(
    mesh_handle: str,
    origin_mm: list[float] | None = None,
    direction: list[float] | None = None,
    queries: list[dict] | None = None,
    frame: str = "root",
    tolerance_mm: float = _RAY_DEFAULT_TOLERANCE_MM,
    replay: dict | None = None,
) -> str:
    kind = parse_handle(mesh_handle)["kind"]
    if kind not in {"mesh_body", "body"}:
        raise ValueError("mesh_handle must be a mesh_body handle")
    # Older callers supplied mesh tokens using the generic body prefix.
    if kind == "body":
        mesh_handle = "mesh_body:" + mesh_handle.split(":", 1)[1]
    if queries is not None or replay is not None:
        if origin_mm is not None or direction is not None:
            raise ValueError("supply either origin_mm and direction or a bounded batch or replay")
        return _build_batch_rays(mesh_handle, queries, frame, tolerance_mm, replay)
    if frame != "root" or tolerance_mm != _RAY_DEFAULT_TOLERANCE_MM:
        raise ValueError("frame and tolerance_mm apply to bounded queries")
    ox, oy, oz = (v / 10 for v in _ray_vector(origin_mm, "origin_mm"))
    dx, dy, dz = _ray_vector(direction, "direction")
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
    origin_mm: list[float] | None = None,
    direction: list[float] | None = None,
    queries: list[dict] | None = None,
    frame: str = "root",
    tolerance_mm: float = _RAY_DEFAULT_TOLERANCE_MM,
    replay: dict | None = None,
) -> Envelope:
    if (queries is None and origin_mm is None and direction is None
            and isinstance(replay, dict) and isinstance(replay.get("method"), str)
            and replay["method"] != _RAY_METHOD):
        return Envelope(ok=True, result={"ok": True, "replay": {"status": "unavailable", "reason": "witness_method_unavailable"}})
    try:
        script = build_ray_collision_with_mesh(mesh_handle, origin_mm, direction, queries, frame, tolerance_mm, replay)
    except ValueError as e:
        return Envelope(ok=False, error="invalid_input", message=str(e))
    env = _ok_runner(adapter, script, "ray_collision_with_mesh", read_only=True)
    if env.ok and (queries is not None or replay is not None):
        env.message = ""
    return env


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
