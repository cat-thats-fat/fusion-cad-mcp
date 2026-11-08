"""Entity handle scaffolding per V2 spec Section 5a.

Format: `kind:path:token` where token comes from Fusion's `entityToken` property.
The path component is human-readable for debug; the token is the machine resolver.

Lifecycle:
- Entity-producing tools (create_sketch, extrude, fillet, etc.) include handles
  in their result envelope under `state.handles_added`. The script captures
  entity.entityToken from Fusion at run time and emits the handle string.
- Entity-consuming tools (measure, ray methods, fillet_edges UI sel, etc.)
  accept handle strings as inputs. The generator parses the handle and emits
  a `Design.findEntityByToken(token)` call to resolve the live entity.
- Invalidation: handles for entities deleted by undo or feature edits return
  a structured `handle_invalid` error from any consuming tool.

Kinds (V2 spec Section 5a):
    sketch, profile, body, mesh_body, face, edge, vertex, feature, component, occurrence,
    param, joint, ucs, plane, axis, point
"""

from __future__ import annotations

import json
import re

VALID_KINDS = frozenset(
    {
        "sketch",
        "profile",
        "body",
        "mesh_body",
        "face",
        "edge",
        "vertex",
        "feature",
        "component",
        "occurrence",
        "param",
        "joint",
        "ucs",
        "plane",
        "axis",
        "point",
    }
)

# token: alphanumeric, +, /, =, _, -, . (entityToken is base64-ish + some symbols)
_HANDLE_RE = re.compile(r"^([a-z][a-z_]*):(.*):([A-Za-z0-9+/=_\-.]+)$")


def parse_handle(s: str) -> dict[str, str]:
    """Parse 'kind:path:token' into {kind, path, token}. Raises ValueError on malformed."""
    if not isinstance(s, str):
        raise ValueError(f"handle must be a string, got {type(s).__name__}")
    m = _HANDLE_RE.match(s)
    if not m:
        raise ValueError(f"malformed handle: {s!r}")
    kind, path, token = m.group(1), m.group(2), m.group(3)
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown handle kind {kind!r} in {s!r}")
    return {"kind": kind, "path": path, "token": token}


def is_handle(s: str) -> bool:
    """Cheap probe: does this look like a handle? Useful for tools accepting either name or handle."""
    if not isinstance(s, str):
        return False
    return bool(_HANDLE_RE.match(s))


def emit_resolve(handle_str: str, var: str = "_entity", fail_var: str = "_handle_invalid") -> str:
    """Emit Python that resolves a handle to a live Fusion entity, assigning to `var`.

    On resolution failure, the script must `print(json.dumps(...)); return` immediately —
    callers compose this into their generator output.

    The emitted code expects `design` and `json` to be in scope. It assigns the resolved
    entity to `var`. If resolution fails, it emits a structured error block; the caller
    can catch by checking `{var}` for None.
    """
    h = parse_handle(handle_str)
    types = {"sketch": "Sketch", "profile": "Profile", "body": "BRepBody", "mesh_body": "MeshBody",
             "face": "BRepFace", "edge": "BRepEdge", "vertex": "BRepVertex",
             "feature": "Feature", "component": "Component", "occurrence": "Occurrence",
             "param": "Parameter", "joint": "Joint", "ucs": "ConstructionCoordinateSystem",
             "plane": "ConstructionPlane", "axis": "ConstructionAxis", "point": "SketchPoint"}
    expected = types[h["kind"]]
    fields = {}
    for field in h["path"].split("|")[1:]:
        key, sep, value = field.partition("=")
        if key in {"occ", "doc", "file"}:
            if not sep or not value or key in fields:
                raise ValueError("invalid handle scope metadata")
            fields[key] = value
    occurrence_path = fields.get("occ")
    if "file" in fields and "doc" not in fields:
        raise ValueError("file scope requires document scope")
    for key in ("doc", "file"):
        if key in fields and not re.fullmatch(r"(?:[0-9a-f]{2})+", fields[key]):
            raise ValueError("invalid handle scope encoding")
    scope_code = ""
    if "doc" in fields:
        scope_code = (
            f"_app = adsk.core.Application.get()\n"
            f"_active_doc = _app.activeDocument\n"
            f"_doc_key = {json.dumps(fields['doc'])}\n"
            f"_file_key = {repr(fields.get('file'))}\n"
            f"def _scope_matches(_doc):\n"
            f"    try:\n"
            f"        if not _doc or not _doc.creationId or str(_doc.creationId).encode('utf-8').hex() != _doc_key: return False\n"
            f"        if _file_key is not None:\n"
            f"            _file = _doc.dataFile\n"
            f"            if not _file or not _file.id or str(_file.id).encode('utf-8').hex() != _file_key: return False\n"
            f"        return True\n"
            f"    except Exception:\n"
            f"        return False\n"
            f"if not _scope_matches(_active_doc):\n"
            f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'reason': 'document_scope_mismatch'}}}})); return\n"
            f"try:\n"
            f"    _docs = _app.documents\n"
            f"    _scope_count = sum(1 for _i in range(_docs.count) if _scope_matches(_docs.item(_i)))\n"
            f"except Exception:\n"
            f"    _scope_count = 0\n"
            f"if _scope_count != 1:\n"
            f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'reason': 'document_scope_ambiguous_or_unavailable'}}}})); return\n"
        )
    context_code = ""
    if occurrence_path:
        context_code = (
            f"_occs = [design.rootComponent.allOccurrences.item(_i) for _i in range(design.rootComponent.allOccurrences.count) if design.rootComponent.allOccurrences.item(_i).fullPathName == {occurrence_path!r}]\n"
            f"if len(_occs) != 1:\n"
            f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'handle': {handle_str!r}, 'reason': 'occurrence_not_found_or_ambiguous'}}}})); return\n"
            f"_ctx = getattr({var}, 'assemblyContext', None)\n"
            f"if _ctx is None:\n"
            f"    {var} = {var}.createForAssemblyContext(_occs[0])\n"
            f"elif _ctx.fullPathName != {occurrence_path!r}:\n"
            f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'handle': {handle_str!r}, 'reason': 'occurrence_mismatch'}}}})); return\n"
            f"if {var} is None:\n"
            f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'handle': {handle_str!r}, 'reason': 'proxy_unavailable'}}}})); return\n"
        )
    return scope_code + (
        f"_found = design.findEntityByToken({json.dumps(h['token'])})\n"
        f"_matches = list(_found) if _found is not None and hasattr(_found, '__len__') else ([_found] if _found else [])\n"
        f"if len(_matches) != 1:\n"
        f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'handle': {handle_str!r}, 'reason': 'not_found_in_active_doc' if not _matches else 'ambiguous_token'}}}})); return\n"
        f"{var} = _matches[0]\n"
        f"_actual_type = getattr({var}, 'objectType', '')\n"
        f"if not _actual_type or {json.dumps(expected)} not in _actual_type:\n"
        f"    print(json.dumps({{'ok': False, 'error': 'handle_invalid', 'detail': {{'handle': {handle_str!r}, 'reason': 'wrong_type', 'actual_type': _actual_type}}}})); return\n"
        + context_code
    )



def emit_resolve_many(handles: list[str], collection_var: str = "_resolved") -> str:
    """Emit Python that resolves a list of handles into a Python list, with per-handle failure."""
    lines = [f"{collection_var} = []\n"]
    for i, handle in enumerate(handles):
        var = f"_h{i}"
        lines.append(emit_resolve(handle, var=var))
        lines.append(f"{collection_var}.append({var})\n")
    return "".join(lines)



def emit_make_handle(kind: str, path_expr: str, entity_expr: str) -> str:
    """Emit a Python expression that builds a handle string at script run time.

    path_expr and entity_expr are Python expressions evaluated in the script (e.g.
    `sk.name` for path, `sk` for the entity itself). Returns a string expression
    that can be used inside the script (e.g. `state['handle'] = <this>`).

    Example:
        emit_make_handle('sketch', 'sk.name', 'sk')
        -> f"'sketch:' + sk.name + ':' + sk.entityToken"
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown handle kind {kind!r}")
    return f"({json.dumps(kind)} + ':' + str({path_expr}) + {emit_scope_expr()} + ':' + {entity_expr}.entityToken)"


def emit_scope_expr() -> str:
    """Emit document scope using the active document's creation and saved file IDs."""
    doc = "adsk.core.Application.get().activeDocument"
    return (f"('|doc=' + str({doc}.creationId).encode('utf-8').hex() + "
            f"(('|file=' + str({doc}.dataFile.id).encode('utf-8').hex()) "
            f"if {doc}.dataFile and {doc}.dataFile.id else ''))")


__all__ = [
    "VALID_KINDS",
    "emit_make_handle",
    "emit_resolve",
    "emit_resolve_many",
    "emit_scope_expr",
    "is_handle",
    "parse_handle",
]
