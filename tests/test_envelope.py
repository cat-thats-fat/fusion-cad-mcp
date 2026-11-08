"""Tier 1c: envelope parsing tests. No Fusion required."""

from __future__ import annotations

import json

import pytest

from fusion_cad_mcp.envelope import Envelope, from_fusion_response, parse_stdout_json


def _fusion_text_response(text: str) -> dict:
    """Wrap a stdout text payload as Fusion's MCP would return it."""
    return {
        "id": 1,
        "jsonrpc": "2.0",
        "result": {"content": [{"type": "text", "text": text}]},
    }


def test_success_envelope_parses_stdout():
    raw = _fusion_text_response(json.dumps({"message": "ok\n", "success": True}))
    env = from_fusion_response(raw)
    assert env.ok is True
    assert env.message == "ok\n"
    assert env.error is None


def test_fusion_reports_failure_with_traceback():
    raw = _fusion_text_response(
        json.dumps(
            {
                "message": "",
                "success": False,
                "error": "NameError: name 'foo' is not defined",
                "traceback": "Traceback (most recent call last):\n  File ...",
            }
        )
    )
    env = from_fusion_response(raw)
    assert env.ok is False
    assert env.error == "NameError: name 'foo' is not defined"
    assert env.traceback is not None
    assert env.traceback.startswith("Traceback")


def test_jsonrpc_transport_error():
    raw = {"id": 1, "jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}}
    env = from_fusion_response(raw)
    assert env.ok is False
    assert env.error == "Method not found"
    assert env.result == {"code": -32601}


def test_empty_content_is_structured_error():
    raw = {"id": 1, "jsonrpc": "2.0", "result": {"content": []}}
    env = from_fusion_response(raw)
    assert env.ok is False
    assert env.error == "empty_content"


def test_native_plain_text_success_is_preserved():
    raw = _fusion_text_response("not actually json")
    env = from_fusion_response(raw)
    assert env.ok is True
    assert env.error is None
    assert env.message == "not actually json"


def test_parse_stdout_json_extracts_dict():
    env = Envelope(ok=True, message=json.dumps({"bodies": 3, "sketches": 1}))
    parsed = parse_stdout_json(env)
    assert parsed == {"bodies": 3, "sketches": 1}


@pytest.mark.parametrize(
    ("ok", "stdout"), [(True, "just a plain message\n"), (True, "[1, 2]"), (False, '{"a":1}')]
)
def test_parse_stdout_json_rejects_non_object_or_failed_result(ok, stdout):
    env = Envelope(ok=ok, message=stdout)
    assert parse_stdout_json(env) is None


def test_envelope_to_dict_drops_nones():
    env = Envelope(ok=True, message="ok")
    d = env.to_dict()
    assert d == {"ok": True, "message": "ok"}
    assert "result" not in d
    assert "traceback" not in d


def test_envelope_to_dict_keeps_ok_false():
    env = Envelope(ok=False, error="boom")
    d = env.to_dict()
    assert d["ok"] is False
    assert d["error"] == "boom"


def test_image_content_parses_into_image_field():
    """Fusion's screenshot returns content[0] with type=image; envelope must carry it."""
    raw = {
        "id": 1,
        "jsonrpc": "2.0",
        "result": {"content": [{"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"}]},
    }
    env = from_fusion_response(raw)
    assert env.ok is True
    assert env.image == {"data": "iVBORw0KGgo=", "mime_type": "image/png"}
    # message stays empty for image responses
    assert env.message == ""


def test_mcp_is_error_takes_precedence_over_inner_success():
    raw = _fusion_text_response('{"success":true,"error":"Fusion rejected the command"}')
    raw["result"]["isError"] = True
    env = from_fusion_response(raw)
    assert env.ok is False
    assert env.error == "Fusion rejected the command"


def test_mcp_error_plain_text_is_preserved():
    raw = _fusion_text_response("Fusion is busy")
    raw["result"]["isError"] = True
    env = from_fusion_response(raw)
    assert env.ok is False
    assert env.error == "fusion_error"
    assert env.message == "Fusion is busy"


def test_structured_content_takes_precedence_and_retains_all_images_in_order():
    raw = _fusion_text_response('{"message":"compatibility copy","success":true}')
    raw["result"]["structuredContent"] = {"message": "structured", "success": True, "count": 2}
    raw["result"]["content"] += [
        {"type": "image", "data": "AAAA", "mimeType": "image/png"},
        {"type": "text", "text": "secondary text"},
        {"type": "image", "data": "BBBB", "mimeType": "image/png"},
    ]
    env = from_fusion_response(raw)
    assert env.ok and env.message == "structured"
    assert env.result["count"] == 2
    assert env.image == env.images[0]
    assert [image["data"] for image in env.images] == ["AAAA", "BBBB"]


def test_structured_content_does_not_require_a_text_copy():
    raw = {"result": {"structuredContent": {"count": 2}, "content": []}}
    env = from_fusion_response(raw)
    assert env.ok and env.result == {"count": 2}


def test_text_before_image_preserves_both_without_structured_content():
    raw = _fusion_text_response("Screenshot caption")
    raw["result"]["content"].append({"type": "image", "data": "AAAA", "mimeType": "image/png"})
    env = from_fusion_response(raw)
    assert env.to_dict() == {
        "ok": True,
        "message": "Screenshot caption",
        "image": {"data": "AAAA", "mime_type": "image/png"},
    }
