"""Import and export local CAD files through the active Fusion design."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_stdout_json, script_parse_failure
from ..handles import emit_make_handle
from ..resolver import RESOLVER_SCRIPT

_MESH_EXPORT_FACTORIES = {
    "stl": "createSTLExportOptions",
    "3mf": "createC3MFExportOptions",
    "obj": "createOBJExportOptions",
}
_COMPONENT_EXPORT_FACTORIES = {
    "step": "createSTEPExportOptions",
    "iges": "createIGESExportOptions",
    "f3d": "createFusionArchiveExportOptions",
    "sat": "createSATExportOptions",
    "smt": "createSMTExportOptions",
}
_IMPORT_FACTORIES = {
    "step": "createSTEPImportOptions",
    "iges": "createIGESImportOptions",
    "sat": "createSATImportOptions",
    "smt": "createSMTImportOptions",
    "f3d": "createFusionArchiveImportOptions",
}
EXPORT_FORMATS = set(_MESH_EXPORT_FACTORIES) | set(_COMPONENT_EXPORT_FACTORIES)
IMPORT_FORMATS = set(_IMPORT_FACTORIES)

STL_REFINEMENT = {
    "low": "MeshRefinementLow",
    "medium": "MeshRefinementMedium",
    "high": "MeshRefinementHigh",
}
_STL_UNITS = {
    "mm": "MillimeterDistanceUnits",
    "cm": "CentimeterDistanceUnits",
    "m": "MeterDistanceUnits",
    "inch": "InchDistanceUnits",
    "in": "InchDistanceUnits",
}

_WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _validate_path(path: str) -> tuple[bool, str | None]:
    """Accept an absolute path for the platform on which Fusion is running."""
    if not isinstance(path, str) or not path or "\0" in path:
        return False, "path must be a non-empty string without NUL characters"
    if ".." in re.split(r"[\\/]", path):
        return False, "path contains '..' parent-dir reference"
    if not (PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()):
        return False, "path must be absolute"
    if path.endswith(("/", "\\")):
        return False, "path must name a file"
    base = re.split(r"[\\/]", path)[-1].lower().split(".")[0].rstrip(" ")
    if base in _WIN_RESERVED:
        return False, f"path uses Windows reserved name {base!r}"
    return True, None


def _ok_runner(adapter: FusionAdapter, script: str, label: str, *, writes_model: bool = True) -> Envelope:
    env = adapter.execute_script(script)
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is None or "ok" not in parsed:
        return script_parse_failure(env, f"{label}_parse_failed", writes_model=writes_model)
    if parsed.get("ok") is not True:
        return Envelope(
            ok=False,
            error=parsed.get("error", f"{label}_failed"),
            message=env.message,
            result=parsed,
        )
    handles = [obj["handle"] for obj in parsed.get("imported_objects", []) if obj.get("handle")]
    state = {"handles_added": handles} if handles else None
    return Envelope(ok=True, message=env.message, result=parsed, state=state)


_SCRIPT_HEADER = """\
import adsk.core
import adsk.fusion
import json
import os
"""

_ACTIVE_DESIGN = """\
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({"ok": False, "error": "no_active_design"}))
        return
    root = design.rootComponent
"""

_PUBLISH_OBJ = """
def _publish_obj(staged_path, path, sidecar):
    # Preserve OBJ's relative material reference. Roll back the companion if
    # publishing the main file fails, including when the destination is locked.
    staged_dir = os.path.dirname(staged_path)
    dest_sidecar = os.path.join(os.path.dirname(path), sidecar)
    backup = os.path.join(staged_dir, ".previous-material")
    if os.path.lexists(dest_sidecar):
        shutil.copy2(dest_sidecar, backup, follow_symlinks=False)
    os.replace(os.path.join(staged_dir, sidecar), dest_sidecar)
    try:
        os.replace(staged_path, path)
    except OSError:
        if os.path.lexists(backup):
            os.replace(backup, dest_sidecar)
        else:
            os.unlink(dest_sidecar)
        raise
"""


def build_export(
    format: str,
    body: str | None,
    path: str,
    refinement: str = "medium",
    units: str = "mm",
    component: str | None = None,
) -> str:
    fmt = format.lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"format must be one of {sorted(EXPORT_FORMATS)}, got {format!r}")
    if body is not None and component is not None:
        raise ValueError("choose either body or component")
    if body is not None and fmt not in _MESH_EXPORT_FACTORIES:
        raise ValueError(f"{fmt} does not support body scope")

    scope_lookup = ""
    scope_info = "'scope': 'root'"
    geometry = "root"
    if body is not None:
        scope_lookup = f"""\
    geometry, error = _select_body(root, {body!r})
    if error:
        print(json.dumps(error))
        return
"""
        geometry = "geometry"
        scope_info = f"'scope': 'body', 'body': {body!r}"
    elif component is not None:
        scope_lookup = f"""\
    occurrence, error = _select_occurrence(root, {component!r})
    if error:
        print(json.dumps(error))
        return
"""
        geometry = "occurrence" if fmt in _MESH_EXPORT_FACTORIES else "occurrence.component"
        scope_info = "'scope': 'component', 'component': occurrence.fullPathName"

    if fmt in _MESH_EXPORT_FACTORIES:
        factory = _MESH_EXPORT_FACTORIES[fmt]
        option_args = f"{geometry}, staged_path"
    else:
        factory = _COMPONENT_EXPORT_FACTORIES[fmt]
        option_args = f"staged_path, {geometry}" if component is not None else "staged_path"

    settings = ""
    if fmt in {"stl", "obj"}:
        if refinement not in STL_REFINEMENT:
            raise ValueError(f"refinement must be one of {sorted(STL_REFINEMENT)} for {fmt}")
        settings += f"        opts.meshRefinement = adsk.fusion.MeshRefinementSettings.{STL_REFINEMENT[refinement]}\n"
        settings += "        opts.sendToPrintUtility = False\n"
        scope_info += f", 'refinement': {refinement!r}"
    if fmt == "stl":
        if units not in _STL_UNITS:
            raise ValueError(f"units must be one of {sorted(_STL_UNITS)} for STL")
        settings += f"""\
        if not hasattr(opts, "unitType"):
            print(json.dumps({{"ok": False, "error": "export_units_unsupported"}}))
            return
        opts.unitType = adsk.fusion.DistanceUnits.{_STL_UNITS[units]}
"""
        scope_info += f", 'units': {units!r}"

    lookup_helpers = RESOLVER_SCRIPT if scope_lookup else ""
    obj_support = "import shutil\n" + _PUBLISH_OBJ if fmt == "obj" else ""
    if fmt == "obj":
        publish = """\
        sidecar = os.path.splitext(os.path.basename(path))[0] + ".mtl"
        sidecars = [sidecar] if extras == {sidecar} and os.path.isfile(os.path.join(staging, sidecar)) else []
        if extras and not sidecars:
            print(json.dumps({"ok": False, "error": "export_unexpected_artifacts"}))
            return
        if sidecars:
            _publish_obj(staged_path, path, sidecar)
        else:
            os.replace(staged_path, path)
"""
    else:
        publish = """\
        sidecars = []
        if extras:
            print(json.dumps({"ok": False, "error": "export_unexpected_artifacts"}))
            return
        os.replace(staged_path, path)
"""

    return f"""\
{_SCRIPT_HEADER}import tempfile
{obj_support}
{lookup_helpers}
def run(_ctx):
{_ACTIVE_DESIGN}{scope_lookup}    path = {path!r}
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    em = design.exportManager
    with tempfile.TemporaryDirectory(prefix=".fusion-export-", dir=parent) as staging:
        staged_path = os.path.join(staging, os.path.basename(path))
        opts = em.{factory}({option_args})
        if opts is None:
            print(json.dumps({{"ok": False, "error": "export_options_failed"}}))
            return
{settings}        if not em.execute(opts):
            print(json.dumps({{"ok": False, "error": "export_failed"}}))
            return
        size = os.path.getsize(staged_path) if os.path.isfile(staged_path) else 0
        if size == 0:
            print(json.dumps({{"ok": False, "error": "export_empty"}}))
            return
        extras = set(os.listdir(staging)) - {{os.path.basename(path)}}
{publish}    print(json.dumps({{
        "ok": True, "format": {fmt!r}, "path": path, "bytes_written": size,
        {scope_info}, "sidecars": [os.path.join(parent, name) for name in sidecars],
    }}))
""".strip()


def export(
    adapter: FusionAdapter,
    format: str,
    path: str,
    body: str | None = None,
    refinement: str = "medium",
    units: str = "mm",
    component: str | None = None,
) -> Envelope:
    ok, err = _validate_path(path)
    if not ok:
        return Envelope(ok=False, error="invalid_path", message=err or "")
    try:
        script = build_export(format, body, path, refinement, units, component)
    except ValueError as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    return _ok_runner(adapter, script, "export", writes_model=False)


def build_import_geometry(
    format: str, path: str, target_component: str | None = None
) -> str:
    fmt = format.lower()
    if fmt not in IMPORT_FORMATS:
        raise ValueError(f"format must be one of {sorted(IMPORT_FORMATS)}, got {format!r}")
    factory = _IMPORT_FACTORIES[fmt]
    occurrence_handle = emit_make_handle("occurrence", "entry['path'] + context_suffix", "item")
    body_handle = emit_make_handle("body", "entry['path'] + context_suffix", "item")
    select_target = ""
    if target_component is not None:
        select_target = f"""\
    occurrence, error = _select_occurrence(root, {target_component!r})
    if error:
        print(json.dumps(error))
        return
    if occurrence.isReferencedComponent:
        print(json.dumps({{"ok": False, "error": "referenced_component_edit_unsupported"}}))
        return
    target = occurrence.component
    if root.allOccurrencesByComponent(target).count != 1:
        print(json.dumps({{"ok": False, "error": "shared_component_edit_unsupported"}}))
        return
"""

    return f"""\
{_SCRIPT_HEADER}import hashlib

{RESOLVER_SCRIPT if select_target else ""}
def _describe_imported(item, occurrence):
    entry = {{"object_type": item.objectType, "name": getattr(item, "name", None)}}
    if not item.objectType.endswith(("::Occurrence", "::BRepBody")):
        return entry
    if occurrence is not None and item.assemblyContext is None:
        proxy = item.createForAssemblyContext(occurrence)
        if proxy is None:
            entry["identity_error"] = "proxy_unavailable"
            return entry
        item = proxy
    context = item.assemblyContext
    context_path = context.fullPathName if context is not None else None
    context_suffix = ("|occ=" + context_path) if context_path else ""
    if item.objectType.endswith("::Occurrence"):
        entry["path"] = item.fullPathName
        entry["handle"] = {occurrence_handle}
    else:
        entry["path"] = (context_path + " / " if context_path else "") + item.name
        entry["handle"] = {body_handle}
    return entry

def run(_ctx):
{_ACTIVE_DESIGN}    path = {path!r}
    if not os.path.isfile(path):
        print(json.dumps({{"ok": False, "error": "file_not_found", "path": path}}))
        return
    target = root
    occurrence = None
{select_target}    source_hash = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            source_hash.update(chunk)
    im = app.importManager
    opts = im.{factory}(path)
    if opts is None:
        print(json.dumps({{"ok": False, "error": "import_options_failed"}}))
        return
    pre_bodies = target.bRepBodies.count
    pre_occs = target.occurrences.count
    display_length_unit = design.unitsManager.defaultLengthUnits
    target_transform = occurrence.transform2 if occurrence is not None else adsk.core.Matrix3D.create()
    objects = im.importToTarget2(opts, target)
    if objects is None or objects.count == 0:
        print(json.dumps({{"ok": False, "error": "import_no_objects"}}))
        return
    imported = []
    for index in range(objects.count):
        item = objects.item(index)
        try:
            imported.append(_describe_imported(item, occurrence))
        except (AttributeError, RuntimeError):
            # Metadata can be unavailable for an entity even after import succeeds.
            imported.append({{"object_type": item.objectType, "identity_error": "identity_unavailable"}})
    print(json.dumps({{
        "ok": True, "format": {fmt!r}, "path": path, "source_sha256": source_hash.hexdigest(),
        "target_component": target.name,
        "target_occurrence_path": occurrence.fullPathName if occurrence is not None else None,
        "coordinate_frame": "target_component" if occurrence is not None else "root",
        "target_to_root_transform_cm": list(target_transform.asArray()),
        "fusion_length_unit": "cm", "display_length_unit": display_length_unit,
        "bodies_added": target.bRepBodies.count - pre_bodies,
        "occurrences_added": target.occurrences.count - pre_occs,
        "imported_objects": imported,
    }}))
""".strip()


def import_geometry(
    adapter: FusionAdapter, format: str, path: str, target_component: str | None = None
) -> Envelope:
    ok, err = _validate_path(path)
    if not ok:
        return Envelope(ok=False, error="invalid_path", message=err or "")
    try:
        script = build_import_geometry(format, path, target_component)
    except ValueError as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    return _ok_runner(adapter, script, "import_geometry")


__all__ = [
    "EXPORT_FORMATS",
    "IMPORT_FORMATS",
    "build_export",
    "build_import_geometry",
    "export",
    "import_geometry",
]
