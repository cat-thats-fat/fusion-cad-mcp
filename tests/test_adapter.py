"""Exercise the adapter's HTTP boundary without a running Fusion instance."""

from __future__ import annotations

import json

import httpx
import pytest

from fusion_cad_mcp.adapter import AdapterError, FusionAdapter

_TOOL = "fusion_mcp_execute"
_SCHEMA = {
    "type": "object",
    "properties": {"featureType": {"type": "string", "enum": ["script"]}},
    "required": ["featureType"],
}
_TOOL_DEFINITION = {"name": _TOOL, "inputSchema": _SCHEMA}
_TOOL_RESULT = {"content": [{"type": "text", "text": '{"success":true,"message":"complete"}'}]}


def _reply(payload, result, headers=None):
    return httpx.Response(
        200, headers=headers, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
    )


class _FusionStub:
    def __init__(self, *, session=True, protocol="2025-03-26", override=None):
        self.session = session
        self.protocol = protocol
        self.override = override
        self.calls = []
        self.session_count = 0

    def adapter(self):
        return FusionAdapter(transport=httpx.MockTransport(self))

    def __call__(self, request):
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((request, payload))
        if self.override is not None:
            special = self.override(request, payload)
            if special is not None:
                return special
        method = payload.get("method")
        if method == "initialize":
            self.session_count += 1
            return _reply(
                payload,
                {"protocolVersion": self.protocol, "capabilities": {"tools": {}}},
                {"Mcp-Session-Id": f"fixture-session-{self.session_count}"} if self.session else None,
            )
        if request.method == "DELETE":
            return httpx.Response(405)
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return httpx.Response(202)
        if method == "tools/list":
            return _reply(payload, {"tools": [_TOOL_DEFINITION]})
        if method == "tools/call":
            return _reply(payload, _TOOL_RESULT)
        raise AssertionError(payload)

    @property
    def methods(self):
        return [payload.get("method") or request.method for request, payload in self.calls]


@pytest.mark.parametrize("session", [True, False])
def test_handshake_uses_negotiated_protocol_and_optional_session(session):
    stub = _FusionStub(session=session)
    with stub.adapter() as adapter:
        result = adapter.execute_script("def run(ctx): pass")
        assert result.ok and result.message == "complete"
        adapter.execute_script("def run(ctx): pass")
    assert stub.methods == [
        "initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call",
    ] + (["DELETE"] if session else [])
    first_request, init = stub.calls[0]
    assert "Mcp-Session-Id" not in first_request.headers
    assert "Mcp-Protocol-Version" not in first_request.headers
    assert init["params"]["protocolVersion"] == "2025-06-18"
    assert set(first_request.headers["Accept"].split(", ")) == {"application/json", "text/event-stream"}
    for request, _ in stub.calls[1:]:
        assert request.headers["Mcp-Protocol-Version"] == "2025-03-26"
        assert request.headers.get("Mcp-Session-Id") == ("fixture-session-1" if session else None)


def test_unsupported_protocol_fails_before_discovery_and_cleans_up():
    stub = _FusionStub(protocol="2099-03-26")
    with stub.adapter() as adapter, pytest.raises(AdapterError, match="unsupported MCP protocol"):
        adapter.list_tools()
    assert stub.methods == ["initialize", "DELETE"]


def test_list_tools_pages_and_enforces_schema_before_calling_fusion():
    def paginate(request, payload):
        if payload.get("method") == "tools/list":
            if not payload.get("params"):
                return _reply(payload, {"tools": [_TOOL_DEFINITION], "nextCursor": "two"})
            assert payload["params"] == {"cursor": "two"}
            return _reply(payload, {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]})
        return None

    stub = _FusionStub(override=paginate)
    with stub.adapter() as adapter:
        assert [tool["name"] for tool in adapter.list_tools()] == [_TOOL, "read"]
        with pytest.raises(AdapterError, match="at featureType"):
            adapter.call_tool(_TOOL, {"featureType": "bad"})
        with pytest.raises(AdapterError, match="does not advertise"):
            adapter.call_tool("missing", {})
    assert stub.methods == ["initialize", "notifications/initialized", "tools/list", "tools/list", "DELETE"]


def test_invalid_tool_schema_stops_before_dispatch():
    def bad_schema(request, payload):
        if payload.get("method") == "tools/list":
            return _reply(payload, {"tools": [{"name": _TOOL, "inputSchema": {"type": "object", "required": True}}]})
        return None

    stub = _FusionStub(override=bad_schema)
    with stub.adapter() as adapter, pytest.raises(AdapterError, match="invalid input schema"):
        adapter.execute_script("def run(ctx): pass")
    assert "tools/call" not in stub.methods


def test_expired_session_retries_only_after_rediscovery():
    def expire(request, payload):
        if payload.get("method") == "tools/call" and request.headers["Mcp-Session-Id"] == "fixture-session-1":
            return httpx.Response(404)
        return None

    stub = _FusionStub(override=expire)
    with stub.adapter() as adapter:
        assert adapter.execute_script("def run(ctx): pass").ok
    cycle = ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert stub.methods == cycle + cycle + ["DELETE"]
    assert "Mcp-Session-Id" not in stub.calls[4][0].headers
    assert stub.calls[3][1]["id"] != stub.calls[7][1]["id"]


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429, 500])
def test_other_http_errors_do_not_replay_tools(status):
    def reject(request, payload):
        if payload.get("method") == "tools/call":
            return httpx.Response(status)
        return None

    stub = _FusionStub(override=reject)
    with stub.adapter() as adapter, pytest.raises(AdapterError, match=f"HTTP {status}") as error:
        adapter.execute_script("def run(ctx): pass")
    assert ("outcome is unknown" in str(error.value)) == (status >= 500)
    assert stub.methods.count("tools/call") == 1
    assert stub.methods.count("initialize") == 1


def test_404_without_session_does_not_replay_tools():
    def missing(request, payload):
        if payload.get("method") == "tools/call":
            return httpx.Response(404)
        return None

    stub = _FusionStub(session=False, override=missing)
    with stub.adapter() as adapter, pytest.raises(AdapterError, match="HTTP 404"):
        adapter.execute_script("def run(ctx): pass")
    assert stub.methods.count("tools/call") == 1


def test_read_timeout_reports_uncertain_outcome_and_never_replays():
    def timeout(request, payload):
        if payload.get("method") == "tools/call":
            raise httpx.ReadTimeout("fixture", request=request)
        return None

    stub = _FusionStub(override=timeout)
    with stub.adapter() as adapter, pytest.raises(AdapterError, match="timed out.*outcome is unknown"):
        adapter.execute_script("def run(ctx): pass")
    assert stub.methods.count("tools/call") == 1
    call, cancel = stub.calls[3][1], stub.calls[4][1]
    assert cancel["method"] == "notifications/cancelled"
    assert cancel["params"]["requestId"] == call["id"]


def test_sse_handles_notifications_and_stops_at_the_response():
    class LiveStream(httpx.SyncByteStream):
        def __init__(self, request_id):
            self.request_id = request_id
            self.closed = False

        def __iter__(self):
            yield b': heartbeat\r\ndata: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\r\n\r\n'
            yield (
                f'event: message\r\ndata: {{"jsonrpc":"2.0",\r\ndata: "id":{self.request_id},'
                f'"result":{json.dumps(_TOOL_RESULT)}}}\r\n\r\n'
            ).encode()
            raise AssertionError("client must stop consuming the stream at its reply")

        def close(self):
            self.closed = True

    streams = []

    def use_sse(request, payload):
        if payload.get("method") == "tools/call":
            stream = LiveStream(payload["id"])
            streams.append(stream)
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)
        return None

    stub = _FusionStub(override=use_sse)
    with stub.adapter() as adapter:
        assert adapter.execute_script("def run(ctx): pass").ok
        assert adapter.execute_script("def run(ctx): pass").ok
    assert stub.methods.count("tools/list") == 2
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("resume_status", [200, 404])
def test_sse_resumes_by_get_and_does_not_replay_post(resume_status):
    call_id = None

    def resume(request, payload):
        nonlocal call_id
        if payload.get("method") == "tools/call":
            call_id = payload["id"]
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, text="id: fixture-event\ndata:\n\n"
            )
        if request.method == "GET":
            assert request.headers["Last-Event-ID"] == "fixture-event"
            assert request.headers["Mcp-Protocol-Version"] == "2025-03-26"
            if resume_status == 404:
                return httpx.Response(404)
            result = json.dumps({"jsonrpc": "2.0", "id": call_id, "result": _TOOL_RESULT})
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, text=f"data: {result}\n\n"
            )
        return None

    stub = _FusionStub(override=resume)
    with stub.adapter() as adapter:
        if resume_status == 200:
            assert adapter.execute_script("def run(ctx): pass").ok
        else:
            with pytest.raises(AdapterError, match="outcome is unknown"):
                adapter.execute_script("def run(ctx): pass")
    assert stub.methods.count("tools/call") == 1
    assert stub.methods.count("GET") == 1
