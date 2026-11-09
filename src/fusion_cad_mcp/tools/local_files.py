"""Open local Fusion archives and publish verified viewport PNG files."""

from __future__ import annotations

from pathlib import PureWindowsPath

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from .io import _validate_path
from .visualize import VALID_DIRECTIONS


def _local_path(path: str, suffix: str) -> str | None:
    valid, error = _validate_path(path)
    if not valid:
        return error
    windows = PureWindowsPath(path)
    if windows.drive.startswith("\\\\") or path.startswith(("\\\\", "//")):
        return "path must be local rather than a UNC share or device path"
    if ":" in path[len(windows.drive):] or windows.suffix.lower() != suffix:
        return f"path must name a {suffix} file"
    return None


_HEADER = """
import adsk.core
import hashlib
import json
import os
import tempfile


def _out(**data):
    print(json.dumps(data))


def _absolute_here(path):
    return os.path.isabs(path) and (os.name != 'nt' or bool(os.path.splitdrive(path)[0]))
"""


_OPEN_ARCHIVE = """
import adsk.fusion

HEALTH = ('healthy', 'warning', 'failed', 'suppressed', 'rolled_back', 'unknown')
COMPONENT_LIMIT = 64
HEALTH_LIMIT = 256


def _summary(doc):
    product = doc.products.itemByProductType('DesignProductType')
    design = adsk.fusion.Design.cast(product) if product is not None else None
    if design is None:
        return {'available': False, 'reason': 'design_product_unavailable'}
    root = design.rootComponent
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    collections = {'bodies': 'bRepBodies', 'sketches': 'sketches', 'features': 'features'}
    root_counts = {key: getattr(root, field).count for key, field in collections.items()}
    all_components = design.allComponents
    components = [root]
    for i in range(min(all_components.count, COMPONENT_LIMIT)):
        if len(components) == COMPONENT_LIMIT:
            break
        comp = all_components.item(i)
        if comp != root:
            components.append(comp)
    totals = {key: 0 for key in collections}
    health = dict.fromkeys(HEALTH, 0)
    checked = 0
    for comp in components:
        for key, field in collections.items():
            collection = getattr(comp, field)
            totals[key] += collection.count
            if key == 'bodies':
                continue
            for i in range(min(collection.count, HEALTH_LIMIT - checked)):
                try:
                    state = int(collection.item(i).healthState)
                    label = HEALTH[state] if 0 <= state < len(HEALTH) else 'unknown'
                except Exception:
                    label = 'unknown'
                health[label] += 1
                checked += 1
    truncated = all_components.count > COMPONENT_LIMIT
    return {'available': True, 'design_type': 'parametric' if parametric else 'direct',
            'is_parametric': parametric, 'units': design.unitsManager.defaultLengthUnits,
            'root_counts': root_counts, 'occurrence_count': root.allOccurrences.count,
            'timeline_count': design.timeline.count if parametric else None,
            'parameter_count': design.userParameters.count if parametric else None,
            'component_count': all_components.count, 'components_sampled': len(components),
            'components_truncated': truncated, 'sampled_component_counts': totals,
            'feature_and_sketch_health': {**health, 'checked': checked, 'recomputed': False,
                'truncated': truncated or checked < totals['features'] + totals['sketches']}}


def run(_ctx):
    if not _absolute_here(PATH):
        _out(ok=False, error='invalid_path', detail='path must be absolute on the Fusion host',
             mutation_state='not_started')
        return
    app = adsk.core.Application.get()
    if not os.path.isfile(PATH):
        _out(ok=False, error='archive_file_not_found', mutation_state='not_started')
        return
    prior = app.activeDocument
    prior_id = prior.creationId if prior is not None else None
    before_ids = {app.documents.item(i).creationId for i in range(app.documents.count)}
    attempted = False
    document = None
    try:
        # Hash the exact snapshot passed to Fusion rather than reopening the
        # source for import after hashing it.
        digest = hashlib.sha256()
        size = 0
        with tempfile.TemporaryDirectory(prefix='fusion-open-') as staging:
            staged = os.path.join(staging, os.path.basename(PATH))
            with open(PATH, 'rb') as source, open(staged, 'wb') as target:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    target.write(block)
                    digest.update(block)
                    size += len(block)
            if size == 0:
                _out(ok=False, error='archive_empty', mutation_state='not_started')
                return
            manager = app.importManager
            options = manager.createFusionArchiveImportOptions(staged)
            if options is None:
                _out(ok=False, error='archive_options_failed', mutation_state='not_started')
                return
            attempted = True
            doc = manager.importToNewDocument(options)
            if doc is None:
                _out(ok=False, error='archive_import_failed', document_may_have_opened=True)
                return
            doc_id = doc.creationId
            active = app.activeDocument
            document = {'creation_id': doc_id, 'name': doc.name, 'is_saved': bool(doc.isSaved),
                        'is_active': active is not None and active.creationId == doc_id}
            after_ids = {app.documents.item(i).creationId for i in range(app.documents.count)}
            if not doc_id or doc_id in before_ids or doc_id not in after_ids or doc.isSaved:
                _out(ok=False, error='archive_document_unverified', document=document,
                     document_may_have_opened=True)
                return
            if prior_id is not None and prior_id not in after_ids:
                _out(ok=False, error='previous_document_unverified', document=document,
                     document_may_have_opened=True)
                return
        try:
            summary = _summary(doc)
        except Exception as exc:
            summary = {'available': False, 'reason': str(exc)}
        _out(ok=True, path=PATH, document=document, design=summary, source_bytes=size,
             source_sha256=digest.hexdigest(), previous_document_still_open=prior_id is not None)
    except Exception as exc:
        _out(ok=False, error='archive_open_failed', detail=str(exc), document=document,
             document_may_have_opened=attempted, mutation_state='unknown' if attempted else 'not_started')
"""


_SAVE_VIEWPORT = """
import math
import struct
import zlib


class _ImageError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _verify_png(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as image:
        def read_exact(size):
            data = image.read(size)
            if len(data) != size:
                raise _ImageError('viewport_png_invalid', 'PNG is truncated')
            digest.update(data)
            return data

        if read_exact(8) != b'\\x89PNG\\r\\n\\x1a\\n':
            raise _ImageError('viewport_png_invalid', 'PNG signature is missing')
        size, kind = struct.unpack('>I4s', read_exact(8))
        if kind != b'IHDR' or size != 13:
            raise _ImageError('viewport_png_invalid', 'PNG IHDR is missing or invalid')
        header = read_exact(size)
        if zlib.crc32(kind + header) != struct.unpack('>I', read_exact(4))[0]:
            raise _ImageError('viewport_png_invalid', 'PNG IHDR checksum mismatch')
        if struct.unpack('>II', header[:8]) != (WIDTH, HEIGHT):
            raise _ImageError('viewport_size_mismatch', 'PNG dimensions do not match the request')
        data_size = 0
        while True:
            size, kind = struct.unpack('>I4s', read_exact(8))
            if kind == b'IHDR' or (kind == b'IEND' and size != 0):
                raise _ImageError('viewport_png_invalid', 'Unexpected PNG chunk')
            if kind == b'IDAT':
                data_size += size
            crc = zlib.crc32(kind)
            remaining = size
            while remaining:
                block = read_exact(min(remaining, 1024 * 1024))
                crc = zlib.crc32(block, crc)
                remaining -= len(block)
            if crc != struct.unpack('>I', read_exact(4))[0]:
                raise _ImageError('viewport_png_invalid', 'PNG chunk checksum mismatch')
            if kind == b'IEND':
                size = image.tell()
                if not data_size or image.read(1):
                    raise _ImageError('viewport_png_invalid', 'PNG image data or trailer is invalid')
                return size, digest.hexdigest()


def _camera_state(camera):
    kind = camera.cameraType
    values = (*camera.eye.asArray(), *camera.target.asArray(), *camera.upVector.asArray())
    if kind == adsk.core.CameraTypes.OrthographicCameraType:
        ok, width, height = camera.getExtents()
        if not ok:
            raise _ImageError('viewport_camera_unavailable', 'Could not read orthographic extents')
        values += (width, height)
    else:
        values += (camera.perspectiveAngle,)
    return kind, values


def _render(viewport, path):
    if ORIENTATION is None and not FIT:
        if not viewport.saveAsImageFile(path, WIDTH, HEIGHT):
            raise _ImageError('viewport_save_failed', 'Fusion refused the viewport image export')
        return
    original = viewport.camera  # Autodesk returns a copy.
    original_state = _camera_state(original)
    try:
        if ORIENTATION is not None:
            camera = viewport.camera
            camera.isSmoothTransition = False
            camera.viewOrientation = getattr(adsk.core.ViewOrientations, ORIENTATION)
            viewport.camera = camera
        if FIT and not viewport.fit():
            raise _ImageError('viewport_fit_failed', 'Fusion could not fit the requested view')
        if not viewport.refresh() or not viewport.saveAsImageFile(path, WIDTH, HEIGHT):
            raise _ImageError('viewport_save_failed', 'Fusion could not render the requested view')
    finally:
        try:
            original.isFitView = False
            original.isSmoothTransition = False
            viewport.camera = original
            if not viewport.refresh():
                raise RuntimeError('Viewport refresh failed')
            restored = _camera_state(viewport.camera)
            if restored[0] != original_state[0] or not all(
                math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-8) for a, b in zip(restored[1], original_state[1])
            ):
                raise RuntimeError('Camera readback did not match')
        except Exception as exc:
            raise _ImageError('viewport_camera_restore_failed', str(exc)) from exc


def run(_ctx):
    if not _absolute_here(PATH):
        _out(ok=False, error='invalid_path', detail='path must be absolute on the Fusion host',
             file_written=False, mutation_state='not_started')
        return
    viewport = adsk.core.Application.get().activeViewport
    if viewport is None:
        _out(ok=False, error='no_active_viewport', file_written=False, mutation_state='not_started')
        return
    parent = os.path.dirname(PATH)
    if not os.path.isdir(parent):
        _out(ok=False, error='viewport_parent_not_found', file_written=False, mutation_state='not_started')
        return
    published = False
    try:
        with tempfile.TemporaryDirectory(prefix='.fusion-viewport-', dir=parent) as staging:
            staged = os.path.join(staging, 'viewport.png')
            _render(viewport, staged)
            size, digest = _verify_png(staged)
            os.replace(staged, PATH)
            published = True
        _out(ok=True, path=PATH, format='png', width=WIDTH, height=HEIGHT,
             bytes_written=size, sha256=digest, direction=DIRECTION, fit=FIT,
             camera_restored=ORIENTATION is not None or FIT, file_written=True, model_changed=False)
    except Exception as exc:
        _out(ok=False, error=getattr(exc, 'code', 'viewport_save_failed'), detail=str(exc),
             file_written=published, model_changed=False)
"""


def _run(adapter: FusionAdapter, script: str, tool: str, *, writes_model: bool,
         operation_id: str | None) -> Envelope:
    env = execute_typed_script(adapter, script, operation_id=operation_id)
    if not env.ok:
        if not writes_model and env.state is not None:
            written = env.result.get("file_written") if env.result else None
            may_have_written = (env.state.get("execution_outcome") == "unknown"
                                if written is None else written)
            env.state = {**env.state, "model_may_have_changed": False,
                         "external_write_may_have_occurred": may_have_written}
        return env
    result = parse_status_json(env)
    if result is None:
        failure = script_parse_failure(env, f"{tool}_parse_failed", writes_model=writes_model)
        if not writes_model and failure.state is not None:
            failure.state["external_write_may_have_occurred"] = True
        return failure
    if result["ok"]:
        state = env.state
        if not writes_model and state is not None:
            state = {**state, "model_may_have_changed": False}
        return Envelope(ok=True, result=result, state=state)
    return Envelope(ok=False, error=result.get("error", f"{tool}_failed"), result=result, state=env.state)


def open_local_archive(adapter: FusionAdapter, path: str, *, operation_id: str | None = None) -> Envelope:
    """Open a local F3D as a separate unsaved Fusion document."""
    if error := _local_path(path, ".f3d"):
        return Envelope(ok=False, error="invalid_path", message=error)
    return _run(adapter, _HEADER + f"\nPATH = {path!r}\n" + _OPEN_ARCHIVE, "open_local_archive",
                writes_model=True, operation_id=operation_id)


def save_viewport(
    adapter: FusionAdapter, path: str, width: int = 1600, height: int = 1000,
    direction: str = "current", fit: bool = False, *, operation_id: str | None = None,
) -> Envelope:
    """Save a PNG in an existing local folder and restore any temporary camera change."""
    if error := _local_path(path, ".png"):
        return Envelope(ok=False, error="invalid_path", message=error)
    if any(type(size) is not int or not 1 <= size <= 8192 for size in (width, height)):
        return Envelope(ok=False, error="invalid_size", message="width and height must be integers from 1 to 8192")
    if not isinstance(direction, str) or direction not in VALID_DIRECTIONS:
        return Envelope(ok=False, error="invalid_direction", message=f"direction must be one of {sorted(VALID_DIRECTIONS)}")
    if type(fit) is not bool:
        return Envelope(ok=False, error="invalid_fit", message="fit must be a boolean")
    orientation = None if direction == "current" else "".join(part.title() for part in direction.split("-")) + "ViewOrientation"
    options = f"\nPATH = {path!r}\nWIDTH = {width}\nHEIGHT = {height}\nDIRECTION = {direction!r}\nORIENTATION = {orientation!r}\nFIT = {fit!r}\n"
    return _run(adapter, _HEADER + options + _SAVE_VIEWPORT, "save_viewport",
                writes_model=False, operation_id=operation_id)
