"""Handle-consuming scripts executed with a deliberately small Fusion API double."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.handles import parse_handle
from fusion_cad_mcp.tools import handle_tools as ht
from tests._assembly_fakes import (
    Collection,
    NoScriptAdapter,
    ScriptAdapter,
    occurrence,
    point,
    root_with,
)


def _app():
    doc = SimpleNamespace(creationId="doc", dataFile=None)
    return SimpleNamespace(activeDocument=doc, documents=Collection([doc]))


def _edge(token, start, end, *, length, linear=True):
    return SimpleNamespace(
        entityToken=token, geometry=SimpleNamespace(linear=linear), length=length,
        startVertex=SimpleNamespace(geometry=start), endVertex=SimpleNamespace(geometry=end),
    )


def _face(token, normal, *, success=True):
    location = point(1, -2, 0.5)
    return SimpleNamespace(
        entityToken=token, pointOnFace=location,
        evaluator=SimpleNamespace(getNormalAtPoint=Mock(return_value=(success, normal))),
    )


def _entities_adapter(monkeypatch, *, faces=(), edges=(), vertices=()):
    occ = occurrence("Parent:1+Plate:2")
    body = SimpleNamespace(
        name="Plate", assemblyContext=occ, faces=Collection(faces),
        edges=Collection(edges), vertices=Collection(vertices),
    )
    occ.bRepBodies.add(body)
    return ScriptAdapter(
        monkeypatch,
        SimpleNamespace(rootComponent=root_with(occurrences=[occ])),
        app=_app(),
        core={"Line3D": SimpleNamespace(cast=lambda geometry: geometry if geometry.linear else None)},
    )


def test_list_body_entities_all_kinds(monkeypatch):
    face = _face("f0", point(z=1))
    edge = _edge("e0", point(), point(0, 0, 2), length=2)
    vertex = SimpleNamespace(entityToken="v0", geometry=point(-1, 2, 3))
    adapter = _entities_adapter(monkeypatch, faces=[face], edges=[edge], vertices=[vertex])
    path = "Parent:1+Plate:2 / Plate"

    env = ht.list_body_entities(adapter, path)

    assert env.ok is True
    assert [env.result[f"{kind}_count"] for kind in ("face", "edge", "vertex")] == [1, 1, 1]
    for collection, kind, token, field, value in [
        ("faces", "face", "f0", "pointOnFace_mm", [10, -20, 5]),
        ("edges", "edge", "e0", "length_mm", 20),
        ("vertices", "vertex", "v0", "position_mm", [-10, 20, 30]),
    ]:
        row, = env.result[collection]
        assert row[field] == value
        assert row["index"] == 0
        assert parse_handle(row["handle"]) == {
            "kind": kind,
            "path": f"{path}/{kind}[0]|occ=Parent:1+Plate:2|doc=646f63",
            "token": token,
        }


def test_list_body_entities_face_normal_filter(monkeypatch):
    faces = [
        _face("side", point(x=1)),
        _face("top", point(z=1)),
        _face("bottom", point(z=-1)),
        _face("unreadable", None, success=False),
    ]
    adapter = _entities_adapter(monkeypatch, faces=faces)

    env = ht.list_body_entities(adapter, "Plate", kinds=["face"], face_normal_filter=[0, 0, 1])

    assert env.ok is True
    assert [parse_handle(row["handle"])["token"] for row in env.result["faces"]] == ["top"]
    for face in faces:
        face.evaluator.getNormalAtPoint.assert_called_once_with(face.pointOnFace)


def test_list_body_entities_edge_parallel_z_filter(monkeypatch):
    edges = [
        _edge("parallel", point(1, 2, 3), point(1, 2, 2), length=1),
        _edge("diagonal", point(1, 2, 3), point(1, 0, 1), length=2.8),
        _edge("arc", point(1, 2, 3), point(1, 2, 2), length=1.5, linear=False),
    ]
    adapter = _entities_adapter(monkeypatch, edges=edges)

    env = ht.list_body_entities(adapter, "Plate", kinds=["edge"], edge_parallel_to="z")

    assert env.ok is True
    assert [parse_handle(row["handle"])["token"] for row in env.result["edges"]] == ["parallel"]


def test_list_body_entities_min_edge_length_converts_mm_to_cm(monkeypatch):
    short = _edge("short", point(), point(0, 0, 0.2), length=0.2)
    boundary = _edge("boundary", point(), point(0, 0, 0.3), length=0.3)
    circle = SimpleNamespace(entityToken="circle", length=2.5)
    adapter = _entities_adapter(monkeypatch, edges=[short, boundary, circle])

    env = ht.list_body_entities(adapter, "Plate", kinds=["edge"], min_edge_length_mm=3)

    assert env.ok is True
    assert [
        (parse_handle(row["handle"])["token"], row["length_mm"]) for row in env.result["edges"]
    ] == [("boundary", 3), ("circle", 25)]


def test_list_body_entities_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown entity kind 'surface'"):
        ht.build_list_body_entities("plate", kinds=["surface"])


@pytest.mark.parametrize(
    ("kind", "method", "measurement", "expected"),
    [
        ("distance", "measureMinimumDistance",
         SimpleNamespace(value=0.75, positionOne=point(1, 2, 3), positionTwo=point(1, 2, 3.75)),
         {"value_cm": 0.75, "value_mm": 7.5, "point_a_cm": [1, 2, 3], "point_b_cm": [1, 2, 3.75]}),
        ("min_distance", "measureMinimumDistance", SimpleNamespace(value=0.75, positionOne=point(), positionTwo=point(0.75)),
         {"value_cm": 0.75, "value_mm": 7.5}),
        ("angle", "measureAngle", SimpleNamespace(value=math.pi / 2),
         {"value_radians": math.pi / 2, "value_degrees": 90}),
    ],
    ids=["distance", "min_distance", "angle"],
)
def test_measure_dispatches_resolved_entities_and_converts_result(
    monkeypatch, kind, method, measurement, expected
):
    entities = {token: SimpleNamespace(objectType="adsk::fusion::BRepFace", token=token, isPointOnFace=lambda p: True)
                for token in ("tA", "tB")}
    lookup = Mock(side_effect=lambda token: [entities[token]])
    measure = Mock(return_value=measurement)
    adapter = ScriptAdapter(
        monkeypatch, SimpleNamespace(findEntityByToken=lookup),
        app=SimpleNamespace(measureManager=SimpleNamespace(**{method: measure})),
        core={"Point3D": SimpleNamespace(create=point)},
    )

    env = ht.measure(adapter, "face:body/face[0]:tA", "face:body/face[1]:tB", kind=kind)

    assert env.ok is True
    measure.assert_called_once_with(entities["tA"], entities["tB"])
    assert [call.args for call in lookup.call_args_list] == [("tA",), ("tB",)]
    assert {key: env.result[key] for key in ("ok", "kind", *expected)} == {"ok": True, "kind": kind, **expected}
    if kind != "angle":
        assert env.result["distance_evidence"]["witness_consistency"]["status"] == "consistent"


@pytest.mark.parametrize("kind", ["distance", "min_distance"])
@pytest.mark.parametrize("bad_point,expected_status", [(point(), "inconsistent"), (None, "unavailable")])
def test_measure_preserves_scalar_when_witness_cannot_be_used(monkeypatch, kind, bad_point, expected_status):
    entities = {token: SimpleNamespace(objectType="adsk::fusion::BRepFace") for token in ("a", "b")}
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(findEntityByToken=lambda t: [entities[t]]),
                            app=SimpleNamespace(measureManager=SimpleNamespace(measureMinimumDistance=Mock(
                                return_value=SimpleNamespace(value=0.0777739, positionOne=point(), positionTwo=bad_point)))))
    result = ht.measure(adapter, "face:body:a", "face:body:b", kind)
    assert not result.ok and result.error == "measurement_witness_unavailable"
    assert result.result["value_mm"] == pytest.approx(0.777739)
    assert result.result["distance_evidence"]["scalar_valid"]
    assert result.result["distance_evidence"]["witness_consistency"]["status"] == expected_status


@pytest.mark.parametrize("mode", ["exception", "null", "nonfinite"])
def test_measure_preserves_native_call_or_scalar_failure(monkeypatch, mode):
    entities = {token: SimpleNamespace(objectType="adsk::fusion::BRepFace") for token in ("a", "b")}
    native = Mock(side_effect=RuntimeError("native distance failed")) if mode == "exception" else Mock(
        return_value=None if mode == "null" else SimpleNamespace(value=float("nan")))
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(findEntityByToken=lambda t: [entities[t]]),
                            app=SimpleNamespace(measureManager=SimpleNamespace(measureMinimumDistance=native)))
    result = ht.measure(adapter, "face:body:a", "face:body:b", "min_distance")
    assert not result.ok and result.error == "measurement_unavailable"
    if mode == "exception":
        assert result.result["detail"] == "native distance failed"
    else:
        assert result.result["value_mm"] is None
        assert result.result["distance_evidence"]["witness_consistency"]["issues"]


@pytest.mark.parametrize("kind", ["distance", "min_distance"])
@pytest.mark.parametrize("bad_entity", ["a", "b"])
def test_measure_rejects_consistent_witness_inside_trimmed_face_void(monkeypatch, kind, bad_entity):
    query = point(1, 1.5, 1)
    def membership(p, *, hole):
        on_patch = abs(p.z - 1) < 1e-7 and 0 <= p.x <= 2 and 0 <= p.y <= 3
        in_void = hole and 0.95 < p.x < 1.05 and 1.45 < p.y < 1.55
        return on_patch and not in_void

    entities = {
        token: SimpleNamespace(objectType="adsk::fusion::BRepFace",
                               isPointOnFace=Mock(side_effect=lambda p, token=token: membership(p, hole=token == bad_entity)))
        for token in ("a", "b")
    }
    native = Mock(return_value=SimpleNamespace(value=0, positionOne=query, positionTwo=query))
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(findEntityByToken=lambda t: [entities[t]]),
                            app=SimpleNamespace(pointTolerance=1e-7,
                                                measureManager=SimpleNamespace(measureMinimumDistance=native)),
                            core={"Point3D": SimpleNamespace(create=point)})
    result = ht.measure(adapter, "face:top:a", "face:top:b", kind)
    assert not result.ok and result.error == "measurement_witness_unavailable"
    assert result.result["value_mm"] == 0
    evidence = result.result["distance_evidence"]
    assert evidence["witness_consistency"]["status"] == "consistent"
    assert evidence["face_membership"][bad_entity]["status"] == "off_face"
    assert evidence["face_membership"][bad_entity]["tolerance_mm"] == 1e-6
    for entity in entities.values():
        entity.isPointOnFace.assert_called_once_with(query)


@pytest.mark.parametrize("kind", ["distance", "min_distance"])
def test_measure_other_entity_kinds_keep_consistency_contract(monkeypatch, kind):
    entities = {
        "a": SimpleNamespace(objectType="adsk::fusion::BRepBody"),
        "b": SimpleNamespace(objectType="adsk::fusion::BRepEdge"),
    }
    native = Mock(return_value=SimpleNamespace(value=5, positionOne=point(), positionTwo=point(3, 4)))
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(findEntityByToken=lambda t: [entities[t]]),
                            app=SimpleNamespace(measureManager=SimpleNamespace(measureMinimumDistance=native)))
    result = ht.measure(adapter, "body:box:a", "edge:line:b", kind)
    assert result.ok and result.result["value_mm"] == 50
    assert "face_membership" not in result.result["distance_evidence"]


def test_build_measure_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be"):
        ht.build_measure("face:b:t1", "face:b:t2", kind="bogus")


def test_measure_short_circuits_on_bad_handle():
    env = ht.measure(NoScriptAdapter(), "garbage", "face:b:t2")
    assert env.ok is False
    assert env.error == "invalid_input"


def _ray_adapter(monkeypatch, design):
    return ScriptAdapter(
        monkeypatch, design, app=_app(),
        core={"Point3D": SimpleNamespace(create=point), "Vector3D": SimpleNamespace(create=point)},
    )


def test_find_mesh_using_ray_default_root(monkeypatch):
    mesh = SimpleNamespace(
        name="Scanned Part", assemblyContext=None, entityToken="tok",
        objectType="adsk::fusion::MeshBody",
        calculateCollisionsWithRay=Mock(return_value=[point(0, 0, 3.5)]),
    )
    find = Mock(return_value=([mesh], [point(0, 0, 3.5)]))
    root = SimpleNamespace(findMeshUsingRay=find)
    lookup = Mock(return_value=[mesh])
    adapter = _ray_adapter(monkeypatch, SimpleNamespace(rootComponent=root, findEntityByToken=lookup))

    env = ht.find_mesh_using_ray(adapter, [0, 0, 50], [0, 0, -1])

    assert env.ok is True
    find.assert_called_once_with(point(0, 0, 5), point(0, 0, -1))
    assert env.result == {
        "ok": True, "meshes_found": 1, "names": ["Scanned Part"],
        "mesh_handles": ["mesh_body:Scanned Part|doc=646f63:tok"],
    }

    collision = ht.ray_collision_with_mesh(
        adapter, env.result["mesh_handles"][0], [0, 0, 50], [0, 0, -1]
    )
    assert collision.ok is True
    lookup.assert_called_once_with("tok")
    mesh.calculateCollisionsWithRay.assert_called_once_with(point(0, 0, 5), point(0, 0, -1))
    assert collision.result["points_mm"] == [[0, 0, 35]]


def test_find_mesh_using_ray_with_component(monkeypatch):
    component = SimpleNamespace(name="Subassembly")
    selected = occurrence("Subassembly:1", component=component)
    descendant = occurrence("Subassembly:1+Child:1")
    other = occurrence("Subassembly:10", component=component)
    meshes = [
        SimpleNamespace(name=name, assemblyContext=occ, entityToken=token)
        for name, occ, token in [
            ("Root Mesh", None, "a"), ("Wrong Instance", other, "b"),
            ("Selected Mesh", selected, "c"), ("Child Mesh", descendant, "d"),
        ]
    ]
    find = Mock(return_value=(meshes, [point()] * len(meshes)))
    root = root_with(name="Root", occurrences=[selected, descendant, other], findMeshUsingRay=find)
    adapter = _ray_adapter(monkeypatch, SimpleNamespace(rootComponent=root))

    env = ht.find_mesh_using_ray(adapter, [30, 20, 10], [1, 0, 0], component_name="Subassembly:1")

    assert env.ok is True
    find.assert_called_once_with(point(3, 2, 1), point(x=1))
    assert env.result == {
        "ok": True,
        "meshes_found": 2,
        "names": ["Selected Mesh", "Child Mesh"],
        "mesh_handles": [
            "mesh_body:Subassembly:1 / Selected Mesh|occ=Subassembly:1|doc=646f63:c",
            "mesh_body:Subassembly:1+Child:1 / Child Mesh|occ=Subassembly:1+Child:1|doc=646f63:d",
        ],
    }
    find.reset_mock()
    missing = ht.find_mesh_using_ray(adapter, [0, 0, 0], [1, 0, 0], component_name="missing")
    assert missing.error == "component_not_found"
    assert missing.result["name"] == "missing"
    ambiguous = ht.find_mesh_using_ray(adapter, [0, 0, 0], [1, 0, 0], component_name="Subassembly")
    assert ambiguous.error == "component_ambiguous"
    assert ambiguous.result["matches"] == ["Subassembly:1", "Subassembly:10"]
    find.assert_not_called()


def test_find_mesh_using_ray_rejects_bad_origin():
    env = ht.find_mesh_using_ray(NoScriptAdapter(), [0, 0], [0, 0, -1])
    assert env.ok is False
    assert env.error == "invalid_input"


@pytest.mark.parametrize("prefix", ["mesh_body", "body"], ids=["mesh_handle", "legacy_body_prefix"])
def test_ray_collision_resolves_mesh_body_and_converts_hit_points(monkeypatch, prefix):
    collide = Mock(return_value=[point(-1, 2, 0.5), point(-1, 2, -0.5)])
    mesh = SimpleNamespace(objectType="adsk::fusion::MeshBody", calculateCollisionsWithRay=collide)
    lookup = Mock(return_value=[mesh])
    adapter = _ray_adapter(monkeypatch, SimpleNamespace(findEntityByToken=lookup))

    env = ht.ray_collision_with_mesh(adapter, f"{prefix}:Mesh:tok", [-10, 20, 50], [0, 0, -1])

    assert env.ok is True
    lookup.assert_called_once_with("tok")
    collide.assert_called_once_with(point(-1, 2, 5), point(0, 0, -1))
    assert env.result == {"ok": True, "collisions": 2, "points_mm": [[-10, 20, 5], [-10, 20, -5]]}


def test_ray_collision_rejects_bad_handle():
    env = ht.ray_collision_with_mesh(NoScriptAdapter(), "not-a-handle", [0, 0, 0], [0, 0, -1])
    assert env.ok is False
    assert env.error == "invalid_input"


def _edge_adapter(monkeypatch, features=None, sketches=(), occurrences=()):
    edges = {token: SimpleNamespace(objectType="adsk::fusion::BRepEdge", token=token)
             for token in ("t0", "t1", "t2")}
    design = SimpleNamespace(
        rootComponent=root_with(
            features=features, sketches=Collection(sketches), occurrences=occurrences,
        ),
        findEntityByToken=lambda token: [edges[token]],
    )
    adapter = ScriptAdapter(
        monkeypatch, design,
        core={"ObjectCollection": SimpleNamespace(create=Collection),
              "ValueInput": SimpleNamespace(createByString=lambda expr: ("expression", expr))},
    )
    return adapter, edges


def test_build_fillet_edges_resolves_each_handle(monkeypatch):
    fillet_input = SimpleNamespace(addConstantRadiusEdgeSet=Mock())
    fillets = SimpleNamespace(
        createInput=lambda: fillet_input, add=Mock(return_value=SimpleNamespace(name="Fillet1", healthState=0)),
    )
    adapter, edges = _edge_adapter(monkeypatch, SimpleNamespace(filletFeatures=fillets))
    handles = [f"edge:b/edge[{i}]:t{i}" for i in range(3)]

    env = ht.fillet_edges(adapter, handles, "5 mm", is_tangent_chain=False)

    assert env.ok is True
    fillet_input.addConstantRadiusEdgeSet.assert_called_once()
    collection, radius, tangent = fillet_input.addConstantRadiusEdgeSet.call_args.args
    assert collection.items == list(edges.values())
    assert radius == ("expression", "5 mm")
    assert tangent is False
    fillets.add.assert_called_once_with(fillet_input)
    assert env.result["edges_filleted"] == 3


def test_fillet_edges_rejects_empty_list():
    env = ht.fillet_edges(NoScriptAdapter(), [], "5 mm")
    assert env.ok is False
    assert env.error == "invalid_input"


def test_build_chamfer_edges_equal(monkeypatch):
    equal = Mock()
    chamfer_input = SimpleNamespace(
        chamferEdgeSets=SimpleNamespace(addEqualDistanceChamferEdgeSet=equal)
    )
    chamfers = SimpleNamespace(
        createInput2=Mock(return_value=chamfer_input),
        add=Mock(return_value=SimpleNamespace(name="Chamfer1", healthState=0)),
    )
    adapter, edges = _edge_adapter(monkeypatch, SimpleNamespace(chamferFeatures=chamfers))

    env = ht.chamfer_edges(adapter, ["edge:b/edge[0]:t0"], "1 mm", kind="equal")

    assert env.ok is True
    chamfers.createInput2.assert_called_once_with()
    equal.assert_called_once()
    collection, distance, tangent = equal.call_args.args
    assert collection.items == [edges["t0"]]
    assert distance == ("expression", "1 mm")
    assert tangent is False
    chamfers.add.assert_called_once_with(chamfer_input)
    assert env.result["edges_chamfered"] == 1


def test_chamfer_two_dist_requires_distance2():
    with pytest.raises(ValueError, match="requires distance2"):
        ht.build_chamfer_edges(["edge:b/edge[0]:t"], "1 mm", kind="two_dist")


def test_build_project_to_sketch_resolves_entities(monkeypatch):
    project = Mock(return_value=[object(), object(), object()])
    sketch = SimpleNamespace(name="outline", project2=project)
    adapter, edges = _edge_adapter(monkeypatch, sketches=[sketch])

    env = ht.project_to_sketch(adapter, "outline", ["edge:b/edge[0]:t1", "edge:b/edge[1]:t2"])

    assert env.ok is True
    project.assert_called_once_with([edges["t1"], edges["t2"]], True)
    assert env.result["entities_projected"] == 2
    assert env.result["curves_added"] == 3


def test_project_to_sketch_uses_qualified_sketch_proxy(monkeypatch):
    project = Mock(return_value=[object()])
    proxy = SimpleNamespace(project2=project)
    native = SimpleNamespace(name="outline", createForAssemblyContext=Mock(return_value=proxy))
    leaf = SimpleNamespace(name="Leaf", sketches=Collection([native]))
    occ = occurrence("Part:1+Leaf:1", component=leaf)
    decoy = SimpleNamespace(name="outline")
    adapter, edges = _edge_adapter(monkeypatch, sketches=[decoy], occurrences=[occ])

    env = ht.project_to_sketch(adapter, "Part:1+Leaf:1 / outline", ["edge:b/edge[0]:t1"])

    assert env.ok is True
    native.createForAssemblyContext.assert_called_once_with(occ)
    project.assert_called_once_with([edges["t1"]], True)


@pytest.mark.parametrize(
    ("selector", "error"),
    [("outline", "sketch_ambiguous"),
     ("Part:2 / outline", "shared_component_edit_unsupported")],
)
def test_project_to_sketch_refuses_ambiguous_or_shared_targets(monkeypatch, selector, error):
    sketch = SimpleNamespace(name="outline")
    shared = SimpleNamespace(name="Part", sketches=Collection([sketch]))
    instances = [occurrence(f"Part:{i}", component=shared) for i in (1, 2)]
    adapter, _ = _edge_adapter(monkeypatch, occurrences=instances)

    env = ht.project_to_sketch(adapter, selector, ["edge:b/edge[0]:unused"])

    assert env.ok is False
    assert env.error == error
