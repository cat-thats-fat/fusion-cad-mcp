"""Routing and receipt metadata tests; no Fusion geometry is emulated."""

from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.execution import execute_typed_script
from fusion_cad_mcp.tools import assembly, inspection, io, parameters, parametric_sketch


class ReceiptAdapter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute_typed_script(self, script, *, read_only=False, operation_id=None, recovery_probe=False):
        self.calls.append((script, read_only, operation_id, recovery_probe))
        return Envelope(ok=True, result=self.result, state={"operation_id": "receipt", "execution_outcome": "completed"})

    def execute_script(self, *args, **kwargs):
        raise AssertionError("typed tool must use typed execution")


@pytest.mark.parametrize(
    "tool,read_only,operation_id",
    [
        (lambda a: parameters.update_parameter(a, "width", "2 mm", operation_id="id"), False, "id"),
        (lambda a: io.export(a, "step", "C:/temp/model.step", operation_id="id"), False, "id"),
        (lambda a: inspection.inspect_assembly(a), True, None),
        (lambda a: assembly.create_rigid_group(a, ["one", "two"], preview=True), True, None),
        (lambda a: parametric_sketch.create_parametric_rectangle(a, "rect", "1 mm", "2 mm"), False, None),
    ],
)
@pytest.mark.parametrize("status", [True, False])
def test_typed_wrappers_use_receipt_route_and_preserve_state(tool, read_only, operation_id, status):
    result = {"ok": status, "error": None if status else "refused"}
    adapter = ReceiptAdapter(result)
    env = tool(adapter)
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1:] == (read_only, operation_id, False)
    assert env.ok is status and env.result == result
    assert env.state["operation_id"] == "receipt"


def test_lightweight_adapter_fallback_does_not_claim_receipts():
    adapter = type("Adapter", (), {})()
    adapter.execute_script = Mock(return_value=Envelope(ok=True, message="{}"))
    assert execute_typed_script(adapter, "script", read_only=True).ok
    adapter.execute_script.assert_called_once_with("script", read_only=True)
    assert execute_typed_script(adapter, "script", operation_id="id").error == "receipts_unavailable"


def test_reconcile_requires_fresh_matching_receipted_probe(monkeypatch):
    from fusion_cad_mcp.tools import operations

    adapter = ReceiptAdapter({"ok": True, "document_creation_id": "doc"})
    adapter.url = "endpoint"
    monkeypatch.setattr(operations, "get_status", lambda _: Envelope(ok=True, result={"document_creation_id": "doc", "status": "unknown"}))
    reconcile = Mock(return_value=Envelope(ok=True))
    monkeypatch.setattr(operations, "reconcile", reconcile)
    result = operations.reconcile_operation(adapter, "original", "doc", True, "inspected")
    assert result.ok and adapter.calls[0][1] is True and adapter.calls[0][3] is True
    assert reconcile.call_args.kwargs["probe_operation_id"] == "receipt"
    assert reconcile.call_args.kwargs["expected_document"] == "doc"

    adapter.result = {"ok": True, "document_creation_id": "other"}
    reconcile.reset_mock()
    result = operations.reconcile_operation(adapter, "original", "doc", True, "inspected")
    assert not result.ok and result.error == "document_mismatch"
    reconcile.assert_not_called()


@pytest.mark.parametrize("evidence", ["é" * 1025, "\ud800"])
def test_reconcile_rejects_oversized_or_invalid_utf8_before_probe(monkeypatch, evidence):
    from fusion_cad_mcp.tools import operations

    adapter = ReceiptAdapter({"ok": True})
    status = Mock()
    monkeypatch.setattr(operations, "get_status", status)
    assert operations.reconcile_operation(adapter, "id", "doc", True, evidence).error == "reconciliation_refused"
    status.assert_not_called()
    assert adapter.calls == []


def test_reconcile_rejects_unresolved_read_only_receipt_before_probe(monkeypatch):
    from fusion_cad_mcp.tools import operations

    adapter = ReceiptAdapter({"ok": True})
    monkeypatch.setattr(operations, "get_status", lambda _: Envelope(ok=True, result={"status": "unknown", "read_only": True, "document_creation_id": "doc"}))
    assert operations.reconcile_operation(adapter, "id", "doc", True, "inspected").error == "reconciliation_not_required"
    assert adapter.calls == []


@pytest.mark.parametrize("read_only", [True, False])
def test_receipt_status_exposes_recorded_read_only_flag(monkeypatch, tmp_path, read_only):
    from fusion_cad_mcp import receipts

    entry = {"read_only": read_only, "endpoint_hash": "endpoint", "fingerprint": "hash"}
    monkeypatch.setattr(receipts, "_entry", lambda *args: entry)
    status = receipts.get_status("00000000-0000-4000-8000-000000000001", journal_dir=tmp_path)
    assert status.ok and status.result["read_only"] is read_only
