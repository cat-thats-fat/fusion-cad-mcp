"""Group 1 Document & State tools beyond doc_state.

Wraps Fusion's fusion_mcp_read (document/projects queries), fusion_mcp_execute
(document ops), and fusion_mcp_update (undo/redo). Tool naming and semantics
per V2 spec Section 4 Group 1.
"""

from __future__ import annotations

from ..adapter import FusionAdapter
from ..envelope import Envelope

# Valid confirm values for `close`. We reject auto-picking either; the agent must
# surface the save-vs-discard choice to the user per the skill's gotcha catalog.
VALID_CLOSE_CONFIRM = {"save", "discard", "prompt"}


# ---------- read-side: document & project listings ----------


def list_open_docs(adapter: FusionAdapter) -> Envelope:
    """Return documents currently open in Fusion (not the recent-file list)."""
    return adapter.read_query("document", operation="open")


def list_projects(adapter: FusionAdapter) -> Envelope:
    """Return all projects (hubs / folders) the user can see."""
    return adapter.read_query("projects")


def search_docs(adapter: FusionAdapter, query: str, project: str | None = None) -> Envelope:
    """Fuzzy-search documents by name. Project is optional; omit to search all."""
    kwargs: dict = {"operation": "search", "name": query}
    if project:
        kwargs["project"] = project
    return adapter.read_query("document", **kwargs)


def open_doc(
    adapter: FusionAdapter, name: str, project: str | None = None, file_id: str | None = None
) -> Envelope:
    """Resolve an exact unique name, then execute the documented open operation."""
    if file_id:
        return adapter.execute_document_op("open", fileId=file_id)
    if not isinstance(name, str) or not name.strip():
        return Envelope(ok=False, error="invalid_name")
    found = search_docs(adapter, name, project)
    if not found.ok:
        return found
    rows = (found.result or {}).get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return Envelope(ok=False, error="document_search_unrecognized")
    matches = [row for row in rows if row.get("name") == name]
    if len(matches) != 1:
        return Envelope(
            ok=False,
            error="document_ambiguous" if len(matches) > 1 else "document_not_found",
            result={"matches": matches},
        )
    file_id = matches[0].get("id") or matches[0].get("fileId")
    if not isinstance(file_id, str) or not file_id:
        return Envelope(ok=False, error="document_id_missing")
    return adapter.execute_document_op("open", fileId=file_id)


# ---------- write-side: document ops ----------


def save(adapter: FusionAdapter) -> Envelope:
    """Save the active document. Untitled docs are refused by Fusion's MCP;
    the refusal surfaces as a structured error so the agent can ask for save_as.
    """
    return adapter.execute_document_op("save")


def save_as(adapter: FusionAdapter, path: str) -> Envelope:
    """A local path is not a valid Fusion cloud save destination."""
    return Envelope(
        ok=False,
        error="save_as_unsupported",
        message="Use export for a local file, or Fusion's Save dialog for an initial cloud save.",
    )


def close(adapter: FusionAdapter, confirm: str = "prompt") -> Envelope:
    """Close the active document. confirm must be one of 'save', 'discard', 'prompt'.

    'save'    -> userConfirmedSaveAndClose=True
    'discard' -> userConfirmedCloseWithoutSave=True
    'prompt'  -> neither flag set; Fusion shows its standard dialog if dirty

    We refuse to pick between save and discard automatically per the skill's
    gotcha catalog: dirty-doc close is a destructive operation that the agent
    must surface to the user.
    """
    if confirm not in VALID_CLOSE_CONFIRM:
        return Envelope(
            ok=False,
            error="invalid_confirm",
            message=f"confirm must be one of {sorted(VALID_CLOSE_CONFIRM)}, got {confirm!r}",
        )
    extras: dict = {}
    if confirm == "save":
        extras["userConfirmedSaveAndClose"] = True
    elif confirm == "discard":
        extras["userConfirmedCloseWithoutSave"] = True
    return adapter.execute_document_op("close", **extras)


# ---------- write-side: undo / redo ----------


def undo(adapter: FusionAdapter, count: int = 1) -> Envelope:
    """Run undo `count` times. WARNING: undo treats the prior execute call as one
    atomic transaction; mixed-content scripts (params + geometry) get fully wiped.
    Prefer the delete-loop cleanup pattern in patterns.md when possible.
    """
    if type(count) is not int or count < 1:
        return Envelope(ok=False, error="invalid_count", message="count must be >= 1")
    last: Envelope | None = None
    for _ in range(count):
        last = adapter.update("undo")
        if not last.ok:
            return last
    assert last is not None
    return last


def redo(adapter: FusionAdapter, count: int = 1) -> Envelope:
    """Run redo `count` times."""
    if type(count) is not int or count < 1:
        return Envelope(ok=False, error="invalid_count", message="count must be >= 1")
    last: Envelope | None = None
    for _ in range(count):
        last = adapter.update("redo")
        if not last.ok:
            return last
    assert last is not None
    return last


__all__ = [
    "list_open_docs",
    "list_projects",
    "search_docs",
    "open_doc",
    "save",
    "save_as",
    "close",
    "undo",
    "redo",
]
