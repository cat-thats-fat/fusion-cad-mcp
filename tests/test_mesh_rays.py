"""Exercise generated ray scripts using finite, separated plane patches."""

from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.tools.handle_tools import ray_collision_with_mesh
from tests._assembly_fakes import (
    Collection,
    NoScriptAdapter,
    ScriptAdapter,
    occurrence,
    point,
    root_with,
)

_IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
_ROTATED_POSE = [0, 0, 1, 4, 0, 1, 0, 5, -1, 0, 0, 6, 0, 0, 0, 1]


class _Matrix:
    def __init__(self, cells=_IDENTITY):
        self.cells = list(cells)

    def asArray(self):
        return self.cells

    def setWithArray(self, cells):
        self.cells = list(cells)
        return True

    def copy(self):
        return _Matrix(self.cells)

    def invert(self):
        previous = self.cells.copy()
        for row in range(3):
            for col in range(3):
                self.cells[row * 4 + col] = previous[col * 4 + row]
            self.cells[row * 4 + 3] = -sum(self.cells[row * 4 + i] * previous[i * 4 + 3] for i in range(3))
        return True


class _Coordinates:
    def __init__(self, x, y, z, *, vector=False):
        self.x, self.y, self.z = x, y, z
        self.vector = vector

    def transformBy(self, matrix):
        values = (self.x, self.y, self.z, 0 if self.vector else 1)
        self.x, self.y, self.z = (sum(matrix.cells[row * 4 + col] * values[col] for col in range(4))
                                 for row in range(3))
        return True


def _mesh_adapter(monkeypatch, *, planes=(3, 1), nested=False):
    # One synthetic indexed triangle per patch. The collision double only models
    # the rectangular interior used by the queries below.
    coordinates = [value for z in planes for value in (-1, -1, z, 1, -1, z, 0, 1, z)]
    indices = list(range(len(planes) * 3))
    mesh_data = NS(nodeCoordinatesAsDouble=coordinates, triangleNodeIndices=indices,
                   nodeCount=len(indices), triangleCount=len(planes), quadNodeIndices=[],
                   polygonNodeIndices=[], nodeCountPerPolygon=[], quadCount=0, polygonCount=0)
    component = NS(name="Leaf") if nested else root_with()

    def intersect(origin, direction):
        if direction.z == 0:
            # A native point return on a grazing/boundary query carries no normals.
            if origin.z in planes and origin.y == 1 and direction.x > 0 and origin.x < 0:
                return [_Coordinates(0, 1, origin.z)]
            return []
        result = []
        for z in planes:
            distance = (z - origin.z) / direction.z
            x = origin.x + distance * direction.x
            y = origin.y + distance * direction.y
            if abs(x) <= 1 and abs(y) <= 1:
                # Include behind-origin hits to exercise our interval check.
                result.append(_Coordinates(x, y, z))
        return result

    native = NS(name="Scan", objectType="adsk::fusion::MeshBody", isValid=True, entityToken="tok",
                nativeObject=None, assemblyContext=None, parentComponent=component,
                mesh=mesh_data, displayMesh=NS(triangleCount=len(planes)),
                boundingBox=NS(minPoint=point(-1, -1, min(planes)), maxPoint=point(1, 1, max(planes))),
                calculateCollisionsWithRay=Mock(side_effect=intersect))
    component.meshBodies = Collection([native])
    scope = ""
    occ = None
    if nested:
        occ = occurrence("Parent:1+Leaf:2", component=component, transform2=_Matrix(_ROTATED_POSE))
        proxy = NS(objectType=native.objectType, isValid=True, nativeObject=native, assemblyContext=occ)
        native.createForAssemblyContext = Mock(return_value=proxy)
        root = root_with(occurrences=[occ])
        scope = f"|occ={occ.fullPathName}"
    else:
        root = component
    lookup = Mock(side_effect=lambda token: [native] if token in ("tok", "fresh") else [])
    doc = NS(creationId="doc", dataFile=None)
    adapter = ScriptAdapter(
        monkeypatch, NS(rootComponent=root, findEntityByToken=lookup),
        app=NS(activeDocument=doc, documents=Collection([doc])),
        core={"Point3D": NS(create=_Coordinates), "Matrix3D": NS(create=_Matrix),
              "Vector3D": NS(create=lambda *values: _Coordinates(*values, vector=True))},
    )
    adapter.mesh_lookup = lookup
    return adapter, native, f"mesh_body:Scan{scope}|doc=646f63:tok", occ


def _query(**options):
    return {"origin_mm": [0, 0, 0], "direction": [0, 0, 5], "start_mm": 0, "end_mm": 40, **options}


def test_batch_mesh_rays_sort_clip_and_report_gaps_and_selections(monkeypatch):
    adapter, native, handle, _ = _mesh_adapter(monkeypatch, planes=(3, -2, 1, 5))
    queries = [_query(selection="nearest"), _query(selection="farthest"),
               _query(start_mm=10, end_mm=30), _query(start_mm=11, end_mm=30),
               _query(origin_mm=[20, 0, 0])]

    env = ray_collision_with_mesh(adapter, handle, queries=queries)

    assert env.ok and env.message == ""
    assert env.result["coverage"] == {"queried": 5, "hits": 4, "no_hit": 1, "ambiguous": 0,
                                      "unavailable": 0, "truncated": 0}
    observations = [row["evidence"] for row in env.result["queries"]]
    assert [[hit["travel_mm"] for hit in row["hits"]] for row in observations] == [[10, 30], [10, 30], [10, 30], [30], []]
    assert [row["selected_hit_indices"] for row in observations] == [[0], [1], [0, 1], [0], []]
    assert observations[0]["hits"][1]["point_mm"] == [0, 0, 30]
    assert all(call.args[1].z == 1 for call in native.calculateCollisionsWithRay.call_args_list)
    assert env.result["units"] == "mm"


def test_batch_mesh_rays_preserve_tangent_unknown_and_coincident_ambiguity(monkeypatch):
    adapter, _, handle, _ = _mesh_adapter(monkeypatch, planes=(1, 1 + 1e-8, 3))

    result = ray_collision_with_mesh(adapter, handle, queries=[
        _query(selection="nearest"),
        _query(origin_mm=[-10, 10, 10], direction=[1, 0, 0]),
    ]).result

    coincident, tangent = [row["evidence"] for row in result["queries"]]
    assert coincident["status"] == "ambiguous" and coincident["selected_hit_indices"] == []
    assert [hit["coincident_returns"] for hit in coincident["hits"]] == [2, 1]
    assert tangent["status"] == "hits" and tangent["hits"][0]["point_mm"] == [0, 10, 10]
    assert tangent["contact_classification"] == "unavailable"


def test_nested_batch_cast_uses_native_mesh_and_root_occurrence_pose(monkeypatch):
    adapter, native, handle, occ = _mesh_adapter(monkeypatch, nested=True)
    root_query = _query(origin_mm=[40, 50, 60], direction=[100, 0, 0])

    root = ray_collision_with_mesh(adapter, handle, queries=[root_query]).result
    local = ray_collision_with_mesh(adapter, handle, queries=[_query()], frame="local").result

    assert root["occurrence_transform_cm"] == _ROTATED_POSE
    assert root["source"]["occurrence_path"] == occ.fullPathName
    assert root["source"] == local["source"]
    assert [hit["point_mm"] for hit in root["queries"][0]["evidence"]["hits"]] == [[50, 50, 60], [70, 50, 60]]
    assert [hit["point_mm"] for hit in local["queries"][0]["evidence"]["hits"]] == [[0, 0, 10], [0, 0, 30]]
    for call in native.calculateCollisionsWithRay.call_args_list:
        origin, direction = call.args
        assert (origin.x, origin.y, origin.z) == (0, 0, 0)
        assert (direction.x, direction.y, direction.z) == (0, 0, 1)


def test_ray_replay_reruns_retained_uncovered_query_and_invalidates_changed_pose(monkeypatch):
    adapter, native, handle, occ = _mesh_adapter(monkeypatch, nested=True)
    query = _query(origin_mm=[40, 70, 60], direction=[1, 0, 0])
    first = ray_collision_with_mesh(adapter, handle, queries=[query]).result["queries"][0]
    assert first["evidence"]["status"] == "no_hit"

    rechecked = ray_collision_with_mesh(adapter, handle.replace(":tok", ":fresh"), replay=first).result
    assert rechecked["replay"]["status"] == "remeasured"
    assert rechecked["queries"][0] == first
    assert native.calculateCollisionsWithRay.call_count == 2

    occ.transform2.cells[7] += 2
    stale = ray_collision_with_mesh(adapter, handle, replay=first).result
    assert stale["replay"] == {"status": "stale", "reason": "source_changed", "differences": ["transform_sha256"]}
    assert native.calculateCollisionsWithRay.call_count == 2
    assert ray_collision_with_mesh(adapter, handle, queries=[query]).result["coverage"]["hits"] == 1


def test_ray_replay_rejects_changed_query_and_geometry_and_missing_handle(monkeypatch):
    adapter, native, handle, _ = _mesh_adapter(monkeypatch)
    first = ray_collision_with_mesh(adapter, handle, queries=[_query()]).result["queries"][0]

    altered = deepcopy(first)
    altered["query"]["origin_mm"][0] = 100
    invalid = ray_collision_with_mesh(NoScriptAdapter(), handle, replay=altered)
    assert invalid.error == "invalid_input"

    native.mesh.nodeCoordinatesAsDouble[0] = -1.5
    stale = ray_collision_with_mesh(adapter, handle, replay=first).result
    assert stale["replay"]["differences"] == ["geometry_sha256"]
    missing = ray_collision_with_mesh(adapter, handle.replace(":tok", ":gone"), replay=first).result
    assert missing["replay"]["status"] == "stale"
    assert missing["replay"]["reason"] == "mesh_handle_invalid"
    native.calculateCollisionsWithRay.assert_called_once()


def test_ray_replay_rejects_identical_replacement_mesh(monkeypatch):
    adapter, native, handle, _ = _mesh_adapter(monkeypatch)
    first = ray_collision_with_mesh(adapter, handle, queries=[_query()]).result["queries"][0]
    replacement = NS(**vars(native))
    replacement.entityToken = "fresh"
    replacement.calculateCollisionsWithRay = Mock(side_effect=native.calculateCollisionsWithRay.side_effect)
    native.parentComponent.meshBodies.items[:] = [replacement]
    # Give the fake lookup the replacement object under the new handle. The
    # mesh name, geometry, document, and placement are unchanged.
    adapter.mesh_lookup.side_effect = lambda token: [replacement] if token == "fresh" else []

    stale = ray_collision_with_mesh(adapter, handle.replace(":tok", ":fresh"), replay=first).result

    assert stale["replay"] == {
        "status": "stale", "reason": "source_changed", "differences": ["entity_token_sha256"],
    }
    replacement.calculateCollisionsWithRay.assert_not_called()


def test_non_ray_witness_does_not_dispatch_or_certify_revalidation():
    result = ray_collision_with_mesh(NoScriptAdapter(), "mesh_body:Scan|doc=646f63:tok",
                                     replay={"method": "closest_point", "distance_mm": 5}).result
    assert result["replay"] == {"status": "unavailable", "reason": "witness_method_unavailable"}


def test_batch_mesh_rays_bound_returned_hits_and_reject_over_limit_meshes(monkeypatch):
    adapter, native, handle, _ = _mesh_adapter(monkeypatch, planes=tuple(range(50, 0, -1)))
    query = _query(end_mm=501, selection="farthest")

    result = ray_collision_with_mesh(adapter, handle, queries=[query]).result
    row = result["queries"][0]["evidence"]
    assert result["coverage"]["truncated"] == 1
    assert row["status"] == "truncated" and row["reason"] == "reported_hit_limit"
    assert len(row["hits"]) == 32 and row["selected_hit_indices"] == []
    assert row["hits"][-1]["travel_mm"] == 320

    native.calculateCollisionsWithRay.side_effect = None
    native.calculateCollisionsWithRay.return_value = [object()] * 4097
    result = ray_collision_with_mesh(adapter, handle, queries=[query]).result
    assert result["queries"][0]["evidence"]["reason"] == "native_hit_limit"

    native.displayMesh.triangleCount = 1_000_001
    env = ray_collision_with_mesh(adapter, handle, queries=[query])
    assert not env.ok and env.error == "mesh_source_unavailable"
    assert "work limits" in env.result["detail"]
    assert native.calculateCollisionsWithRay.call_count == 2


def test_batch_mesh_rays_report_native_failures_and_refuse_ambiguous_mesh_name(monkeypatch):
    adapter, native, handle, _ = _mesh_adapter(monkeypatch, planes=(1,))
    native.calculateCollisionsWithRay.side_effect = RuntimeError("ray failed")

    result = ray_collision_with_mesh(adapter, handle, queries=[_query()]).result
    assert result["coverage"]["unavailable"] == 1
    assert result["queries"][0]["evidence"]["reason"] == "ray failed"

    native.parentComponent.meshBodies.add(NS(name="Scan"))
    env = ray_collision_with_mesh(adapter, handle, queries=[_query()])
    assert env.error == "mesh_source_unavailable"
    assert "ambiguous" in env.result["detail"]
    native.calculateCollisionsWithRay.assert_called_once()


@pytest.mark.parametrize("options", [
    {"queries": []}, {"queries": [_query()] * 33}, {"queries": [_query(direction=[0, 0, 0])]},
    {"queries": [_query(direction=[0, 0, True])]}, {"queries": [_query(direction=[0, 0, float("nan")])]},
    {"queries": [_query(origin_mm=[0, 0, 10**1000])]}, {"queries": [_query(start_mm=-1)]},
    {"queries": [_query(start_mm=40)]}, {"queries": [_query(end_mm=float("inf"))]},
    {"queries": [_query(selection="automatic")]}, {"queries": [_query()], "frame": "world"},
    {"queries": [_query()], "tolerance_mm": -1}, {"queries": [_query()], "origin_mm": [0, 0, 0]},
])
def test_invalid_batch_mesh_rays_do_not_dispatch(options):
    env = ray_collision_with_mesh(NoScriptAdapter(), "mesh_body:Scan|doc=646f63:tok", **options)
    assert env.error == "invalid_input"
