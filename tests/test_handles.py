"""Handle parsing and execution of emitted resolver fragments."""

import json
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp import handles as h


def test_parse_handle_basic():
    parsed = h.parse_handle("body:plate_v3:abc123")
    assert parsed == {"kind": "body", "path": "plate_v3", "token": "abc123"}


def test_parse_handle_path_can_have_slashes_and_indices():
    parsed = h.parse_handle("edge:Part:1 / plate/edge[7]:xyz==")
    assert parsed == {"kind": "edge", "path": "Part:1 / plate/edge[7]", "token": "xyz=="}


def test_parse_handle_empty_path_ok():
    parsed = h.parse_handle("face::tokenonly")
    assert parsed["path"] == ""
    assert parsed["token"] == "tokenonly"


def test_parse_handle_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown handle kind 'widget'"):
        h.parse_handle("widget:foo:bar")


@pytest.mark.parametrize("handle", ["not_a_handle", "body:no_token", "", "body:plate:"])
def test_parse_handle_rejects_malformed(handle):
    with pytest.raises(ValueError, match="malformed handle"):
        h.parse_handle(handle)


@pytest.mark.parametrize("handle", ["body:plate:tok", "edge:body/edge[0]:tok=="])
def test_is_handle_true_for_well_formed(handle):
    assert h.is_handle(handle) is True


@pytest.mark.parametrize("handle", ["plate_v3", "", None])
def test_is_handle_false_for_garbage(handle):
    assert h.is_handle(handle) is False


def _resolver(fragment, design, *, result="_entity", adsk=None):
    namespace = {"design": design, "json": json, "adsk": adsk}
    source = textwrap.indent(f"{fragment}\nreturn {result}\n", "    ")
    exec(f"def resolve():\n{source}", namespace)
    return namespace["resolve"]


def test_emit_resolve_reports_missing_token_and_stops(capsys):
    token_lookup = Mock(return_value=[])
    design = SimpleNamespace(findEntityByToken=token_lookup)
    handle = "body:plate:missing"

    assert _resolver(h.emit_resolve(handle), design)() is None

    token_lookup.assert_called_once_with("missing")
    assert json.loads(capsys.readouterr().out) == {
        "ok": False, "error": "handle_invalid",
        "detail": {"handle": handle, "reason": "not_found_in_active_doc"},
    }


class IndexableVector:
    """An indexable, non-list sequence like the BaseVector Python binding."""

    def __init__(self, *items):
        self.items = items

    def __len__(self):
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]


def test_emit_resolve_unwraps_basevector():
    face = SimpleNamespace(objectType="adsk::fusion::BRepFace")
    lookup = Mock(return_value=IndexableVector(face))
    design = SimpleNamespace(findEntityByToken=lookup)

    assert _resolver(h.emit_resolve("face:plate:tok"), design)() is face
    lookup.assert_called_once_with("tok")


def test_emit_resolve_many_returns_ordered_entities():
    edges = {
        token: SimpleNamespace(objectType="adsk::fusion::BRepEdge", label=token)
        for token in ("t1", "t2")
    }
    lookup = Mock(side_effect=lambda token: (edges[token],))
    handles = ["edge:body/edge[0]:t2", "edge:body/edge[1]:t1"]

    result = _resolver(
        h.emit_resolve_many(handles, collection_var="selected"),
        SimpleNamespace(findEntityByToken=lookup),
        result="selected",
    )()

    assert result[0] is edges["t2"]
    assert result[1] is edges["t1"]
    assert [call.args for call in lookup.call_args_list] == [("t2",), ("t1",)]


@pytest.mark.parametrize("saved", [False, True], ids=["unsaved", "saved"])
def test_emit_make_handle_builds_runtime_expression(saved):
    file = SimpleNamespace(id="file/id") if saved else None
    document = SimpleNamespace(creationId="doc/id", dataFile=file)
    app = SimpleNamespace(activeDocument=document)
    adsk = SimpleNamespace(core=SimpleNamespace(Application=SimpleNamespace(get=lambda: app)))
    body = SimpleNamespace(name="Part:1 / Body", entityToken="ab+c==")

    handle = eval(h.emit_make_handle("body", "body.name", "body"), {"adsk": adsk, "body": body})

    path = f"Part:1 / Body|doc={document.creationId.encode().hex()}"
    if saved:
        path += f"|file={file.id.encode().hex()}"
    assert h.parse_handle(handle) == {"kind": "body", "path": path, "token": "ab+c=="}


def test_emit_make_handle_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown handle kind 'widget'"):
        h.emit_make_handle("widget", "x.name", "x")
