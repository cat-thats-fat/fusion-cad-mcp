"""Exercise the actual FastMCP registration and client-side response."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from fusion_cad_mcp import server
from fusion_cad_mcp.envelope import Envelope

_IMAGE_A = {"data": "cG5nLWE=", "mime_type": "image/png"}
_IMAGE_B = {"data": "cG5nLWI=", "mime_type": "image/png"}


@pytest.mark.asyncio
async def test_screenshot_wire_reply_contains_native_images_and_compact_metadata(monkeypatch):
    class Adapter:
        def read_query(self, query, **kwargs):
            assert query == "screenshot"
            assert kwargs["width"] == 400
            return Envelope(ok=True, message="caption", image=_IMAGE_A, images=[_IMAGE_A, _IMAGE_B])

    monkeypatch.setattr(server, "_adapter", Adapter())
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        reply = await client.call_tool("screenshot", {"width": 400})
    assert reply.isError is False
    assert reply.structuredContent == {"ok": True, "message": "caption"}
    assert reply.content[0].type == "text"
    assert json.loads(reply.content[0].text) == reply.structuredContent
    assert [(block.type, block.mimeType, block.data) for block in reply.content[1:]] == [
        ("image", "image/png", _IMAGE_A["data"]),
        ("image", "image/png", _IMAGE_B["data"]),
    ]


@pytest.mark.asyncio
async def test_compare_wire_reply_links_before_and_after_native_images(monkeypatch):
    def compare(adapter, before, direction, width, height, transparent, antialiasing):
        assert before == 3
        return Envelope(
            ok=True,
            result={
                "before_image": _IMAGE_A,
                "after_image": _IMAGE_B,
                "before_marker": before,
                "after_marker": 5,
                "direction": direction,
            },
        )

    monkeypatch.setattr(server, "_adapter", object())
    monkeypatch.setattr(server.viz_tools, "screenshot_compare_with_marker", compare)
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        reply = await client.call_tool(
            "screenshot_compare_with_marker", {"before_marker_position": 3, "direction": "front"}
        )
    assert not reply.isError
    assert reply.structuredContent["result"] == {
        "before_image": {"content_index": 1},
        "after_image": {"content_index": 2},
        "before_marker": 3,
        "after_marker": 5,
        "direction": "front",
    }
    assert json.loads(reply.content[0].text) == reply.structuredContent
    assert [(block.type, block.data) for block in reply.content[1:]] == [
        ("image", _IMAGE_A["data"]), ("image", _IMAGE_B["data"])
    ]


@pytest.mark.asyncio
async def test_screenshot_wire_failure_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(server, "_adapter", object())
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        reply = await client.call_tool("screenshot", {"direction": "diagonal"})
    assert reply.isError is True
    assert reply.structuredContent["ok"] is False
    assert reply.structuredContent["error"] == "invalid_direction"
    assert [block.type for block in reply.content] == ["text"]


@pytest.mark.parametrize("run_fails", [False, True])
def test_main_closes_adapter_when_server_exits(monkeypatch, run_fails):
    events = []

    class Adapter:
        def close(self):
            events.append("close")

    def run():
        events.append("run")
        if run_fails:
            raise RuntimeError("fixture server failed")

    monkeypatch.setattr(server.sys, "argv", ["fusion-cad-mcp"])
    monkeypatch.setattr(server, "_adapter", Adapter())
    monkeypatch.setattr(server.mcp, "run", run)
    if run_fails:
        with pytest.raises(RuntimeError, match="fixture server failed"):
            server.main()
    else:
        server.main()
    assert events == ["run", "close"]
    assert server._adapter is None
