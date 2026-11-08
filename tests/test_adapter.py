"""Exercise the adapter's HTTP boundary without a running Fusion instance."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
                {"Mcp-Session-Id": f"fixture-session-{self.session_count}"}
                if self.session
                else None,
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
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
        "tools/call",
    ] + (["DELETE"] if session else [])
    first_request, init = stub.calls[0]
    assert "Mcp-Session-Id" not in first_request.headers
    assert "Mcp-Protocol-Version" not in first_request.headers
    assert init["params"]["protocolVersion"] == "2025-06-18"
    assert set(first_request.headers["Accept"].split(", ")) == {
        "application/json",
        "text/event-stream",
    }
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
    assert stub.methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/list",
        "DELETE",
    ]


def test_invalid_tool_schema_stops_before_dispatch():
    def bad_schema(request, payload):
        if payload.get("method") == "tools/list":
            return _reply(
                payload,
                {"tools": [{"name": _TOOL, "inputSchema": {"type": "object", "required": True}}]},
            )
        return None

    stub = _FusionStub(override=bad_schema)
    with stub.adapter() as adapter, pytest.raises(AdapterError, match="invalid input schema"):
        adapter.call_tool(_TOOL, {"featureType": "script"})
    assert "tools/call" not in stub.methods


def test_expired_session_retries_only_after_rediscovery():
    def expire(request, payload):
        if (
            payload.get("method") == "tools/call"
            and request.headers["Mcp-Session-Id"] == "fixture-session-1"
        ):
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
        adapter.call_tool(_TOOL, {"featureType": "script"})
    assert ("outcome is unknown" in str(error.value)) == (status >= 500 or status == 409)
    assert stub.methods.count("tools/call") == 1
    assert stub.methods.count("initialize") == 1


def test_404_without_session_does_not_replay_tools():
    def missing(request, payload):
        if payload.get("method") == "tools/call":
            return httpx.Response(404)
        return None

    stub = _FusionStub(session=False, override=missing)
    with stub.adapter() as adapter, pytest.raises(AdapterError, match="HTTP 404"):
        adapter.call_tool(_TOOL, {"featureType": "script"})
    assert stub.methods.count("tools/call") == 1


def test_read_timeout_reports_uncertain_outcome_and_never_replays():
    def timeout(request, payload):
        if payload.get("method") == "tools/call":
            raise httpx.ReadTimeout("fixture", request=request)
        return None

    stub = _FusionStub(override=timeout)
    with (
        stub.adapter() as adapter,
        pytest.raises(AdapterError, match="timed out.*outcome is unknown"),
    ):
        adapter.call_tool(_TOOL, {"featureType": "script"})
    assert stub.methods.count("tools/call") == 1
    call, cancel = stub.calls[3][1], stub.calls[4][1]
    assert cancel["method"] == "notifications/cancelled"
    assert cancel["params"]["requestId"] == call["id"]


def test_sse_handles_notifications_and_stops_at_the_response():
    class LiveStream(httpx.AsyncByteStream):
        def __init__(self, request_id):
            self.request_id = request_id
            self.closed = False

        async def __aiter__(self):
            yield b': heartbeat\r\ndata: {"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\r\n\r\n'
            yield (
                f'event: message\r\ndata: {{"jsonrpc":"2.0",\r\ndata: "id":{self.request_id},'
                f'"result":{json.dumps(_TOOL_RESULT)}}}\r\n\r\n'
            ).encode()
            raise AssertionError("client must stop consuming the stream at its reply")

        async def aclose(self):
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
                200,
                headers={"Content-Type": "text/event-stream"},
                text="id: fixture-event\ndata:\n\n",
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
                adapter.call_tool(_TOOL, {"featureType": "script"})
    assert stub.methods.count("tools/call") == 1
    assert stub.methods.count("GET") == 1


@pytest.mark.parametrize(
    "stall",
    [
        "heartbeats",
        "partial_drip",
        "partial_then_silent",
        "json_drip",
        "resume",
        "header_drip",
        "chunk_drip",
    ],
)
def test_sse_deadline_releases_lock_and_connection_on_stalled_stream(stall):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def respond(self, status, body=b"", headers=None):
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self):
            calls.append({"method": "DELETE"})
            self.respond(204)

        def do_GET(self):
            calls.append({"method": "GET", "last_event_id": self.headers.get("Last-Event-ID")})
            self.stream_stall("heartbeats")

        def stream_stall(self, kind):
            if kind == "header_drip":
                try:
                    self.connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: "
                    )
                    for _ in range(40):
                        self.connection.sendall(b"x")
                        time.sleep(0.04)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                self.close_connection = True
                return
            if kind == "chunk_drip":
                try:
                    self.connection.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
                    )
                    for _ in range(40):
                        self.connection.sendall(b"1")
                        time.sleep(0.04)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header(
                "Content-Type", "application/json" if kind == "json_drip" else "text/event-stream"
            )
            self.end_headers()
            try:
                if kind == "partial_then_silent":
                    self.wfile.write(b"data: incomplete")
                    self.wfile.flush()
                    time.sleep(1.5)
                elif kind == "resume":
                    self.wfile.write(b"id: resume-point\ndata:\n\n")
                    self.wfile.flush()
                    time.sleep(0.28)
                else:
                    chunk = {
                        "heartbeats": b": heartbeat\n\n",
                        "partial_drip": b"x",
                        "json_drip": b" ",
                    }[kind]
                    # A finite producer guarantees that the pre-fix test does not hang.
                    end = time.monotonic() + 1.5
                    while time.monotonic() < end:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            self.close_connection = True

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            method = payload["method"]
            if method == "tools/call" and sum(c["method"] == method for c in calls) == 1:
                self.stream_stall(stall)
                return
            if method in {"notifications/initialized", "notifications/cancelled"}:
                self.respond(202)
                return
            result = {
                "initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                "tools/list": {"tools": [_TOOL_DEFINITION]},
                "tools/call": _TOOL_RESULT,
            }[method]
            body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()
            headers = {"Content-Type": "application/json"}
            if method == "initialize":
                headers["Mcp-Session-Id"] = "fixture-session"
            self.respond(200, body, headers)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = FusionAdapter(url=f"http://127.0.0.1:{server.server_port}/mcp", timeout=0.45)
    worker = None
    try:
        adapter.list_tools()
        started = time.monotonic()
        with pytest.raises(AdapterError, match="timed out.*outcome is unknown"):
            adapter.call_tool(_TOOL, {"featureType": "script"})
        assert time.monotonic() - started < (0.65 if stall == "resume" else 1.1)
        assert sum(c["method"] == "tools/call" for c in calls) == 1
        if stall == "resume":
            gets = [c for c in calls if c["method"] == "GET"]
            assert gets == [{"method": "GET", "last_event_id": "resume-point"}]
        # A separate thread must acquire the lock and use the same adapter/session.
        result = []
        worker = threading.Thread(
            target=lambda: result.append(adapter.execute_script("def run(ctx): pass")), daemon=True
        )
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert result and result[0].ok
        assert sum(c["method"] == "tools/call" for c in calls) == 2
        first_call = next(c for c in calls if c["method"] == "tools/call")
        cancellation = next(c for c in calls if c["method"] == "notifications/cancelled")
        assert cancellation["params"]["requestId"] == first_call["id"]
    finally:
        if worker is None or not worker.is_alive():
            adapter.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert calls[-1]["method"] == "DELETE"
    assert adapter._client.is_closed


def test_high_level_timeout_state_and_read_only_request():
    def timeout(request, payload):
        if payload.get("method") == "tools/call":
            raise httpx.ReadTimeout("fixture", request=request)
        return None

    stub = _FusionStub(override=timeout)
    with stub.adapter() as adapter:
        mutating = adapter.execute_script("pass")
        readonly = adapter.execute_script("pass", read_only=True)
    assert mutating.error == "transport_timeout"
    assert mutating.state == {
        "failure_kind": "transport",
        "execution_outcome": "unknown",
        "model_may_have_changed": True,
        "retry_safe": False,
    }
    assert readonly.state["execution_outcome"] == "unknown"
    assert readonly.state["model_may_have_changed"] is False
    assert readonly.state["retry_safe"] is True
    calls = [p for _, p in stub.calls if p.get("method") == "tools/call"]
    assert calls[1]["params"]["arguments"]["object"]["readOnly"] is True
    assert "readOnly" not in calls[0]["params"]["arguments"]["object"]


def test_lock_wait_is_bounded_and_reports_not_started():
    adapter = FusionAdapter(timeout=0.15, transport=httpx.MockTransport(_FusionStub()))
    locked = threading.Event()
    release = threading.Event()

    def holder():
        with adapter._lock:
            locked.set()
            release.wait(2)

    worker = threading.Thread(target=holder)
    worker.start()
    assert locked.wait(1)
    try:
        started = time.monotonic()
        result = adapter.execute_script("pass")
        assert time.monotonic() - started < 0.7
        assert result.error == "adapter_busy"
        assert result.state["execution_outcome"] == "not_started"
        assert result.state["retry_safe"] is True
    finally:
        release.set()
        worker.join(2)
        adapter.close()
    assert not adapter._worker.is_alive()


def test_cancellation_and_delete_with_stalled_headers_are_bounded():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def slow_headers(self):
            try:
                self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                for _ in range(35):
                    self.connection.sendall(b"x")
                    time.sleep(0.04)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            self.close_connection = True

        def do_DELETE(self):
            calls.append("DELETE")
            self.slow_headers()

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = payload["method"]
            calls.append(method)
            if method in {"tools/call", "notifications/cancelled"}:
                self.slow_headers()
                return
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = {
                "initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                "tools/list": {"tools": [_TOOL_DEFINITION]},
            }[method]
            body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if method == "initialize":
                self.send_header("Mcp-Session-Id", "session")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = FusionAdapter(url=f"http://127.0.0.1:{server.server_port}/mcp", timeout=0.2)
    try:
        adapter.list_tools()
        start = time.monotonic()
        result = adapter.execute_script("pass")
        assert result.error == "transport_timeout"
        assert time.monotonic() - start < 1.1
        assert calls.count("tools/call") == calls.count("notifications/cancelled") == 1
        start = time.monotonic()
        adapter.close()
        assert time.monotonic() - start < 0.8
        assert not adapter._worker.is_alive()
        assert calls[-1] == "DELETE"
    finally:
        if adapter._worker.is_alive():
            adapter.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_calls_after_close_fail_promptly_and_close_is_idempotent():
    adapter = _FusionStub().adapter()
    adapter.close()
    adapter.close()
    started = time.monotonic()
    result = adapter.execute_script("pass")
    assert result.error == "adapter_closed"
    assert result.state["execution_outcome"] == "not_started"
    with pytest.raises(AdapterError, match="closed"):
        adapter.list_tools()
    assert time.monotonic() - started < 0.5
    assert not adapter._worker.is_alive()


def test_unexpected_worker_timeout_quarantines_adapter():
    import asyncio

    adapter = _FusionStub().adapter()
    try:
        with pytest.raises(AdapterError) as error:
            adapter._run_async(asyncio.sleep(2), wait=0.02)
        assert error.value.code == "transport_timeout"
        assert error.value.outcome == "unknown"
        result = adapter.execute_script("pass")
        assert result.error == "adapter_unavailable"
        assert result.state["execution_outcome"] == "not_started"
    finally:
        adapter.close()
    assert not adapter._worker.is_alive()


def test_coroutine_timeout_does_not_quarantine_responsive_worker():
    adapter = _FusionStub().adapter()

    async def fail():
        raise TimeoutError("inside coroutine")

    try:
        with pytest.raises(TimeoutError, match="inside coroutine"):
            adapter._run_async(fail())
        assert adapter.execute_script("pass").ok
    finally:
        adapter.close()


def test_huge_sse_retry_fails_as_timeout_without_resume_or_replay():
    def huge_retry(request, payload):
        if payload.get("method") == "tools/call":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text="id: checkpoint\nretry: " + "9" * 400 + "\ndata:\n\n",
            )
        return None

    stub = _FusionStub(override=huge_retry)
    with stub.adapter() as adapter:
        out = adapter.execute_script("pass")
        assert out.error == "transport_timeout"
        assert out.state["execution_outcome"] == "unknown"
        assert out.state["retry_safe"] is False
    assert stub.methods.count("tools/call") == 1
    assert stub.methods.count("GET") == 0
    assert stub.methods.count("notifications/cancelled") == 1


def test_malformed_sse_retry_is_ignored_and_known_stream_resumes():
    call_id = None

    def malformed(request, payload):
        nonlocal call_id
        if payload.get("method") == "tools/call":
            call_id = payload["id"]
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                text="id: checkpoint\nretry: not-a-number\ndata:\n\n",
            )
        if request.method == "GET":
            data = json.dumps({"jsonrpc": "2.0", "id": call_id, "result": _TOOL_RESULT})
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, text=f"data: {data}\n\n"
            )
        return None

    stub = _FusionStub(override=malformed)
    with stub.adapter() as adapter:
        assert adapter.execute_script("pass").ok
    assert stub.methods.count("tools/call") == 1
    assert stub.methods.count("GET") == 1


@pytest.mark.parametrize("result,code", [
    ({"content": []}, "empty_content"),
    (None, "invalid_transport_response"),
    ({"content": [{"type": "unsupported"}]}, "unexpected_content_type"),
])
@pytest.mark.parametrize("read_only", [False, True])
def test_missing_tool_result_is_protocol_uncertainty_without_replay(result, code, read_only):
    def malformed(request, payload):
        if payload.get("method") == "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})
        return None

    stub = _FusionStub(override=malformed)
    with stub.adapter() as adapter:
        out = adapter.execute_script("pass", read_only=read_only)
    assert not out.ok and out.error == code
    assert out.state == {
        "failure_kind": "protocol", "execution_outcome": "unknown",
        "model_may_have_changed": not read_only, "retry_safe": read_only,
    }
    assert stub.methods.count("tools/call") == 1
