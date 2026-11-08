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
        ("image", _IMAGE_A["data"]),
        ("image", _IMAGE_B["data"]),
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


@pytest.mark.asyncio
async def test_new_tool_registration_and_wire_preflight(monkeypatch):
    class Adapter:
        def __init__(self):
            self.calls = []

        def execute_script(self, script, *, read_only=False):
            self.calls.append(read_only)
            return Envelope(ok=True, message='{"ok":true,"sections":{}}')

    adapter = Adapter()
    monkeypatch.setattr(server, "_adapter", adapter)
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        registered = [t.name for t in (await client.list_tools()).tools]
        for name in ("sweep", "inspect_assembly", "create_parametric_enclosure"):
            assert registered.count(name) == 1
        missing = await client.call_tool(
            "sweep", {"sketch": "p", "path_sketch": "q", "path_entities": ["line:0"]}
        )
        assert missing.isError is True
        assert adapter.calls == []
        invalid = await client.call_tool(
            "create_parametric_enclosure",
            {
                "name": "case",
                "width": "",
                "depth": "10 mm",
                "height": "10 mm",
                "wall": "1 mm",
                "floor": "1 mm",
            },
        )
        assert json.loads(invalid.content[0].text)["error"] == "invalid_input"
        assert adapter.calls == []
        inspected = await client.call_tool(
            "inspect_assembly", {"include": ["occurrences"], "limit": 2}
        )
        assert inspected.isError is False
        payload = json.loads(inspected.content[0].text)
        assert payload["ok"] is True
        assert payload["result"]["sections"] == {}
        assert adapter.calls == [True]


@pytest.mark.asyncio
async def test_wire_structured_envelopes_and_error_flag_for_legacy_new_and_raw(monkeypatch):
    class Adapter:
        def execute_script(self, script, *, read_only=False):
            if script == "bad":
                return Envelope(ok=False, error="fusion_error", message="failed")
            if script == "good":
                return Envelope(ok=True, message="done")
            return Envelope(ok=True, message='{"ok":true,"sections":{}}')

    adapter = Adapter()
    monkeypatch.setattr(server, "_adapter", adapter)
    monkeypatch.setattr(
        server.doc_state_tool, "run", lambda _: Envelope(ok=True, result={"active_design": True})
    )
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools["sweep"].inputSchema["required"]) == {
            "sketch",
            "path_sketch",
            "path_entities",
            "name",
        }
        legacy = await client.call_tool("doc_state", {})
        new = await client.call_tool("inspect_assembly", {"include": ["occurrences"]})
        invalid = await client.call_tool(
            "create_parametric_enclosure",
            {
                "name": "case",
                "width": "",
                "depth": "10 mm",
                "height": "10 mm",
                "wall": "1 mm",
                "floor": "1 mm",
            },
        )
        raw_ok = await client.call_tool("execute", {"script": "good"})
        raw_fail = await client.call_tool("execute", {"script": "bad"})
    for reply in (legacy, new, invalid, raw_ok, raw_fail):
        assert reply.structuredContent is not None
        assert json.loads(reply.content[0].text) == reply.structuredContent
        assert reply.isError is (not reply.structuredContent["ok"])
    assert legacy.structuredContent["result"]["active_design"] is True
    assert invalid.structuredContent["error"] == "invalid_input"
    assert raw_ok.structuredContent["message"] == "done"
    assert raw_fail.structuredContent["error"] == "fusion_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("inspect_assembly", {"include": ["bodies"], "limit": 1}),
        ("inspect_feature", {"feature": "fixture"}),
        ("query_geometry", {"body": "fixture", "kind": "face"}),
    ],
)
async def test_inspection_wire_keeps_result_without_repeating_stdout(monkeypatch, name, arguments):
    # Synthetic adapter output tests serialization, not Fusion geometry. The
    # unknown nested field ensures compaction cannot silently whitelist data.
    payload = {
        "ok": True,
        "count": 1,
        "items": [{"handle": "face:fixture:opaque-token", "sample_point_mm": [1, 2, 3]}],
        "extension_data": {"warning": "preserve this", "future_field": [False, None, 0]},
    }
    stdout = json.dumps(payload) + "\n"
    calls = []

    class Adapter:
        def execute_script(self, script, *, read_only=False):
            calls.append(read_only)
            return Envelope(ok=True, message=stdout)

    monkeypatch.setattr(server, "_adapter", Adapter())
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        reply = await client.call_tool(name, arguments)
        raw = await client.call_tool("execute", {"script": "fixture stdout"})
    assert reply.isError is False
    assert reply.structuredContent == {"ok": True, "message": "", "result": payload}
    assert json.loads(reply.content[0].text) == reply.structuredContent
    # The same stdout stays intact on the explicit raw execution escape hatch.
    assert raw.structuredContent["message"] == stdout
    assert calls == [True, False]
