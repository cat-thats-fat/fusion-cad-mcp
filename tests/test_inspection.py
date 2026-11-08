import contextlib
import io
import json
import sys
import types

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import inspection


def coll(values):
    return types.SimpleNamespace(count=len(values), item=lambda i: values[i])


def point(x, y, z):
    return types.SimpleNamespace(x=x, y=y, z=z)


def execute(monkeypatch, script, root):
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    adsk.core, adsk.fusion = core, fusion
    app = types.SimpleNamespace(
        activeProduct=types.SimpleNamespace(rootComponent=root),
        activeDocument=types.SimpleNamespace(creationId="doc", dataFile=None),
    )
    core.Application = types.SimpleNamespace(get=lambda: app)
    fusion.Design = types.SimpleNamespace(cast=lambda p: p)
    for name, mod in [("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)]:
        monkeypatch.setitem(sys.modules, name, mod)
    ns = {}
    exec(script, ns)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ns["run"](None)
    return json.loads(out.getvalue())


def face(x, radius=1):
    return types.SimpleNamespace(
        pointOnFace=point(x, 0, 0),
        geometry=types.SimpleNamespace(objectType="adsk::core::Cylinder", radius=radius),
        evaluator=types.SimpleNamespace(getNormalAtPoint=lambda p: (True, point(1, 0, 0))),
        entityToken=f"face{x}",
        nativeObject=None,
    )


def fixture():
    b = types.SimpleNamespace(
        name="body",
        faces=coll([face(0), face(1), face(2, 2)]),
        assemblyContext=None,
        boundingBox=types.SimpleNamespace(minPoint=point(0, 0, 0), maxPoint=point(2, 1, 1)),
        entityToken="body-token",
        objectType="adsk::fusion::BRepBody",
    )
    feature = types.SimpleNamespace(
        name="Cut",
        objectType="adsk::fusion::ExtrudeFeature",
        healthState=0,
        errorOrWarningMessage="",
        isParametric=True,
        isSuppressed=False,
        faces=coll([b.faces.item(1)]),
        bodies=coll([b]),
        linkedFeatures=coll([]),
    )
    param = types.SimpleNamespace(createdBy=feature, name="d1", expression="10 mm", unit="mm")
    root = types.SimpleNamespace(
        name="Root",
        bRepBodies=coll([b]),
        allOccurrences=coll([]),
        features=coll([feature]),
        sketches=coll([]),
        joints=coll([]),
        modelParameters=coll([param]),
    )
    b.parentComponent = root
    return root


def test_geometry_predicates_pagination_and_expected_count(monkeypatch):
    root = fixture()
    script = inspection.build_query_geometry(
        "body", "face", {"radius": 10, "normal": [1, 0, 0]}, limit=1, offset=1, expected_count=2
    )
    out = execute(monkeypatch, script, root)
    assert out["ok"] and out["count"] == 2 and len(out["items"]) == 1
    assert out["items"][0]["sample_point_mm"] == [10, 0, 0]
    assert out["items"][0]["handle"].startswith("face:body/face[1]|doc=")
    assert not execute(
        monkeypatch, inspection.build_query_geometry("body", "face", expected_count=2), root
    )["ok"]


@pytest.mark.parametrize("scale", [1e-320, 1e300])
def test_direction_filter_normalizes_finite_extreme_magnitudes(monkeypatch, scale):
    filters = {"normal": [scale, 0, 0]}
    out = execute(monkeypatch, inspection.build_query_geometry("body", "face", filters), fixture())
    assert out["ok"] and out["count"] == 3
    assert filters == {"normal": [scale, 0, 0]}


@pytest.mark.parametrize("filters", [{"radius": 10**1000}, {"normal": [10**1000, 0, 0]}])
def test_geometry_filters_reject_oversized_integers(filters):
    from tests._assembly_fakes import NoScriptAdapter

    assert inspection.query_geometry(NoScriptAdapter(), "body", "face", filters).error == "invalid_input"


def test_missing_entity_token_does_not_erase_paginated_geometry_evidence(monkeypatch):
    root = fixture()
    del root.bRepBodies.item(0).faces.item(0).entityToken
    out = execute(monkeypatch, inspection.build_query_geometry("body", "face", limit=1), root)
    assert out["ok"] and out["count"] == 3
    assert out["items"][0]["handle_unavailable"]
    page = execute(monkeypatch, inspection.build_query_geometry("body", "face", limit=1, offset=1), root)
    assert page["count"] == 3 and page["items"][0]["handle"].startswith("face:body/face[1]")


def test_feature_filter_uses_current_faces(monkeypatch):
    out = execute(
        monkeypatch, inspection.build_query_geometry("body", "face", {"feature": "Cut"}), fixture()
    )
    assert out["count"] == 1 and out["items"][0]["index"] == 1


def test_feature_reports_parameters_and_surviving_faces(monkeypatch):
    out = execute(monkeypatch, inspection.build_inspect_feature("Cut"), fixture())
    assert out["faces"]["count"] == 1
    assert out["parameters"]["items"][0]["expression"] == "10 mm"
    assert out["dependencies"]["available"] is False


def test_assembly_inspection_is_bounded(monkeypatch):
    out = execute(
        monkeypatch, inspection.build_inspect_assembly(include=["bodies"], limit=1), fixture()
    )
    assert out["sections"]["bodies"]["items"][0]["bounding_box_mm"]["max"] == [20, 10, 10]
    assert set(out["sections"]) == {"bodies"}


def test_ambiguous_body_fails(monkeypatch):
    root = fixture()
    root.bRepBodies = coll([root.bRepBodies.item(0), root.bRepBodies.item(0)])
    out = execute(monkeypatch, inspection.build_query_geometry("body", "face"), root)
    assert out["error"] == "body_ambiguous"


def test_wrapper_requests_read_only():
    class Adapter:
        def execute_script(self, script, read_only=False):
            assert read_only
            return Envelope(ok=True, message='{"ok":true,"count":0}')

    assert inspection.query_geometry(Adapter(), "body", "face").ok


@pytest.mark.parametrize(
    "filters", [{"unknown": 1}, {"normal": [0, 0, 0]}, {"radius": float("nan")}, {"length": 2}]
)
def test_rejects_bad_filters(filters):
    with pytest.raises(ValueError):
        inspection.build_query_geometry("body", "face", filters)


def test_feature_zero_is_qualified_as_incomplete_provenance(monkeypatch):
    root = fixture()
    root.features.item(0).faces = coll([])
    result = execute(
        monkeypatch,
        inspection.build_query_geometry("body", "face", {"feature": "Cut"}, expected_count=0),
        root,
    )
    assert result["ok"] and result["count"] == 0
    assert "does not prove" in result["feature_provenance"]


def test_edge_without_vertices_still_reports_sample(monkeypatch):
    root = fixture()
    edge = types.SimpleNamespace(
        startVertex=None,
        endVertex=None,
        pointOnEdge=point(1, 2, 3),
        geometry=types.SimpleNamespace(objectType="adsk::core::Circle3D", radius=1),
        length=6.28,
        entityToken="edge",
        nativeObject=None,
    )
    root.bRepBodies.item(0).edges = coll([edge])
    result = execute(
        monkeypatch, inspection.build_query_geometry("body", "edge", {"near": [10, 20, 30]}), root
    )
    assert result["ok"] and result["count"] == 1
    assert result["items"][0]["start_mm"] is None
    assert result["items"][0]["sample_point_mm"] == [10, 20, 30]


def test_nested_occurrence_hierarchy_paging_and_definition_counts(monkeypatch):
    root = fixture()
    root.bRepBodies = coll([])
    comp_parent = types.SimpleNamespace(
        name="Carrier", bRepBodies=coll([]), sketches=coll([]), features=coll([]), joints=coll([])
    )
    comp_child = types.SimpleNamespace(
        name="Board", bRepBodies=coll([]), sketches=coll([]), features=coll([]), joints=coll([])
    )
    comp_other = types.SimpleNamespace(
        name="Other", bRepBodies=coll([]), sketches=coll([]), features=coll([]), joints=coll([])
    )

    def matrix(n):
        return types.SimpleNamespace(asArray=lambda: [n] * 16)

    def occ(path, component, parent, local, world):
        return types.SimpleNamespace(
            name=path,
            fullPathName=path,
            component=component,
            assemblyContext=parent,
            nativeObject=types.SimpleNamespace(transform2=matrix(local)),
            transform2=matrix(world),
            isGrounded=False,
            isGroundToParent=False,
            isReferencedComponent=False,
            entityToken="tok-" + path,
        )

    parent = occ("Carrier:1", comp_parent, None, 1, 1)
    one = occ("Carrier:1+Board:1", comp_child, parent, 2, 3)
    two = occ("Carrier:1+Board:2", comp_child, parent, 4, 5)
    unrelated = occ("Other:1", comp_other, None, 6, 6)
    root.allOccurrences = coll([parent, one, two, unrelated])
    out = execute(
        monkeypatch,
        inspection.build_inspect_assembly(include=["occurrences"], limit=1, offset=2),
        root,
    )
    section = out["sections"]["occurrences"]
    assert section["count"] == 4 and len(section["items"]) == 1
    assert section["items"][0]["path"] == two.fullPathName
    assert section["items"][0]["parent_path"] == parent.fullPathName
    assert section["items"][0]["definition_occurrence_count"] == 2
    assert section["items"][0]["shared_definition"] is True
    assert section["items"][0]["root_transform"]["matrix_cm"] == [5] * 16
    assert section["items"][0]["local_transform"]["matrix_cm"] == [4] * 16
    assert "|occ=Carrier:1|doc=" in section["items"][0]["handle"]
    subtree = execute(
        monkeypatch, inspection.build_inspect_assembly("Carrier:1", ["occurrences"]), root
    )
    assert [x["path"] for x in subtree["sections"]["occurrences"]["items"]] == [
        one.fullPathName,
        two.fullPathName,
    ]
    assert "selected occurrence excluded" in subtree["sections"]["occurrences"]["scope"]


def test_selected_child_features_use_contextual_tokens(monkeypatch):
    root = fixture()
    root.bRepBodies = coll([])
    feature = types.SimpleNamespace(
        name="Cut",
        healthState=0,
        errorOrWarningMessage="",
        objectType="adsk::fusion::ExtrudeFeature",
        createForAssemblyContext=lambda occ: types.SimpleNamespace(entityToken="proxy-feature"),
    )
    sketch = types.SimpleNamespace(
        name="Sketch",
        healthState=0,
        errorOrWarningMessage="",
        objectType="adsk::fusion::Sketch",
        createForAssemblyContext=lambda occ: types.SimpleNamespace(entityToken="proxy-sketch"),
    )
    component = types.SimpleNamespace(
        name="Child", features=coll([feature]), sketches=coll([sketch])
    )
    occ = types.SimpleNamespace(
        fullPathName="Child:1",
        name="Child:1",
        component=component,
        transform2=types.SimpleNamespace(asArray=lambda: [0] * 16),
    )
    root.allOccurrences = coll([occ])
    out = execute(
        monkeypatch, inspection.build_inspect_assembly("Child:1", ["features", "sketches"]), root
    )
    assert out["sections"]["features"]["items"][0]["selector"] == "Child:1 / Cut"
    assert out["sections"]["features"]["items"][0]["handle"].endswith(":proxy-feature")
    assert out["sections"]["sketches"]["items"][0]["handle"].endswith(":proxy-sketch")
