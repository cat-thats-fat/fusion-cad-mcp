"""Synchronous Streamable HTTP MCP client for Autodesk Fusion."""

from __future__ import annotations

import asyncio
import itertools
import math
from contextlib import contextmanager, suppress
from threading import Event, RLock, Thread
from typing import Any

import httpx
from httpx_sse import EventSource
from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator, validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from . import __version__
from .envelope import Envelope, from_fusion_response

DEFAULT_URL = "http://127.0.0.1:27182/mcp"
DEFAULT_TIMEOUT = 60.0
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-03-26", PROTOCOL_VERSION, "2025-11-25"}
CLIENT_NAME = "fusion-cad-mcp"


class AdapterError(RuntimeError):
    """Fusion could not be reached, rejected the request, or sent an invalid response."""

    def __init__(
        self, message: str, *, outcome: str = "not_started", code: str = "transport_error"
    ):
        super().__init__(message)
        self.outcome = outcome
        self.code = code


class _SessionExpired(AdapterError):
    """Fusion rejected a request because its session expired."""


class FusionAdapter:
    """Synchronous HTTP MCP client to Fusion."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number of seconds")
        self.url = url
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._loop = asyncio.new_event_loop()
        ready = Event()

        def serve():
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()
            self._loop.close()

        self._worker = Thread(target=serve, name="fusion-mcp-http", daemon=True)
        self._worker.start()
        if not ready.wait(2.0):
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._worker.join(2.0)
            raise AdapterError("Fusion transport worker did not start.", code="adapter_unavailable")
        self._closed = False
        self._broken = False
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._id_counter = itertools.count(1)
        self._tool_validators: dict[str, Any] | None = None
        self._lock = RLock()

    @contextmanager
    def _serialized(self):
        # Queueing behind another call must not block a client indefinitely.
        if not self._lock.acquire(timeout=self.timeout):
            raise AdapterError(
                "Another Fusion call is still running; this call was not sent.", code="adapter_busy"
            )
        try:
            yield
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._serialized():
            if self._closed:
                return
            try:
                if not self._broken:
                    try:
                        self._end_session()
                    finally:
                        if not self._broken:
                            self._run_async(self._client.aclose(), wait=2.0)
            finally:
                self._closed = True
                if not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._loop.stop)
                self._worker.join(2.0)

    def _ensure_open(self) -> None:
        if self._closed:
            raise AdapterError("Fusion adapter is closed.", code="adapter_closed")
        if self._broken or not self._worker.is_alive():
            self._broken = True
            raise AdapterError(
                "Fusion transport worker is unavailable; this call was not sent.",
                code="adapter_unavailable",
            )

    def __enter__(self) -> FusionAdapter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream"}
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version is not None:
            headers["Mcp-Protocol-Version"] = self._protocol_version
        return headers

    def _reset_session(self) -> None:
        self._session_id = None
        self._protocol_version = None
        self._tool_validators = None

    def _run_async(self, coroutine, *, wait: float | None = None):
        if self._broken or self._loop.is_closed() or not self._worker.is_alive():
            coroutine.close()
            self._broken = True
            raise AdapterError(
                "Fusion transport worker is unavailable.", code="adapter_unavailable"
            )
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError as exc:
            coroutine.close()
            self._broken = True
            raise AdapterError(
                "Fusion transport worker is unavailable.", code="adapter_unavailable"
            ) from exc
        try:
            return future.result(timeout=self.timeout + 3.0 if wait is None else wait)
        except TimeoutError as exc:
            if future.done():
                # The coroutine itself can raise TimeoutError (e.g. notification timeout).
                # Preserve that exception rather than quarantining a responsive worker.
                return future.result()
            future.cancel()
            self._broken = True
            raise AdapterError(
                "Fusion transport worker did not stop in time; outcome is unknown.",
                outcome="unknown",
                code="transport_timeout",
            ) from exc

    def _end_session(self) -> None:
        async def end():
            try:
                if self._session_id is not None:
                    async with asyncio.timeout(min(self.timeout, 2.0)):
                        async with self._client.stream("DELETE", self.url, headers=self._headers()):
                            pass
            except (httpx.HTTPError, TimeoutError):
                pass
            finally:
                self._reset_session()

        self._run_async(end())

    @staticmethod
    def _validate_reply(reply: Any, request_id: int) -> dict[str, Any]:
        if (
            not isinstance(reply, dict)
            or reply.get("jsonrpc") != "2.0"
            or type(reply.get("id")) is not int
            or reply["id"] != request_id
            or ("result" in reply) == ("error" in reply)
            or not isinstance(reply.get("result", reply.get("error")), dict)
        ):
            raise ValueError("invalid or mismatched JSON-RPC reply")
        return reply

    async def _send_one_way_async(self, payload: dict[str, Any], timeout: float) -> None:
        async with asyncio.timeout(timeout):
            async with self._client.stream(
                "POST", self.url, json=payload, headers=self._headers()
            ) as response:
                if response.status_code != 202:
                    raise AdapterError(
                        f"Fusion refused {payload.get('method', 'a JSON-RPC reply')} "
                        f"with HTTP {response.status_code}."
                    )

    def _send_one_way(self, payload: dict[str, Any], *, timeout: float | None = None) -> None:
        try:
            self._run_async(
                self._send_one_way_async(payload, self.timeout if timeout is None else timeout)
            )
        except TimeoutError as exc:
            raise httpx.ReadTimeout("notification deadline exceeded") from exc

    async def _consume_sse(
        self, response: httpx.Response, request_id: int
    ) -> tuple[dict[str, Any] | None, str, int]:
        last_event_id = ""
        retry_ms = 0
        async for event in EventSource(response).aiter_sse():
            last_event_id = event.id
            if event.retry is not None and event.retry >= 0:
                retry_ms = event.retry
            if event.event != "message" or not event.data:
                continue
            reply = event.json()
            if isinstance(reply, dict) and reply.get("jsonrpc") == "2.0":
                method = reply.get("method")
                if isinstance(method, str):
                    if method == "notifications/tools/list_changed":
                        self._tool_validators = None
                    if "id" in reply:
                        acknowledgement = {"jsonrpc": "2.0", "id": reply["id"]}
                        if method == "ping":
                            acknowledgement["result"] = {}
                        else:
                            acknowledgement["error"] = {
                                "code": -32601,
                                "message": "Method not found",
                            }
                        try:
                            await self._send_one_way_async(acknowledgement, self.timeout)
                        except AdapterError as exc:
                            raise ValueError("Fusion refused a reply on the SSE stream") from exc
                    continue
            return self._validate_reply(reply, request_id), last_event_id, retry_ms
        return None, last_event_id, retry_ms

    async def _request_async(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request_id = next(self._id_counter)
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        uncertain = (
            " The tool outcome is unknown. Check the Fusion document before retrying."
            if method == "tools/call"
            else ""
        )
        response_started = False
        try:
            async with asyncio.timeout(self.timeout) as budget:
                async with self._client.stream(
                    "POST", self.url, json=payload, headers=self._headers()
                ) as response:
                    response_started = True
                    status = response.status_code
                    if self._session_id is not None and status == 404:
                        raise _SessionExpired("Fusion MCP session expired.")
                    if status != 200:
                        unknown = method == "tools/call" and (status >= 500 or status in {408, 409})
                        raise AdapterError(
                            f"Fusion {method} returned HTTP {status}."
                            + (uncertain if unknown else ""),
                            outcome="unknown" if unknown else "not_started",
                            code="http_error",
                        )
                    if method == "initialize":
                        self._session_id = response.headers.get("Mcp-Session-Id")
                    content_type = (
                        response.headers.get("content-type", "").partition(";")[0].strip().lower()
                    )
                    if content_type == "application/json":
                        await response.aread()
                        return self._validate_reply(response.json(), request_id)
                    if content_type != "text/event-stream":
                        raise ValueError("expected a JSON or SSE response")
                    reply, last_event_id, retry_ms = await self._consume_sse(response, request_id)
                if reply is None and last_event_id:
                    if retry_ms:
                        seconds, milliseconds = divmod(retry_ms, 1000)
                        remaining = max(0.0, budget.when() - asyncio.get_running_loop().time())
                        if seconds >= remaining:
                            raise httpx.ReadTimeout("SSE retry would exceed request deadline")
                        await asyncio.sleep(float(seconds) + milliseconds / 1000)
                    headers = {
                        **self._headers(),
                        "Accept": "text/event-stream",
                        "Last-Event-ID": last_event_id,
                    }
                    async with self._client.stream("GET", self.url, headers=headers) as response:
                        if response.status_code == 404 and self._session_id is not None:
                            self._reset_session()
                        if response.status_code != 200:
                            raise ValueError(f"SSE resume returned HTTP {response.status_code}")
                        reply, _, _ = await self._consume_sse(response, request_id)
                if reply is None:
                    raise ValueError("SSE stream ended before the JSON-RPC reply")
                return reply
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            if response_started:
                raise AdapterError(
                    f"Fusion {method} connection failed.{uncertain}",
                    outcome="unknown" if uncertain else "not_started",
                ) from exc
            raise AdapterError(
                f"Cannot reach Fusion MCP at {self.url}. Is Fusion running with Preferences > General > API > Fusion MCP Server enabled?"
            ) from exc
        except (httpx.TimeoutException, TimeoutError) as exc:
            if method != "initialize":
                await self._cancel_request_async(request_id)
            raise AdapterError(
                f"Fusion {method} timed out.{uncertain}",
                outcome="unknown" if uncertain else "not_started",
                code="transport_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise AdapterError(
                f"Fusion {method} connection failed.{uncertain}",
                outcome="unknown" if uncertain else "not_started",
            ) from exc
        except ValueError as exc:
            raise AdapterError(
                f"Fusion {method} returned an invalid response ({exc}).{uncertain}",
                outcome="unknown" if uncertain else "not_started",
                code="invalid_transport_response",
            ) from exc

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._run_async(self._request_async(method, params))

    async def _cancel_request_async(self, request_id: int) -> None:
        with suppress(AdapterError, httpx.HTTPError, TimeoutError):
            await self._send_one_way_async(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": request_id, "reason": "Request timed out"},
                },
                min(self.timeout, 1.0),
            )

    @staticmethod
    def _result(reply: dict[str, Any], method: str) -> dict[str, Any]:
        if "error" in reply:
            error = reply["error"]
            raise AdapterError(
                f"Fusion {method} returned a JSON-RPC error (code {error.get('code')})."
            )
        return reply["result"]

    def _ensure_initialized(self) -> None:
        if self._protocol_version is not None:
            return
        try:
            reply = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                },
            )
            result = self._result(reply, "initialize")
            version = result.get("protocolVersion")
            if not isinstance(version, str) or version not in SUPPORTED_PROTOCOL_VERSIONS:
                raise AdapterError("Fusion returned an unsupported MCP protocol version.")
            capabilities = result.get("capabilities")
            if not isinstance(capabilities, dict) or not isinstance(
                capabilities.get("tools"), dict
            ):
                raise AdapterError("Fusion did not advertise MCP tool support.")
            self._protocol_version = version
            self._send_one_way({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except httpx.HTTPError as exc:
            self._end_session()
            raise AdapterError("Fusion did not accept the MCP initialized notification.") from exc
        except AdapterError:
            self._end_session()
            raise

    def _discover_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        validators: dict[str, Any] = {}
        seen_cursors: set[str] = set()
        params = None
        while True:
            result = self._result(self._request("tools/list", params), "tools/list")
            page = result.get("tools")
            if not isinstance(page, list):
                raise AdapterError("Fusion tools/list returned an invalid tool list.")
            for tool in page:
                if not isinstance(tool, dict):
                    raise AdapterError("Fusion tools/list returned an invalid tool description.")
                name, schema = tool.get("name"), tool.get("inputSchema")
                if not isinstance(name, str) or not name or name in validators:
                    raise AdapterError(
                        "Fusion tools/list returned an invalid or duplicate tool name."
                    )
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    raise AdapterError(
                        f"Fusion tools/list returned an invalid input schema for {name!r}."
                    )
                validator = (
                    validator_for(schema, default=None)
                    if "$schema" in schema
                    else Draft202012Validator
                )
                if validator is None:
                    raise AdapterError(
                        f"Fusion tools/list returned an unsupported input schema for {name!r}."
                    )
                try:
                    validator.check_schema(schema)
                except SchemaError as exc:
                    raise AdapterError(
                        f"Fusion tools/list returned an invalid input schema for {name!r}."
                    ) from exc
                validators[name] = validator(schema, registry=Registry())
                tools.append(tool)
            cursor = result.get("nextCursor")
            if cursor is None:
                self._tool_validators = validators
                return tools
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise AdapterError("Fusion tools/list returned an invalid pagination cursor.")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}

    def list_tools(self) -> list[dict[str, Any]]:
        """List tools from the current Fusion session, including their input schemas."""
        with self._serialized():
            self._ensure_open()
            for attempt in range(2):
                self._ensure_initialized()
                try:
                    return self._discover_tools()
                except _SessionExpired:
                    self._reset_session()
                    if attempt:
                        raise
        raise AssertionError("unreachable")

    def _validate_arguments(self, name: str, arguments: dict[str, Any]) -> None:
        if self._tool_validators is None:
            self._discover_tools()
        assert self._tool_validators is not None
        validator = self._tool_validators.get(name)
        if validator is None:
            raise AdapterError(f"Fusion does not advertise the tool {name!r}.")
        # Keep document names, paths, and script contents out of validation errors.
        try:
            error = next(validator.iter_errors(arguments), None)
        except Unresolvable as exc:
            raise AdapterError(
                f"Fusion tool {name!r} has an unresolved input schema reference."
            ) from exc
        if error is not None:
            field = ".".join(map(str, error.absolute_path)) or "arguments"
            raise AdapterError(
                f"Invalid arguments for Fusion tool {name!r} at {field} ({error.validator})."
            )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a Fusion MCP tool by name and return the raw JSON-RPC reply."""
        with self._serialized():
            self._ensure_open()
            for attempt in range(2):
                self._ensure_initialized()
                try:
                    self._validate_arguments(name, arguments)
                    return self._request("tools/call", {"name": name, "arguments": arguments})
                except _SessionExpired:
                    self._reset_session()
                    if attempt:
                        raise
        raise AssertionError("unreachable")

    def _call_envelope(
        self, name: str, arguments: dict[str, Any], *, read_only: bool = False
    ) -> Envelope:
        try:
            raw = self.call_tool(name, arguments)
        except AdapterError as exc:
            return Envelope(
                ok=False,
                error=exc.code,
                message=str(exc),
                state={
                    "failure_kind": "protocol" if exc.code == "invalid_transport_response" else "transport",
                    "execution_outcome": exc.outcome,
                    "model_may_have_changed": not read_only and exc.outcome == "unknown",
                    "retry_safe": exc.outcome == "not_started" or read_only,
                },
            )
        env = from_fusion_response(raw)
        if not env.ok:
            # A Fusion script error does not establish rollback, nor does a
            # JSON-RPC error prove that the dispatched operation never started.
            env.state = {
                "failure_kind": "protocol" if "error" in raw or env.error in {
                    "empty_content", "missing_result", "unexpected_content_type"
                } else "modeling",
                "execution_outcome": "unknown",
                "model_may_have_changed": not read_only,
                "retry_safe": read_only,
            }
        return env

    def execute_script(self, script: str, *, read_only: bool = False) -> Envelope:
        """Run a Python script in Fusion. Returns the V2-spec envelope."""
        obj: dict[str, Any] = {"script": script}
        if read_only:
            obj["readOnly"] = True
        return self._call_envelope(
            "fusion_mcp_execute",
            {"featureType": "script", "object": obj},
            read_only=read_only,
        )

    def execute_typed_script(self, script: str, *, read_only: bool = False,
                             operation_id: str | None = None, recovery_probe: bool = False) -> Envelope:
        """Execute a generated JSON-status script with a durable local receipt."""
        from .receipts import run_typed

        return run_typed(self.execute_script, script, self.url, self.timeout,
                         operation_id=operation_id, read_only=read_only, recovery_probe=recovery_probe)

    def execute_document_op(self, operation: str, **object_args: Any) -> Envelope:
        """Run a document-level op: save, close, open. See Fusion MCP schema."""
        obj = {"operation": operation, **object_args}
        return self._call_envelope(
            "fusion_mcp_execute",
            {"featureType": "document", "object": obj},
        )

    def read_query(self, query_type: str, **kwargs: Any) -> Envelope:
        """Run a fusion_mcp_read query: document, projects, screenshot, apiDocumentation."""
        payload: dict[str, Any] = {"queryType": query_type, **kwargs}
        return self._call_envelope("fusion_mcp_read", payload, read_only=True)

    def update(self, kind: str) -> Envelope:
        """Run undo or redo."""
        if kind not in {"undo", "redo"}:
            raise ValueError(f"update kind must be undo or redo, got {kind!r}")
        return self._call_envelope("fusion_mcp_update", {"featureType": kind})
