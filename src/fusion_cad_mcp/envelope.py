"""V2-spec result envelope for tool returns.

Every tool returns this shape so clients have one parsing contract.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Envelope:
    """The single result envelope all tools return."""

    ok: bool
    message: str = ""
    result: dict[str, Any] | None = None
    state: dict[str, Any] | None = None
    image: dict[str, Any] | None = None  # {data: <base64>, mime_type: "image/png"}
    error: str | None = None
    traceback: str | None = None
    images: list[dict[str, Any]] | None = None  # all image blocks, if more than one

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k == "ok"}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def from_fusion_response(raw: dict[str, Any]) -> Envelope:
    """Read native MCP content and Fusion's legacy JSON-encoded text payload."""
    if "error" in raw:
        rpc_err = raw["error"]
        if not isinstance(rpc_err, dict):
            return Envelope(ok=False, error="jsonrpc_error")
        return Envelope(
            ok=False,
            error=rpc_err.get("message") or "jsonrpc_error",
            result={"code": rpc_err.get("code")},
        )

    result = raw.get("result")
    if not isinstance(result, dict):
        return Envelope(ok=False, error="missing_result", message=json.dumps(raw)[:500])

    content = result.get("content")
    texts: list[str] = []
    parsed_texts: list[tuple[str, Any]] = []
    images: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image":
            data, mime_type = block.get("data"), block.get("mimeType")
            if isinstance(data, str) and data and isinstance(mime_type, str) and mime_type:
                images.append({"data": data, "mime_type": mime_type})
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            block_text = block["text"]
            try:
                parsed_texts.append((block_text, json.loads(block_text)))
            except json.JSONDecodeError:
                texts.append(block_text)

    inner = result.get("structuredContent")
    if not isinstance(inner, dict):
        inner = next((value for _, value in parsed_texts if isinstance(value, dict)), None)

    message = "\n".join(texts)
    if inner is not None and isinstance(inner.get("message"), str):
        message = inner["message"] or message
    image = images[0] if images else None
    extra_images = images if len(images) > 1 else None

    if result.get("isError") is True or (inner is not None and inner.get("success") is False):
        details = inner or {}
        return Envelope(
            ok=False,
            error=details.get("error") or "fusion_error",
            message=message,
            traceback=details.get("traceback"),
            image=image,
            images=extra_images,
        )

    if inner is not None or images:
        return Envelope(ok=True, message=message, result=inner, image=image, images=extra_images)
    if parsed_texts:
        block_text, value = parsed_texts[0]
        return Envelope(ok=True, message=block_text, result={"raw": value})
    if texts:
        return Envelope(ok=True, message=message)
    if content:
        return Envelope(ok=False, error="unexpected_content_type", result={"content": content})
    return Envelope(ok=False, error="empty_content")


def parse_stdout_json(envelope: Envelope) -> dict[str, Any] | None:
    """Many tools print their result as a single JSON line on stdout.

    Returns the parsed dict, or None if the message is not JSON.
    """
    if not envelope.ok or not envelope.message:
        return None
    msg = envelope.message.strip()
    if not msg.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(msg)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


# Re-export for convenience
__all__ = ["Envelope", "from_fusion_response", "parse_stdout_json"]
