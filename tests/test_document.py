"""Tier 1c: document/state tools wrap the right MCP calls. No Fusion required."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import document


class FakeAdapter:
    """Records every call so we can assert on the wire payloads."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # ---- mimic the real adapter convenience methods ----

    def read_query(self, query_type: str, **kwargs: Any) -> Envelope:
        self.calls.append(("read_query", {"queryType": query_type, **kwargs}))
        return Envelope(ok=True, message="", result={"ok": True})

    def execute_document_op(self, operation: str, **kwargs: Any) -> Envelope:
        self.calls.append(("execute_document_op", {"operation": operation, **kwargs}))
        return Envelope(ok=True, message="", result={"ok": True})

    def update(self, kind: str) -> Envelope:
        self.calls.append(("update", {"kind": kind}))
        return Envelope(ok=True, message="", result={"ok": True})


def test_list_open_docs_uses_current_documents_operation():
    a = FakeAdapter()
    document.list_open_docs(a)
    assert a.calls == [("read_query", {"queryType": "document", "operation": "open"})]


def test_list_projects_wraps_projects_query():
    a = FakeAdapter()
    document.list_projects(a)
    assert a.calls == [("read_query", {"queryType": "projects"})]


def test_search_docs_includes_project_when_given():
    a = FakeAdapter()
    document.search_docs(a, "bracket", project="agenius3d")
    assert a.calls[0][1] == {
        "queryType": "document",
        "operation": "search",
        "name": "bracket",
        "project": "agenius3d",
    }


def test_search_docs_omits_project_when_none():
    a = FakeAdapter()
    document.search_docs(a, "bracket")
    assert "project" not in a.calls[0][1]


def test_open_doc_by_id_executes_instead_of_listing():
    a = FakeAdapter()
    document.open_doc(a, "", file_id="fixture-id")
    assert a.calls == [("execute_document_op", {"operation": "open", "fileId": "fixture-id"})]


class SearchingAdapter(FakeAdapter):
    def __init__(self, response):
        super().__init__()
        self.response = response

    def read_query(self, query_type, **kwargs):
        super().read_query(query_type, **kwargs)
        return self.response


def test_open_doc_resolves_unique_exact_match():
    a = SearchingAdapter(
        Envelope(
            ok=True,
            result={
                "results": [
                    {"name": "fixture", "id": "exact-id"},
                    {"name": "fixture old", "id": "wrong-id"},
                ]
            },
        )
    )
    result = document.open_doc(a, "fixture", "test-project")
    assert result.ok
    assert a.calls == [
        (
            "read_query",
            {
                "queryType": "document",
                "operation": "search",
                "name": "fixture",
                "project": "test-project",
            },
        ),
        ("execute_document_op", {"operation": "open", "fileId": "exact-id"}),
    ]


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([], "document_not_found"),
        ([{"name": "fixture-old", "id": "wrong"}], "document_not_found"),
        ([{"name": "fixture", "id": "a"}, {"name": "fixture", "id": "b"}], "document_ambiguous"),
        ([{"name": "fixture"}], "document_id_missing"),
        (["invalid"], "document_search_unrecognized"),
        (None, "document_search_unrecognized"),
    ],
)
def test_open_doc_refuses_uncertain_resolution_without_mutation(rows, error):
    a = SearchingAdapter(Envelope(ok=True, result={"results": rows}))
    result = document.open_doc(a, "fixture")
    assert not result.ok and result.error == error
    assert len(a.calls) == 1 and a.calls[0][0] == "read_query"


def test_open_doc_propagates_search_error_without_opening():
    error = Envelope(ok=False, error="offline")
    a = SearchingAdapter(error)
    assert document.open_doc(a, "fixture") is error
    assert len(a.calls) == 1


def test_open_doc_blank_name_never_searches():
    a = FakeAdapter()
    assert document.open_doc(a, " ").error == "invalid_name"
    assert not a.calls


def test_save_as_refuses_undocumented_path_parameter():
    a = FakeAdapter()
    result = document.save_as(a, "/fixture/design.f3d")
    assert not result.ok and result.error == "save_as_unsupported"
    assert not a.calls


def test_save_passes_no_extras():
    a = FakeAdapter()
    document.save(a)
    assert a.calls == [("execute_document_op", {"operation": "save"})]


def test_close_with_save_sets_userConfirmedSaveAndClose():
    a = FakeAdapter()
    document.close(a, confirm="save")
    assert a.calls[0][1] == {"operation": "close", "userConfirmedSaveAndClose": True}


def test_close_with_discard_sets_userConfirmedCloseWithoutSave():
    a = FakeAdapter()
    document.close(a, confirm="discard")
    assert a.calls[0][1] == {"operation": "close", "userConfirmedCloseWithoutSave": True}


def test_close_with_prompt_sets_no_flags():
    a = FakeAdapter()
    document.close(a, confirm="prompt")
    assert a.calls[0][1] == {"operation": "close"}


def test_close_rejects_invalid_confirm():
    a = FakeAdapter()
    env = document.close(a, confirm="yolo")
    assert env.ok is False
    assert env.error == "invalid_confirm"
    assert a.calls == [], "must not reach Fusion when validation fails"


def test_undo_calls_update_count_times():
    a = FakeAdapter()
    document.undo(a, count=3)
    assert len(a.calls) == 3
    assert all(c == ("update", {"kind": "undo"}) for c in a.calls)


def test_undo_rejects_zero_count():
    a = FakeAdapter()
    env = document.undo(a, count=0)
    assert env.ok is False
    assert env.error == "invalid_count"
    assert a.calls == []


def test_redo_calls_update_with_redo_kind():
    a = FakeAdapter()
    document.redo(a)
    assert a.calls == [("update", {"kind": "redo"})]


def test_undo_stops_on_first_error():
    class ErroringAdapter(FakeAdapter):
        def update(self, kind: str) -> Envelope:
            self.calls.append(("update", {"kind": kind}))
            return Envelope(ok=False, error="no_more_history")

    a = ErroringAdapter()
    env = document.undo(a, count=5)
    assert env.ok is False
    assert env.error == "no_more_history"
    assert len(a.calls) == 1, "must not keep calling after first failure"


@pytest.mark.parametrize("operation", [document.undo, document.redo])
@pytest.mark.parametrize("count", [-1, 0, 1.5, "2", True, None])
def test_history_rejects_nonpositive_or_noninteger_without_mutation(operation, count):
    a = FakeAdapter()
    result = operation(a, count)
    assert not result.ok and result.error == "invalid_count"
    assert not a.calls


@pytest.mark.parametrize("operation", [document.undo, document.redo])
def test_history_preserves_last_success_and_stops_on_later_error(operation):
    class SequenceAdapter(FakeAdapter):
        def update(self, kind):
            super().update(kind)
            return Envelope(ok=len(self.calls) < 2, error="stop" if len(self.calls) == 2 else None)

    a = SequenceAdapter()
    result = operation(a, 4)
    assert not result.ok and result.error == "stop"
    assert len(a.calls) == 2
