"""Occurrence and document identity contracts exercised with local API doubles."""

import json
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.handles import emit_resolve, parse_handle
from fusion_cad_mcp.resolver import RESOLVER_SCRIPT
from fusion_cad_mcp.tools import handle_tools, verify
from tests._assembly_fakes import Collection, ScriptAdapter, occurrence, point, root_with


def test_occurrence_paths_and_ambiguity():
    helpers = {}
    exec(RESOLVER_SCRIPT, helpers)
    first_body = SimpleNamespace(name="Body")
    second_body = SimpleNamespace(name="Body")
    first = occurrence("Part:1", bodies=[first_body])
    second = occurrence("Part:2", bodies=[second_body])
    root = root_with(occurrences=[first, second])

    body, error = helpers["_select_body"](root, "Body")
    assert body is None
    assert error["error"] == "body_ambiguous"
    assert error["matches"] == ["Part:1 / Body", "Part:2 / Body"]
    selected, error = helpers["_select_body"](root, "Part:2 / Body")
    assert selected is second_body
    assert error is None

    selected, error = helpers["_select_occurrence"](root, "Part")
    assert selected is None
    assert error["error"] == "occurrence_ambiguous"
    assert error["matches"] == ["Part:1", "Part:2"]
    assert helpers["_select_occurrence"](root, "Part:1")[0] is first


@pytest.mark.parametrize("existing_proxy", [False, True], ids=["create_proxy", "use_existing_proxy"])
def test_measure_restores_or_retains_selected_assembly_context(monkeypatch, existing_proxy):
    occ1 = occurrence("Parent:1+Leaf:1")
    occ2 = occurrence("Parent:2+Leaf:1")
    root = root_with(occurrences=[occ1, occ2])
    face_type = "adsk::fusion::BRepFace"
    first_proxy = SimpleNamespace(objectType=face_type, assemblyContext=occ1)
    second_proxy = SimpleNamespace(objectType=face_type, assemblyContext=occ2)
    proxies = {id(occ1): first_proxy, id(occ2): second_proxy}
    native = SimpleNamespace(
        objectType=face_type, createForAssemblyContext=Mock(side_effect=lambda occ: proxies[id(occ)])
    )
    matches = {"a": [first_proxy if existing_proxy else native], "b": [native]}
    design = SimpleNamespace(rootComponent=root, findEntityByToken=matches.__getitem__)
    measure = Mock(return_value=SimpleNamespace(value=3.5))
    app = SimpleNamespace(measureManager=SimpleNamespace(measureMinimumDistance=measure))
    handles = [
        "face:Parent:1+Leaf:1 / Body/face[0]|occ=Parent:1+Leaf:1:a",
        "face:Parent:2+Leaf:1 / Body/face[0]|occ=Parent:2+Leaf:1:b",
    ]

    assert parse_handle(handles[1])["path"].endswith("|occ=Parent:2+Leaf:1")
    env = handle_tools.measure(ScriptAdapter(monkeypatch, design, app=app), *handles, "min_distance")

    assert env.ok is True
    assert env.result["value_mm"] == 35
    measure.assert_called_once_with(first_proxy, second_proxy)
    assert [call.args[0] for call in native.createForAssemblyContext.call_args_list] == (
        [occ2] if existing_proxy else [occ1, occ2]
    )


def test_measure_rejects_token_proxy_in_another_occurrence(monkeypatch):
    occ1 = occurrence("Part:1")
    occ2 = occurrence("Part:2")
    entity = SimpleNamespace(objectType="adsk::fusion::BRepFace", assemblyContext=occ1)
    lookup = Mock(return_value=[entity])
    design = SimpleNamespace(rootComponent=root_with(occurrences=[occ1, occ2]), findEntityByToken=lookup)
    adapter = ScriptAdapter(monkeypatch, design)
    handle = "face:Part:2 / Body/face[0]|occ=Part:2:token"

    env = handle_tools.measure(adapter, handle, "face::unused")

    assert env.ok is False
    assert env.error == "handle_invalid"
    assert env.result["detail"]["reason"] == "occurrence_mismatch"
    lookup.assert_called_once_with("token")


def _scoped_resolver(doc, design, handle):
    app = SimpleNamespace(activeDocument=doc, documents=Collection([doc]))
    adsk = SimpleNamespace(core=SimpleNamespace(Application=SimpleNamespace(get=lambda: app)))
    namespace = {"adsk": adsk, "design": design, "json": json}
    source = textwrap.indent(emit_resolve(handle) + "\nreturn _entity\n", "    ")
    exec("def resolve():\n" + source, namespace)
    return app, namespace["resolve"]


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("creation", "document_scope_mismatch"),
        ("file", "document_scope_mismatch"),
        ("duplicate_doc", "document_scope_ambiguous_or_unavailable"),
        ("duplicate_entity", "ambiguous_token"),
        ("entity_type", "wrong_type"),
    ],
)
def test_scoped_handle_rejects_other_or_duplicate_documents_and_token_matches(capsys, case, reason):
    doc = SimpleNamespace(creationId="doc-a", dataFile=SimpleNamespace(id="file-a"))
    entity = SimpleNamespace(objectType="adsk::fusion::BRepFace")
    lookup = Mock(return_value=[entity])
    design = SimpleNamespace(findEntityByToken=lookup)
    key, file_key = b"doc-a".hex(), b"file-a".hex()
    app, resolve = _scoped_resolver(doc, design, f"face:part|doc={key}|file={file_key}:token")

    assert resolve() is entity
    lookup.assert_called_once_with("token")
    lookup.reset_mock()
    if case == "creation":
        app.activeDocument = SimpleNamespace(creationId="doc-b", dataFile=doc.dataFile)
    elif case == "file":
        app.activeDocument = SimpleNamespace(creationId="doc-a", dataFile=SimpleNamespace(id="file-b"))
    elif case == "duplicate_doc":
        app.documents.add(SimpleNamespace(creationId="doc-a", dataFile=doc.dataFile))
    elif case == "duplicate_entity":
        lookup.return_value = [entity, entity]
    else:
        lookup.return_value = [SimpleNamespace(objectType="adsk::fusion::BRepEdge")]

    assert resolve() is None
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"] == "handle_invalid"
    assert result["detail"]["reason"] == reason
    if case in ("creation", "file", "duplicate_doc"):
        lookup.assert_not_called()
    else:
        lookup.assert_called_once_with("token")


def test_unsaved_scope_still_resolves_after_initial_save():
    doc = SimpleNamespace(creationId="new-doc", dataFile=None)
    entity = SimpleNamespace(objectType="adsk::fusion::BRepFace")
    design = SimpleNamespace(findEntityByToken=lambda token: [entity])
    handle = f"face:part|doc={b'new-doc'.hex()}:token"
    _, resolve = _scoped_resolver(doc, design, handle)

    assert resolve() is entity
    doc.dataFile = SimpleNamespace(id="new-file")
    assert resolve() is entity


def test_nested_occurrence_proxies_are_distinct_instances():
    helpers = {}
    exec(RESOLVER_SCRIPT, helpers)
    shared = SimpleNamespace(name="Leaf")
    bodies = [SimpleNamespace(name="Body") for _ in range(2)]
    root = root_with(occurrences=[
        occurrence(f"Parent:{index}+Leaf:1", component=shared, bodies=[body])
        for index, body in enumerate(bodies, start=1)
    ])

    assert helpers["_select_body"](root, "Parent:1+Leaf:1 / Body")[0] is bodies[0]
    assert helpers["_select_body"](root, "Parent:2+Leaf:1 / Body")[0] is bodies[1]
    assert helpers["_select_body"](root, "Body")[1]["matches"] == [
        "Parent:1+Leaf:1 / Body", "Parent:2+Leaf:1 / Body",
    ]


def test_measurements_use_each_occurrence_proxy(monkeypatch):
    bodies = [
        SimpleNamespace(
            name="Body",
            boundingBox=SimpleNamespace(minPoint=point(x, 1, -2), maxPoint=point(x + 1, 4, 2)),
            physicalProperties=SimpleNamespace(centerOfMass=point(x + 0.5, 2.5, -0.1)),
        )
        for x in (0, 10)
    ]
    root = root_with(occurrences=[
        occurrence(f"Part:{index}+Leaf:1", bodies=[body])
        for index, body in enumerate(bodies, start=1)
    ])
    adapter = ScriptAdapter(monkeypatch, SimpleNamespace(rootComponent=root))
    bbox = verify.bounding_box(adapter)
    center = verify.center_of_mass(adapter)
    ambiguous = verify.bounding_box(adapter, "Body")

    assert bbox.ok is True
    assert [row["bbox_mm"]["min"] for row in bbox.result["bodies"]] == [[0, 10, -20], [100, 10, -20]]
    assert center.ok is True
    assert [(row["path"], row["center_of_mass_mm"]) for row in center.result["bodies"]] == [
        ("Part:1+Leaf:1 / Body", [5, 25, -1]),
        ("Part:2+Leaf:1 / Body", [105, 25, -1]),
    ]
    assert ambiguous.error == "body_ambiguous"
    assert ambiguous.result["matches"] == ["Part:1+Leaf:1 / Body", "Part:2+Leaf:1 / Body"]


def test_health_reports_suppressed_rolled_back_and_unknown(monkeypatch):
    entities = [
        SimpleNamespace(name=f"f{i}", healthState=i, errorOrWarningMessage="", isSuppressed=i == 3)
        for i in range(6)
    ]
    root = root_with(name="Root", features=Collection(entities[:5]), sketches=Collection(entities[5:]))
    design = SimpleNamespace(rootComponent=root, computeAll=lambda: True)
    env = verify.audit_feature_health(ScriptAdapter(monkeypatch, design))

    assert env.ok is False
    assert env.error == "health_unknown"
    assert env.result["summary"] == {
        "total": 6, "healthy": 1, "warning": 1, "failed": 1,
        "suppressed": 1, "rolled_back": 1, "unknown": 1,
    }
    assert [(row["category"], row["healthLabel"]) for row in env.result["features"]][-3:] == [
        ("feature", "suppressed"), ("feature", "rolled_back"), ("sketch", "unknown"),
    ]
