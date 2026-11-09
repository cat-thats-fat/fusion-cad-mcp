"""Exercise local file workflows with generated scripts and Fusion API doubles."""

import copy
import hashlib
import os
import struct
import uuid
import zlib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.receipts import run_typed
from fusion_cad_mcp.tools import local_files as local
from tests._assembly_fakes import Collection, NoScriptAdapter, ScriptAdapter


class RecordingAdapter(ScriptAdapter):
    def __init__(self, monkeypatch, *, app, core=None, fusion=None):
        super().__init__(monkeypatch, None, app=app, core=core, fusion=fusion)
        self.read_only = []

    def execute_script(self, script, *, read_only=False):
        self.read_only.append(read_only)
        return super().execute_script(script, read_only=read_only)


def _component(name, states=()):
    return NS(name=name, bRepBodies=Collection([object()]), sketches=Collection([NS(healthState=0)]),
              features=Collection(NS(healthState=state) for state in states), allOccurrences=Collection())


def _archive_app(monkeypatch, *, parametric=True, states=(0, 1)):
    original = NS(name="Original", creationId="old", isSaved=True, isModified=True, close=Mock(), save=Mock())
    root = _component("Root", states)
    design = NS(rootComponent=root, designType="parametric" if parametric else "direct",
                allComponents=Collection([root]), unitsManager=NS(defaultLengthUnits="mm"))
    if parametric:
        design.timeline = Collection([object()])
        design.userParameters = Collection([object(), object()])
    opened = NS(name="Imported", creationId="new", isSaved=False, close=Mock(),
                products=NS(itemByProductType=Mock(return_value=design)))
    manager = NS(createFusionArchiveImportOptions=Mock(side_effect=lambda path: NS(path=path)),
                 importToNewDocument=Mock())
    app = NS(activeDocument=original, documents=Collection([original]), importManager=manager)

    def import_document(options):
        app.staged_path = Path(options.path)
        app.imported_bytes = app.staged_path.read_bytes()
        app.documents.add(opened)
        app.activeDocument = opened
        return opened

    manager.importToNewDocument.side_effect = import_document
    adapter = RecordingAdapter(monkeypatch, app=app,
                               fusion={"DesignTypes": NS(ParametricDesignType="parametric")})
    return adapter, app, original, opened, design


@pytest.mark.parametrize("path", ["model.f3d", "C:/temp/../model.f3d", "C:/temp/model.f3z",
                                  "C:/temp/model.step", "C:/temp/NUL.f3d", "//server/share/model.f3d",
                                  r"/\server\share\model.f3d", "C:/temp:stream/model.f3d"])
def test_archive_refuses_nonlocal_or_unsupported_path_before_dispatch(path):
    assert local.open_local_archive(NoScriptAdapter(), path).error == "invalid_path"


def test_open_archive_hashes_exact_import_bytes_and_preserves_previous_document(monkeypatch, tmp_path):
    adapter, app, original, opened, _ = _archive_app(monkeypatch)
    path = tmp_path / "Case's Ω.F3D"
    data = b"opaque local archive test bytes"
    path.write_bytes(data)

    result = local.open_local_archive(adapter, str(path))

    assert result.ok, result.result
    assert result.result["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert result.result["source_bytes"] == len(data)
    assert app.imported_bytes == path.read_bytes() == data
    assert app.staged_path != path and not app.staged_path.exists()
    assert result.result["document"] == {"creation_id": "new", "name": "Imported", "is_saved": False, "is_active": True}
    assert result.result["previous_document_still_open"] and app.documents.items == [original, opened]
    assert result.result["design"]["root_counts"] == {"bodies": 1, "sketches": 1, "features": 2}
    assert result.result["design"]["feature_and_sketch_health"]["warning"] == 1
    assert result.result["design"]["feature_and_sketch_health"]["checked"] == 3
    assert result.result["design"]["feature_and_sketch_health"]["recomputed"] is False
    assert original.isModified and original.isSaved
    original.close.assert_not_called()
    original.save.assert_not_called()
    assert adapter.read_only == [False]


def test_open_archive_direct_design_keeps_parametric_fields_unavailable(monkeypatch, tmp_path):
    adapter, _, _, _, _ = _archive_app(monkeypatch, parametric=False)
    path = tmp_path / "archive.f3d"
    path.write_bytes(b"archive")

    result = local.open_local_archive(adapter, str(path))

    assert result.ok
    summary = result.result["design"]
    assert summary["design_type"] == "direct" and not summary["is_parametric"]
    assert summary["timeline_count"] is None and summary["parameter_count"] is None


def test_open_archive_bounds_health_sample(monkeypatch, tmp_path):
    adapter, _, _, _, design = _archive_app(monkeypatch, states=(0,) * 300)
    design.allComponents.items.extend(_component(f"Part {i}") for i in range(90))
    path = tmp_path / "archive.f3d"
    path.write_bytes(b"archive")

    summary = local.open_local_archive(adapter, str(path)).result["design"]

    assert summary["components_sampled"] <= 64 and summary["components_truncated"]
    assert summary["feature_and_sketch_health"]["checked"] == 256
    assert summary["feature_and_sketch_health"]["truncated"]


def test_archive_requires_new_identity_without_closing_returned_document(monkeypatch, tmp_path):
    adapter, app, original, _, _ = _archive_app(monkeypatch)
    app.importManager.importToNewDocument.side_effect = lambda _: original
    path = tmp_path / "archive.f3d"
    path.write_bytes(b"archive")

    result = local.open_local_archive(adapter, str(path))

    assert result.error == "archive_document_unverified"
    assert result.result["document_may_have_opened"]
    original.close.assert_not_called()
    original.save.assert_not_called()


@pytest.mark.parametrize(("create_file", "error"), [(False, "archive_file_not_found"), (True, "archive_empty")])
def test_archive_missing_or_empty_never_reaches_import(monkeypatch, tmp_path, create_file, error):
    adapter, app, _, _, _ = _archive_app(monkeypatch)
    path = tmp_path / "archive.f3d"
    if create_file:
        path.touch()
    assert local.open_local_archive(adapter, str(path)).error == error
    app.importManager.importToNewDocument.assert_not_called()


def test_absolute_path_must_be_native_on_fusion_host(monkeypatch):
    adapter = RecordingAdapter(monkeypatch, app=NS())
    prefix = "/tmp" if os.name == "nt" else "C:/temp"

    archive = local.open_local_archive(adapter, f"{prefix}/model.f3d")
    viewport = local.save_viewport(adapter, f"{prefix}/view.png")

    assert archive.error == viewport.error == "invalid_path"
    assert viewport.result["file_written"] is False


def _chunk(tag, data=b""):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def _png(width, height):
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = (b"\x00" + b"\x7f" * width) * height
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(pixels)) + _chunk(b"IEND")


def _vector(*values):
    return NS(asArray=lambda: values)


class _Viewport:
    def __init__(self, data):
        self.data = data
        self._camera = NS(cameraType=0, eye=_vector(1, 2, 3), target=_vector(0, 0, 0),
                          upVector=_vector(0, 1, 0), getExtents=lambda: (True, 10, 5),
                          viewOrientation="original")
        self.save_result = True
        self.captures = []
        self.fail_restore = False

    @property
    def camera(self):
        return copy.deepcopy(self._camera)

    @camera.setter
    def camera(self, camera):
        if self.fail_restore and camera.viewOrientation == "original":
            raise RuntimeError("camera restore refused")
        self._camera = copy.deepcopy(camera)

    def fit(self):
        self._camera.eye = _vector(8, 9, 10)
        return True

    def refresh(self):
        return True

    def saveAsImageFile(self, path, width, height):
        self.captures.append((Path(path), width, height, self.camera.viewOrientation, self.camera.eye.asArray()))
        Path(path).write_bytes(self.data)
        return self.save_result


def _viewport_adapter(monkeypatch, viewport):
    return RecordingAdapter(monkeypatch, app=NS(activeViewport=viewport),
                            core={"CameraTypes": NS(OrthographicCameraType=0),
                                  "ViewOrientations": NS(TopViewOrientation="top")})


@pytest.mark.parametrize(("path", "width", "height", "direction", "fit", "error"), [
    ("out.png", 2, 3, "current", False, "invalid_path"),
    ("C:/out.jpg", 2, 3, "current", False, "invalid_path"),
    ("//server/share/out.png", 2, 3, "current", False, "invalid_path"),
    ("C:/out.png", True, 3, "current", False, "invalid_size"),
    ("C:/out.png", 2, 8193, "current", False, "invalid_size"),
    ("C:/out.png", 2, 3, "tilted", False, "invalid_direction"),
    ("C:/out.png", 2, 3, "current", "yes", "invalid_fit"),
])
def test_viewport_invalid_input_never_dispatches(path, width, height, direction, fit, error):
    assert local.save_viewport(NoScriptAdapter(), path, width, height, direction, fit).error == error


def test_viewport_publishes_verified_png_and_restores_named_view(monkeypatch, tmp_path):
    data = _png(7, 4)
    viewport = _Viewport(data)
    adapter = _viewport_adapter(monkeypatch, viewport)
    path = tmp_path / "view.PNG"
    path.write_bytes(b"old")

    result = local.save_viewport(adapter, str(path), 7, 4, direction="top", fit=True)

    assert result.ok, result.result
    assert path.read_bytes() == data
    assert result.result["bytes_written"] == len(data)
    assert result.result["sha256"] == hashlib.sha256(data).hexdigest()
    assert (result.result["width"], result.result["height"]) == (7, 4)
    assert result.result["camera_restored"] and not result.result["model_changed"]
    assert adapter.read_only == [False]
    stage, width, height, view, eye = viewport.captures[0]
    assert stage != path and stage.parent.parent == tmp_path and not stage.parent.exists()
    assert (width, height, view, eye) == (7, 4, "top", (8, 9, 10))
    assert viewport.camera.viewOrientation == "original" and viewport.camera.eye.asArray() == (1, 2, 3)


@pytest.mark.parametrize(("data", "save_result", "error"), [
    (b"not a png", True, "viewport_png_invalid"),
    (_png(6, 4), True, "viewport_size_mismatch"),
    (_png(7, 4)[:-12], True, "viewport_png_invalid"),
    (_png(7, 4), False, "viewport_save_failed"),
])
def test_bad_staged_image_never_replaces_destination(monkeypatch, tmp_path, data, save_result, error):
    viewport = _Viewport(data)
    viewport.save_result = save_result
    path = tmp_path / "view.png"
    path.write_bytes(b"preserved")

    result = local.save_viewport(_viewport_adapter(monkeypatch, viewport), str(path), 7, 4,
                                 direction="top", fit=True)

    assert result.error == error and not result.result["file_written"]
    assert path.read_bytes() == b"preserved"
    assert not list(tmp_path.glob(".fusion-viewport-*"))
    assert viewport.camera.viewOrientation == "original" and viewport.camera.eye.asArray() == (1, 2, 3)


def test_camera_restore_failure_does_not_publish_staged_image(monkeypatch, tmp_path):
    viewport = _Viewport(_png(7, 4))
    viewport.fail_restore = True
    path = tmp_path / "view.png"
    path.write_bytes(b"preserved")

    result = local.save_viewport(_viewport_adapter(monkeypatch, viewport), str(path), 7, 4, direction="top")

    assert result.error == "viewport_camera_restore_failed" and not result.result["file_written"]
    assert path.read_bytes() == b"preserved"


def test_empty_viewport_result_reports_possible_file_write_without_replay(tmp_path):
    adapter = NS(execute_script=Mock(return_value=Envelope(ok=True)))
    result = local.save_viewport(adapter, str(tmp_path / "view.png"))

    assert result.error == "save_viewport_parse_failed"
    assert result.state == {"failure_kind": "protocol", "execution_outcome": "unknown",
                            "model_may_have_changed": False, "retry_safe": False,
                            "external_write_may_have_occurred": True}
    adapter.execute_script.assert_called_once()


def _with_receipts(adapter, journal):
    def send(script, *, read_only):
        adapter.execute_script(script, read_only=read_only)
        return Envelope(ok=True)  # Simulate Fusion completing without returning its stdout.

    def execute_typed(script, *, read_only, operation_id):
        return run_typed(send, script, "offline-fusion", 1, operation_id, read_only, journal)

    adapter.execute_typed_script = execute_typed
    return str(uuid.uuid4())


def test_archive_receipt_recovers_new_document_without_reopening(monkeypatch, tmp_path):
    adapter, app, _, _, _ = _archive_app(monkeypatch)
    operation_id = _with_receipts(adapter, tmp_path / "receipts")
    path = tmp_path / "case.f3d"
    path.write_bytes(b"case")

    opened = local.open_local_archive(adapter, str(path), operation_id=operation_id)
    replay = local.open_local_archive(adapter, str(path), operation_id=operation_id)

    assert opened.ok and opened.state["recovered_from_receipt"]
    assert opened.state["operation_id"] == operation_id
    assert replay.result == opened.result
    assert app.importManager.importToNewDocument.call_count == 1


def test_missing_archive_declares_no_import_and_does_not_block_new_operation(monkeypatch, tmp_path):
    adapter, app, _, _, _ = _archive_app(monkeypatch)
    operation_id = _with_receipts(adapter, tmp_path / "receipts")
    path = tmp_path / "case.f3d"

    missing = local.open_local_archive(adapter, str(path), operation_id=operation_id)
    assert missing.error == "archive_file_not_found"
    assert missing.state["receipt_status"] == "declared_not_started"
    app.importManager.importToNewDocument.assert_not_called()

    path.write_bytes(b"case")
    assert local.open_local_archive(adapter, str(path)).ok
    app.importManager.importToNewDocument.assert_called_once()


def test_viewport_receipt_recovers_verified_png_without_replacing_it_again(monkeypatch, tmp_path):
    viewport = _Viewport(_png(7, 4))
    adapter = _viewport_adapter(monkeypatch, viewport)
    operation_id = _with_receipts(adapter, tmp_path / "receipts")
    path = tmp_path / "view.png"

    result = local.save_viewport(adapter, str(path), 7, 4, operation_id=operation_id)
    replay = local.save_viewport(adapter, str(path), 7, 4, operation_id=operation_id)

    assert result.ok and result.state["recovered_from_receipt"]
    assert result.state["model_may_have_changed"] is False
    assert replay.result == result.result
    assert path.read_bytes() == viewport.data and len(viewport.captures) == 1


def test_viewport_declared_receipt_failure_distinguishes_no_file_publication(monkeypatch, tmp_path):
    viewport = _Viewport(b"invalid PNG")
    adapter = _viewport_adapter(monkeypatch, viewport)
    operation_id = _with_receipts(adapter, tmp_path / "receipts")
    path = tmp_path / "view.png"

    result = local.save_viewport(adapter, str(path), 7, 4, operation_id=operation_id)

    assert not result.ok and result.error == "viewport_png_invalid"
    assert result.state["operation_id"] == operation_id
    assert result.state["external_write_may_have_occurred"] is False and not path.exists()
