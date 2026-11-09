"""Bounded local OBJ bundle preflight and native mesh import scripts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import tempfile
from pathlib import Path

from ..handles import emit_make_handle
from ..native_mesh import MESH_EVIDENCE_SCRIPT
from ..resolver import RESOLVER_SCRIPT

_UNITS = {"mm": "MillimeterMeshUnit", "cm": "CentimeterMeshUnit", "m": "MeterMeshUnit", "in": "InchMeshUnit", "inch": "InchMeshUnit"}
_TEXTURES = {"map_ka", "map_kd", "map_ks", "map_ke", "map_ns", "map_d", "map_bump", "bump", "disp", "decal", "refl", "norm", "map_ao", "map_pr", "map_pm", "map_ps", "map_pc", "map_pcr", "map_aniso", "map_anisor"}
_MAX_MTL = 16 * 1024 * 1024
_MAX_OBJ = 256 * 1024 * 1024
_MAX_FILE = 256 * 1024 * 1024
_MAX_DEPS = 256


def _bundle(path: str, roots: list[str] | None, policy: str) -> dict:
    if policy not in {"require_resources", "geometry_only"}:
        raise ValueError("appearance_policy must be require_resources or geometry_only")
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("network paths are not supported for OBJ bundles")
    source = Path(path).resolve(strict=True)
    if str(source).startswith(("\\\\", "//")) or source.suffix.lower() != ".obj":
        raise ValueError("OBJ source must be a local .obj file")
    if not source.is_file():
        raise ValueError("OBJ source is not a file")
    if roots is not None and (not isinstance(roots, list) or len(roots) > 16):
        raise ValueError("dependency_roots must be a list of at most 16 absolute directories")
    allowed = [source.parent]
    for root in roots or []:
        candidate = Path(root)
        if str(root).startswith(("\\\\", "//")):
            raise ValueError("network dependency roots are unsupported")
        if not candidate.is_absolute():
            raise ValueError("dependency roots must be absolute")
        resolved = candidate.resolve(strict=True)
        if str(resolved).startswith(("\\\\", "//")) or not resolved.is_dir():
            raise ValueError("dependency root must be a local directory")
        allowed.append(resolved)
    entries = []
    missing = []
    missing_paths = []
    visited = set()
    roles = {}
    aliases = []
    total = 0

    def visit(file: Path, kind: str, reference: str):
        nonlocal total
        lexical = file.absolute()
        resolved = lexical.resolve()
        if not any(resolved.is_relative_to(root) for root in allowed):
            raise ValueError("dependency escapes allowed roots: " + reference)
        if kind == "mtl" and lexical.parent.resolve() != resolved.parent:
            raise ValueError("MTL file aliases across directories are unsupported")
        prior_role = roles.setdefault(resolved, kind)
        if prior_role != kind:
            raise ValueError("dependency referenced with conflicting roles: " + reference)
        key = (str(lexical), kind)
        if key in visited:
            return
        visited.add(key)
        if len(visited) > _MAX_DEPS:
            raise ValueError("too many OBJ dependencies")
        exists = resolved.is_file()
        aliases.append({"lexical": str(lexical), "resolved": str(resolved), "exists": exists})
        if not exists:
            missing.append(reference)
            missing_paths.append(str(lexical))
            return
        file = resolved
        size = file.stat().st_size
        if size > (_MAX_MTL if kind == "mtl" else _MAX_OBJ if kind == "obj" else _MAX_FILE):
            raise ValueError("OBJ dependency exceeds size limit")
        total += size
        if total > 512 * 1024 * 1024:
            raise ValueError("OBJ bundle exceeds size limit")
        digest = hashlib.sha256()
        count = 0
        with file.open("rb") as stream:
            while count <= size:
                chunk = stream.read(min(1024 * 1024, size + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                digest.update(chunk)
        if count != size:
            raise ValueError("OBJ dependency changed during preflight")
        entries.append({"path": str(file), "reference": reference, "kind": kind, "bytes": size, "sha256": digest.hexdigest()})
        if kind not in {"obj", "mtl"}:
            return
        try:
            parsed_digest = hashlib.sha256()
            parsed_size = 0
            with file.open("rb") as stream:
                while True:
                    raw = stream.readline(65537)
                    if not raw:
                        break
                    parsed_size += len(raw)
                    if parsed_size > size:
                        raise ValueError("OBJ dependency changed during preflight")
                    parsed_digest.update(raw)
                    line = raw.decode("utf-8-sig" if parsed_size == len(raw) else "utf-8")
                    if len(line) > 16384:
                        raise ValueError("OBJ/MTL line exceeds limit")
                    content = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
                    if any(ord(char) < 32 and char != "\t" or 127 <= ord(char) <= 159 for char in content):
                        raise ValueError("OBJ/MTL contains unsupported control characters")
                    if content.rstrip().endswith("\\"):
                        raise ValueError("continued OBJ/MTL lines are unsupported")
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    parts = stripped.split(None, 1)
                    directive = parts[0].lower()
                    if (kind == "obj" and directive in {"call", "csh"}) or (kind == "mtl" and directive.startswith("map_") and directive not in _TEXTURES):
                        raise ValueError("unsupported external dependency directive")
                    relevant = directive == "mtllib" if kind == "obj" else directive in _TEXTURES
                    if not relevant:
                        continue
                    if len(parts) != 2:
                        raise ValueError("missing dependency reference")
                    if "\\" in parts[1]:
                        raise ValueError("backslash in dependency reference is unsupported")
                    try:
                        values = shlex.split(parts[1], posix=True, comments=True)
                    except ValueError as exc:
                        raise ValueError("invalid dependency reference") from exc
                    if not values or any(value.startswith("-") for value in values):
                        raise ValueError("MTL texture options are unsupported")
                    if kind == "mtl" and len(values) != 1:
                        raise ValueError("texture paths containing spaces must be quoted")
                    for value in values:
                        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value) or value.startswith(("//", "\\\\")) or "\\" in value:
                            raise ValueError("remote or platform-ambiguous dependency reference")
                        visit(lexical.parent / value, "mtl" if kind == "obj" else "image", value)
            if parsed_size != size or parsed_digest.hexdigest() != digest.hexdigest():
                raise ValueError("OBJ dependency changed during preflight")
        except UnicodeError as exc:
            raise ValueError("OBJ/MTL must be UTF-8 text") from exc

    visit(source, "obj", source.name)
    if missing and policy == "require_resources":
        raise ValueError("missing appearance dependencies: " + ", ".join(missing[:10]))
    return {"source": str(source), "allowed_roots": [str(x) for x in allowed], "dependencies": entries, "missing": missing, "missing_paths": missing_paths, "aliases": aliases, "appearance_policy": policy}


def _manifest_artifact(bundle: dict) -> dict:
    """Publish bounded provenance, separately from model mutation receipts."""
    payload = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > 256 * 1024:
        raise ValueError("OBJ manifest exceeds size limit")
    digest = hashlib.sha256(payload).hexdigest()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    directory = base / "fusion-cad-mcp" / "manifests-v1"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (digest + ".json")
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("existing manifest artifact is invalid") from None
            with destination.open("rb") as existing:
                if existing.read(len(payload) + 1) != payload:
                    raise ValueError("existing manifest artifact is invalid") from None
    finally:
        os.unlink(temporary)
    return {"path": str(destination), "sha256": digest,
            "dependency_count": len(bundle["dependencies"]),
            "missing_count": len(bundle["missing"]), "appearance_policy": bundle["appearance_policy"]}


def build_obj_import(path: str, target_component: str | None, units: str | None, roots: list[str] | None, policy: str) -> str:
    if units not in _UNITS:
        raise ValueError("OBJ requires explicit units: mm, cm, m, in or inch")
    try:
        bundle = _bundle(path, roots, policy)
        artifact = _manifest_artifact({**bundle, "source_units": units, "target_component": target_component})
    except OSError as exc:
        raise ValueError("OBJ bundle cannot be read: " + str(exc)) from exc
    select = ""
    if target_component is not None:
        select = f'''
    occurrence, error = _select_occurrence(root, {target_component!r})
    if error:
        error["mutation_state"] = "not_started"
        print(json.dumps(error)); return
    if occurrence.isReferencedComponent or root.allOccurrencesByComponent(occurrence.component).count != 1:
        print(json.dumps({{"ok": False, "error": "unsafe_target_component", "mutation_state": "not_started"}})); return
    target = occurrence.component
'''
    handle = emit_make_handle("mesh_body", "entry['path'] + context_suffix", "item")
    return f'''import adsk.core
import adsk.fusion
import json
import os
import hashlib
{RESOLVER_SCRIPT if select else ""}
{MESH_EVIDENCE_SCRIPT}

def _inside(path, roots):
    for root in roots:
        try:
            if os.path.normcase(os.path.commonpath([path, root])) == os.path.normcase(root): return True
        except ValueError:
            continue
    return False

def _bounds(box):
    return {{"min": [box.minPoint.x * 10, box.minPoint.y * 10, box.minPoint.z * 10], "max": [box.maxPoint.x * 10, box.maxPoint.y * 10, box.maxPoint.z * 10], "units": "mm"}}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design", "mutation_state": "not_started"}})); return
    root = design.rootComponent
    target = root
    occurrence = None
{select}
    bundle = {bundle!r}
    try:
        before = target.meshBodies.count
        before_bodies = [target.meshBodies.item(i) for i in range(before)]
        before_tokens = [body.entityToken for body in before_bodies]
        transform = occurrence.transform2 if occurrence is not None else adsk.core.Matrix3D.create()
        if len(set(before_tokens)) != before or any(not body.isValid for body in before_bodies):
            raise ValueError("invalid existing identities")
        before_witnesses = [(body.name, _mesh_witness(body)) for body in before_bodies]
    except Exception:
        print(json.dumps({{"ok": False, "error": "mesh_baseline_unavailable", "mutation_state": "not_started"}})); return
    # Recheck every lexical name Fusion can follow, including missing aliases.
    try:
        for alias in bundle["aliases"]:
            actual = os.path.realpath(alias["lexical"])
            if (os.path.normcase(actual) != os.path.normcase(alias["resolved"])
                    or not _inside(actual, bundle["allowed_roots"])
                    or os.path.isfile(actual) != alias["exists"]):
                raise ValueError("alias changed")
    except (OSError, ValueError):
        print(json.dumps({{"ok": False, "error": "dependency_changed", "mutation_state": "not_started"}})); return
    # Recheck bytes immediately before native import.
    for dep in bundle["dependencies"]:
        resolved = os.path.realpath(dep["path"])
        if resolved != dep["path"] or not _inside(resolved, bundle["allowed_roots"]):
            print(json.dumps({{"ok": False, "error": "dependency_changed", "mutation_state": "not_started"}})); return
        try:
            digest = hashlib.sha256()
            count = 0
            with open(resolved, "rb") as stream:
                while count <= dep["bytes"]:
                    chunk = stream.read(min(1024 * 1024, dep["bytes"] + 1 - count))
                    if not chunk: break
                    count += len(chunk)
                    digest.update(chunk)
            if count != dep["bytes"] or digest.hexdigest() != dep["sha256"]:
                raise ValueError("changed")
        except (OSError, ValueError):
            print(json.dumps({{"ok": False, "error": "dependency_changed", "mutation_state": "not_started"}})); return
    # An exception or empty result may have left mesh bodies behind; preserve the barrier.
    objects = target.meshBodies.add(bundle["source"], adsk.fusion.MeshUnits.{_UNITS[units]})
    if objects is None or objects.count == 0:
        print(json.dumps({{"ok": False, "error": "import_no_mesh_bodies", "mesh_bodies_delta": target.meshBodies.count - before, "cleanup": "not_attempted"}})); return
    if objects.count > 128:
        print(json.dumps({{"ok": False, "error": "too_many_imported_mesh_bodies", "count": objects.count, "cleanup": "not_attempted"}})); return
    created = []
    imported = []
    try:
        for index in range(objects.count):
            native = objects.item(index)
            if native is None:
                raise ValueError("returned mesh unavailable")
            created.append(native)
        created_tokens = [item.entityToken for item in created]
        current_tokens = [target.meshBodies.item(i).entityToken for i in range(target.meshBodies.count)]
        if (any(not item.isValid or item.parentComponent != target for item in created)
                or any(not item.isValid for item in before_bodies)
                or [(body.name, _mesh_witness(body)) for body in before_bodies] != before_witnesses
                or len(set(before_tokens + created_tokens)) != before + len(created_tokens)
                or sorted(current_tokens) != sorted(before_tokens + created_tokens)):
            raise ValueError("returned mesh identities do not match target changes")
        for native in created:
            item = native.createForAssemblyContext(occurrence) if occurrence is not None else native
            if item is None:
                raise ValueError("mesh proxy unavailable")
            context_path = occurrence.fullPathName if occurrence is not None else None
            context_suffix = ("|occ=" + context_path) if context_path else ""
            entry = {{"name": native.name, "object_type": item.objectType, "path": (context_path + " / " if context_path else "") + native.name,
                      "local_bounds": _bounds(native.boundingBox), "root_bounds": _bounds(item.boundingBox),
                      "node_count": native.mesh.nodeCount, "triangle_count": native.mesh.triangleCount}}
            entry["handle"] = {handle}
            imported.append(entry)
    except Exception as exc:
        cleanup = "not_attempted"
        try:
            created_tokens = [item.entityToken for item in created]
            current_tokens = [target.meshBodies.item(i).entityToken for i in range(target.meshBodies.count)]
            safe = (len(created) == objects.count and all(item.isValid and item.parentComponent == target for item in created)
                    and all(item.isValid for item in before_bodies)
                    and [(body.name, _mesh_witness(body)) for body in before_bodies] == before_witnesses
                    and len(set(before_tokens + created_tokens)) == len(before_tokens) + len(created_tokens)
                    and sorted(current_tokens) == sorted(before_tokens + created_tokens))
            if safe:
                cleanup = "partial_or_unverified"
                deleted = all(item.deleteMe() for item in reversed(created))
                remaining = [target.meshBodies.item(i).entityToken for i in range(target.meshBodies.count)]
                if (deleted and sorted(remaining) == sorted(before_tokens)
                        and [(body.name, _mesh_witness(body)) for body in before_bodies] == before_witnesses):
                    cleanup = "verified_mesh_bodies_only"
        except Exception:
            cleanup = "partial_or_unverified"
        print(json.dumps({{"ok": False, "error": "mesh_readback_failed", "detail": str(exc)[:256], "cleanup": cleanup}})); return
    print(json.dumps({{"ok": True, "format": "obj", "path": bundle["source"], "source_units": {units!r},
      "target_component": target.name, "target_occurrence_path": occurrence.fullPathName if occurrence else None,
      "target_to_root_transform_cm": list(transform.asArray()), "coordinate_frame": "target_component" if occurrence else "root",
      "mesh_bodies_added": target.meshBodies.count - before, "imported_objects": imported,
      "bundle": {artifact!r}, "appearance": {{"status": "unknown", "policy": bundle["appearance_policy"], "missing_count": len(bundle["missing"])}}}}))
'''.strip()
