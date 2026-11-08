"""Group 6 / visualize tools.

screenshot via fusion_mcp_read (Fusion handles the fit-view internally).
set_view via fusion_mcp_execute (no direct read tool; uses the active viewport).
"""

from __future__ import annotations

from ..adapter import AdapterError, FusionAdapter
from ..envelope import Envelope, parse_stdout_json

VALID_DIRECTIONS = {
    "current",
    "front",
    "back",
    "bottom",
    "top",
    "left",
    "right",
    "iso-bottom-left",
    "iso-bottom-right",
    "iso-top-left",
    "iso-top-right",
}


def screenshot(
    adapter: FusionAdapter,
    direction: str = "iso-top-right",
    width: int | None = None,
    height: int | None = None,
    transparent: bool = True,
    anti_aliasing: bool = True,
) -> Envelope:
    """Capture a screenshot of the active viewport.

    Fusion's MCP returns PNG image content; the envelope carries it under .image.
    """
    if direction not in VALID_DIRECTIONS:
        return Envelope(
            ok=False,
            error="invalid_direction",
            message=f"direction must be one of {sorted(VALID_DIRECTIONS)}, got {direction!r}",
        )
    kwargs: dict = {
        "direction": direction,
        "transparentBackground": transparent,
        "antiAliasing": anti_aliasing,
    }
    if width is not None:
        kwargs["width"] = width
    if height is not None:
        kwargs["height"] = height
    env = adapter.read_query("screenshot", **kwargs)
    if env.ok and env.image is None:
        return Envelope(ok=False, error="screenshot_missing_image", message=env.message)
    return env


def build_set_view(direction: str, fit: bool = True) -> str:
    """Generate a script that orients the viewport to a named direction.

    Uses ViewOrientations enum from adsk.core. 'current' is a no-op.
    """
    enum_map = {
        "top": "TopViewOrientation",
        "bottom": "BottomViewOrientation",
        "left": "LeftViewOrientation",
        "right": "RightViewOrientation",
        "front": "FrontViewOrientation",
        "back": "BackViewOrientation",
        "iso-top-right": "IsoTopRightViewOrientation",
        "iso-top-left": "IsoTopLeftViewOrientation",
        "iso-bottom-right": "IsoBottomRightViewOrientation",
        "iso-bottom-left": "IsoBottomLeftViewOrientation",
    }
    if direction == "current":
        # Just fit, no orient
        orient_block = "# direction='current' — no orientation change"
    else:
        enum_name = enum_map.get(direction)
        if enum_name is None:
            # generator-time validation also; runtime fallthrough returns clean error
            orient_block = (
                f"print(json.dumps({{'ok': False, 'error': 'invalid_direction',"
                f" 'direction': {direction!r}}})); return"
            )
        else:
            orient_block = (
                "camera = vp.camera\n"
                f"    camera.viewOrientation = adsk.core.ViewOrientations.{enum_name}\n"
                "    vp.camera = camera"
            )
    fit_block = "vp.fit()" if fit else ""
    return f"""
import adsk.core
import adsk.fusion
import json

def run(_ctx):
    app = adsk.core.Application.get()
    vp = app.activeViewport
    if vp is None:
        print(json.dumps({{"ok": False, "error": "no_active_viewport"}}))
        return
    {orient_block}
    {fit_block}
    vp.refresh()
    print(json.dumps({{
        "ok": True,
        "direction": {direction!r},
        "fit": {fit!r},
    }}))
""".strip()


def set_view(
    adapter: FusionAdapter, direction: str = "iso-top-right", fit: bool = True
) -> Envelope:
    if direction not in VALID_DIRECTIONS:
        return Envelope(
            ok=False,
            error="invalid_direction",
            message=f"direction must be one of {sorted(VALID_DIRECTIONS)}, got {direction!r}",
        )
    env = adapter.execute_script(build_set_view(direction, fit))
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is None:
        return Envelope(ok=False, error="set_view_parse_failed", message=env.message)
    if parsed.get("ok") is not True:
        return Envelope(ok=False, error=parsed.get("error", "set_view_failed"), message=env.message)
    return Envelope(ok=True, message=env.message, result=parsed)


def _get_marker_script() -> str:
    """Emit a script that prints {pos, count} of the active design's timeline."""
    return r"""
import adsk.core
import adsk.fusion
import json

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({"ok": False, "error": "no_active_design"}))
        return
    tl = design.timeline
    print(json.dumps({"ok": True, "pos": tl.markerPosition, "count": tl.count}))
""".strip()


def _set_marker_script(target_pos: int) -> str:
    """Emit a script that sets the timeline marker to the exact requested position."""
    return f"""
import adsk.core
import adsk.fusion
import json

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({{"ok": False, "error": "no_active_design"}}))
        return
    tl = design.timeline
    target = {target_pos}
    if not 0 <= target <= tl.count:
        print(json.dumps({{"ok": False, "error": "invalid_marker_position"}}))
        return
    tl.markerPosition = target
    app.activeViewport.refresh()
    print(json.dumps({{"ok": True, "pos": tl.markerPosition, "count": tl.count}}))
""".strip()


def _move_marker(adapter: FusionAdapter, target: int) -> Envelope:
    try:
        env = adapter.execute_script(_set_marker_script(target))
    except (AdapterError, TimeoutError) as exc:
        return Envelope(ok=False, error="marker_move_failed", message=str(exc))
    if not env.ok:
        return env
    parsed = parse_stdout_json(env)
    if parsed is not None and parsed.get("ok") is True:
        actual = parsed.get("pos")
        if type(actual) is int and actual == target:
            return env
    return Envelope(ok=False, error="marker_move_unverified", message=env.message, result=parsed)


def screenshot_compare_with_marker(
    adapter: FusionAdapter,
    before_marker_position: int,
    direction: str = "iso-top-right",
    width: int | None = None,
    height: int | None = None,
    transparent: bool = True,
    anti_aliasing: bool = True,
) -> Envelope:
    """Capture before/after screenshots with the same view direction by rolling the
    timeline marker to `before_marker_position`, screenshotting, then rolling
    forward to the original position and screenshotting again.

    Restore-on-error semantics: if anything fails after we've moved the marker,
    we attempt to restore it before returning. Result envelope carries both
    base64-encoded PNGs so the agent can render or save either independently.

    Returns envelope.result = {
        before_image: {data, mime_type},
        after_image:  {data, mime_type},
        before_marker: int,
        after_marker:  int,
        direction:     str,
    }
    """
    if direction not in VALID_DIRECTIONS:
        return Envelope(
            ok=False,
            error="invalid_direction",
            message=f"direction must be one of {sorted(VALID_DIRECTIONS)}, got {direction!r}",
        )

    if type(before_marker_position) is not int or before_marker_position < 0:
        return Envelope(
            ok=False,
            error="invalid_marker_position",
            message="before_marker_position must be a nonnegative integer.",
        )

    try:
        cur_env = adapter.execute_script(_get_marker_script())
    except (AdapterError, TimeoutError) as exc:
        return Envelope(ok=False, error="marker_read_failed", message=str(exc))
    if not cur_env.ok:
        return cur_env
    cur_parsed = parse_stdout_json(cur_env) or {}
    after_pos = cur_parsed.get("pos")
    tl_count = cur_parsed.get("count")
    if (
        cur_parsed.get("ok") is not True
        or type(after_pos) is not int
        or type(tl_count) is not int
        or not 0 <= after_pos <= tl_count
    ):
        return Envelope(
            ok=False,
            error="marker_read_failed",
            message=cur_env.message,
            result=cur_parsed,
        )
    if before_marker_position > tl_count:
        return Envelope(
            ok=False,
            error="invalid_marker_position",
            message=f"before_marker_position must be in [0, {tl_count}], got {before_marker_position}",
        )

    def capture(phase: str) -> Envelope:
        try:
            env = screenshot(adapter, direction, width, height, transparent, anti_aliasing)
        except (AdapterError, TimeoutError) as exc:
            return Envelope(ok=False, error=f"{phase}_screenshot_failed", message=str(exc))
        if not env.ok:
            return Envelope(
                ok=False, error=f"{phase}_screenshot_failed", message=env.message, result=env.to_dict()
            )
        return env

    # Even a failed or timed-out move may have changed the marker in Fusion.
    try:
        before_env = _move_marker(adapter, before_marker_position)
        if before_env.ok:
            before_env = capture("before")
    finally:
        restored = _move_marker(adapter, after_pos)

    if not restored.ok:
        details = {
            "before_marker": before_marker_position,
            "after_marker_target": after_pos,
            "restore": restored.to_dict(),
        }
        if not before_env.ok:
            details["before_failure"] = before_env.to_dict()
        return Envelope(
            ok=False,
            error="restore_marker_failed",
            message=f"Could not verify restoration to timeline marker {after_pos}. {restored.message}".strip(),
            result=details,
        )
    if not before_env.ok:
        return before_env

    after_env = capture("after")
    if not after_env.ok:
        return after_env

    return Envelope(
        ok=True,
        result={
            "before_image": before_env.image,
            "after_image": after_env.image,
            "before_marker": before_marker_position,
            "after_marker": after_pos,
            "direction": direction,
        },
    )


__all__ = [
    "VALID_DIRECTIONS",
    "build_set_view",
    "screenshot",
    "screenshot_compare_with_marker",
    "set_view",
]
