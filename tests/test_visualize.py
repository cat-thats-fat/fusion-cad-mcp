"""Tests for screenshot + set_view wrappers."""

from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from types import ModuleType, SimpleNamespace

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import visualize as viz


def test_screenshot_invalid_direction_short_circuits():
    class A:
        def read_query(self, *a, **kw):
            raise AssertionError("must not reach Fusion")

    env = viz.screenshot(A(), direction="diagonal")
    assert env.ok is False
    assert env.error == "invalid_direction"


def test_screenshot_passes_through_image_envelope():
    image = Envelope(ok=True, image={"data": "AAAA", "mime_type": "image/png"})

    class A:
        def read_query(self, qt: str, **kw):
            assert qt == "screenshot"
            assert kw == {
                "direction": "iso-top-right", "width": 400, "height": 300,
                "transparentBackground": False, "antiAliasing": False,
            }
            return image

    env = viz.screenshot(A(), width=400, height=300, transparent=False, anti_aliasing=False)
    assert env is image


def test_screenshot_omits_width_height_when_none():
    captured: dict = {}

    class A:
        def read_query(self, qt: str, **kw):
            assert qt == "screenshot"
            captured.update(kw)
            return Envelope(ok=True, image={"data": "AAAA", "mime_type": "image/png"})

    assert viz.screenshot(A(), direction="front").ok
    assert captured == {"direction": "front", "transparentBackground": True, "antiAliasing": True}


def test_screenshot_rejects_success_without_an_image():
    class A:
        def read_query(self, qt: str, **kw):
            return Envelope(ok=True, message="no data returned")

    env = viz.screenshot(A())
    assert env.ok is False and env.error == "screenshot_missing_image"
    assert env.message == "no data returned"


@pytest.mark.parametrize(
    ("direction", "fit", "camera_events"),
    [
        ("top", True, ["get_camera", ("set_camera", "TOP"), "fit", "refresh"]),
        ("current", True, ["fit", "refresh"]),
        ("iso-top-right", False, ["get_camera", ("set_camera", "ISO"), "refresh"]),
    ],
)
def test_set_view_uses_camera_copy_and_obeys_fit(monkeypatch, direction, fit, camera_events):
    events = []

    class Viewport:
        __slots__ = ()

        @property
        def camera(self):
            events.append("get_camera")
            return SimpleNamespace(viewOrientation="ORIGINAL")

        @camera.setter
        def camera(self, value):
            events.append(("set_camera", value.viewOrientation))

        def fit(self):
            events.append("fit")

        def refresh(self):
            events.append("refresh")

    adsk = ModuleType("adsk")
    adsk.core = ModuleType("adsk.core")
    adsk.core.Application = SimpleNamespace(get=lambda: SimpleNamespace(activeViewport=Viewport()))
    adsk.core.ViewOrientations = SimpleNamespace(
        TopViewOrientation="TOP", IsoTopRightViewOrientation="ISO"
    )
    adsk.fusion = ModuleType("adsk.fusion")
    for module in (adsk, adsk.core, adsk.fusion):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    class Adapter:
        def execute_script(self, script):
            namespace = {}
            exec(script, namespace)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                namespace["run"](None)
            return Envelope(ok=True, message=stdout.getvalue())

    env = viz.set_view(Adapter(), direction, fit)
    assert env.result == {"ok": True, "direction": direction, "fit": fit}
    assert events == camera_events


def test_set_view_invalid_direction_short_circuits():
    class A:
        def execute_script(self, s: str):
            raise AssertionError("must not reach Fusion")

    env = viz.set_view(A(), direction="bogus")
    assert env.ok is False
    assert env.error == "invalid_direction"


@pytest.mark.parametrize(
    ("stdout", "error"),
    [("invalid", "set_view_parse_failed"), ('{"ok":false,"error":"no_active_viewport"}', "no_active_viewport")],
)
def test_set_view_rejects_unsuccessful_stdout(stdout, error):
    class A:
        def execute_script(self, s: str):
            return Envelope(ok=True, message=stdout)

    env = viz.set_view(A(), direction="top")
    assert env.ok is False and env.error == error
    assert env.message == stdout


# ---------- screenshot_compare_with_marker ----------


class _CompareAdapter:
    """Track the simulated marker position and record each call."""

    def __init__(
        self,
        current_pos: int,
        count: int,
        before_png: str,
        after_png: str,
        fail_at: str | None = None,
    ):
        self.calls: list[str] = []
        self._current_pos = current_pos
        self._count = count
        self._before_png = before_png
        self._after_png = after_png
        self._screenshot_idx = 0
        self._marker_set_idx = 0
        self.fail_at = fail_at
        self.captured_positions: list[int] = []

    def execute_script(self, script: str) -> Envelope:
        # First execute_script call reads the current marker
        if "tl.markerPosition" in script and "target =" not in script:
            self.calls.append("get_marker")
            if self.fail_at == "get_marker":
                return Envelope(ok=False, error="boom")
            return Envelope(
                ok=True,
                message=json.dumps({"ok": True, "pos": self._current_pos, "count": self._count}),
            )
        # Otherwise it's a set-marker call
        self._marker_set_idx += 1
        label = f"set_marker_{self._marker_set_idx}"
        self.calls.append(label)
        if self.fail_at == label:
            return Envelope(ok=False, error="boom")
        self._current_pos = int(re.search(r"target = (\d+)", script)[1])
        return Envelope(
            ok=True,
            message=json.dumps({"ok": True, "pos": self._current_pos, "count": self._count}),
        )

    def read_query(self, qt: str, **kw) -> Envelope:
        self._screenshot_idx += 1
        label = f"screenshot_{self._screenshot_idx}"
        self.calls.append(label)
        self.captured_positions.append(self._current_pos)
        if self.fail_at == label:
            return Envelope(ok=False, error="boom")
        png = self._before_png if self._screenshot_idx == 1 else self._after_png
        return Envelope(ok=True, image={"data": png, "mime_type": "image/png"})


def test_compare_invalid_direction_short_circuits():
    a = _CompareAdapter(45, 50, "A", "B")
    env = viz.screenshot_compare_with_marker(a, 30, direction="diagonal")
    assert env.ok is False
    assert env.error == "invalid_direction"
    assert a.calls == []  # no adapter calls made


def test_compare_invalid_marker_position_short_circuits():
    a = _CompareAdapter(45, 50, "A", "B")
    env = viz.screenshot_compare_with_marker(a, 99, direction="iso-top-right")
    assert env.ok is False
    assert env.error == "invalid_marker_position"
    # get-marker happens before the position check (we need tl.count first)
    assert a.calls == ["get_marker"]


def test_compare_returns_both_images_and_marker_positions():
    a = _CompareAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG")
    env = viz.screenshot_compare_with_marker(a, 30, direction="iso-top-right")
    assert env.ok is True
    assert env.result["before_marker"] == 30
    assert env.result["after_marker"] == 45
    assert env.result["direction"] == "iso-top-right"
    assert env.result["before_image"]["data"] == "BEFORE_PNG"
    assert env.result["after_image"]["data"] == "AFTER_PNG"
    assert a.captured_positions == [30, 45]
    # Verify call sequence: get -> set(before) -> shot -> set(after) -> shot
    assert a.calls == [
        "get_marker",
        "set_marker_1",
        "screenshot_1",
        "set_marker_2",
        "screenshot_2",
    ]


def test_compare_restores_marker_on_before_screenshot_failure():
    """If the before-shot fails, the marker is at the rolled-back position;
    the tool must attempt to restore it before returning."""
    a = _CompareAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG", fail_at="screenshot_1")
    env = viz.screenshot_compare_with_marker(a, 30, direction="iso-top-right")
    assert env.ok is False
    assert env.error == "before_screenshot_failed"
    assert a.calls == ["get_marker", "set_marker_1", "screenshot_1", "set_marker_2"]
    assert a._current_pos == 45


def test_compare_surfaces_restore_marker_failed_distinctly():
    """If the restore (forward roll) itself fails, the user needs to know
    the design may be left mid-rebuild."""
    a = _CompareAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG", fail_at="set_marker_2")
    env = viz.screenshot_compare_with_marker(a, 30, direction="iso-top-right")
    assert env.ok is False
    assert env.error == "restore_marker_failed"
    assert env.result["after_marker_target"] == 45
    assert a.captured_positions == [30]


def test_compare_marker_position_zero_is_valid():
    """marker=0 (empty initial state) must be accepted, not rejected as falsy."""
    a = _CompareAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG")
    env = viz.screenshot_compare_with_marker(a, 0, direction="iso-top-right")
    assert env.ok is True
    assert env.result["before_marker"] == 0
    assert a.captured_positions == [0, 45]


def test_compare_restores_after_screenshot_transport_timeout():
    class TimeoutAdapter(_CompareAdapter):
        def read_query(self, qt: str, **kw) -> Envelope:
            raise TimeoutError("fixture timed out")

    adapter = TimeoutAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG")
    env = viz.screenshot_compare_with_marker(adapter, 30)
    assert env.error == "before_screenshot_failed"
    assert env.message == "fixture timed out"
    assert adapter.calls == ["get_marker", "set_marker_1", "set_marker_2"]
    assert adapter._current_pos == 45


def test_compare_restores_even_when_unexpected_exception_propagates():
    class RaisingAdapter(_CompareAdapter):
        def read_query(self, qt: str, **kw) -> Envelope:
            raise RuntimeError("unexpected failure")

    adapter = RaisingAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG")
    with pytest.raises(RuntimeError, match="unexpected failure"):
        viz.screenshot_compare_with_marker(adapter, 30)
    assert adapter._current_pos == 45


@pytest.mark.parametrize("restore_stdout", ['{"ok":false,"error":"no_active_design"}', '{"ok":true,"pos":30}', 'invalid'])
def test_compare_rejects_unverified_inner_restoration(restore_stdout):
    class RestoreAdapter(_CompareAdapter):
        def execute_script(self, script: str) -> Envelope:
            if "target = 45" in script:
                return Envelope(ok=True, message=restore_stdout)
            return super().execute_script(script)

    adapter = RestoreAdapter(45, 50, "BEFORE_PNG", "AFTER_PNG", fail_at="screenshot_1")
    env = viz.screenshot_compare_with_marker(adapter, 30)
    assert env.error == "restore_marker_failed"
    assert env.result["after_marker_target"] == 45
    assert env.result["before_failure"]["error"] == "before_screenshot_failed"
    assert env.result["restore"]["error"] == "marker_move_unverified"
