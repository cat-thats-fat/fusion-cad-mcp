"""Local operation recovery, with explicit evidence and document identity."""

from ..envelope import Envelope, parse_status_json
from ..execution import execute_typed_script
from ..receipts import get_status, reconcile

_DOCUMENT_PROBE = '''
import adsk.core, json
def run(_ctx):
    doc = adsk.core.Application.get().activeDocument
    identity = str(doc.creationId) if doc and doc.creationId else None
    print(json.dumps({'ok': identity is not None, 'document_creation_id': identity}))
'''


def reconcile_operation(adapter, operation_id: str, expected_document: str,
                        acknowledgement: bool, evidence: str,
                        allow_missing_identity: bool = False) -> Envelope:
    if acknowledgement is not True or not isinstance(evidence, str) or not evidence.strip():
        return Envelope(ok=False, error="reconciliation_refused",
                        message="Acknowledge that execution ended and describe your independent inspection")
    try:
        if len(evidence.encode("utf-8")) > 2048:
            raise ValueError("evidence too long")
    except (UnicodeError, ValueError):
        return Envelope(ok=False, error="reconciliation_refused",
                        message="Evidence must be valid UTF-8 and at most 2048 bytes")
    journal_dir = getattr(adapter, "receipt_journal_dir", None)
    status = get_status(operation_id, **({"journal_dir": journal_dir} if journal_dir else {}))
    if not status.ok:
        return status
    if status.result.get("read_only") is True:
        return Envelope(ok=False, error="reconciliation_not_required", result=status.result,
                        message="Read-only receipts do not require write reconciliation")
    if status.result.get("status") not in {"unknown", "declared_failure_unreconciled"}:
        return Envelope(ok=False, error="reconciliation_not_required", result=status.result,
                        message="This receipt does not have an unresolved write outcome")
    recorded = status.result.get("document_creation_id")
    if not expected_document or (recorded != expected_document
                                and not (recorded is None and allow_missing_identity is True)):
        return Envelope(ok=False, error="document_mismatch",
                        message="Expected document must match the original receipt creation ID")
    probe = execute_typed_script(adapter, _DOCUMENT_PROBE, read_only=True, recovery_probe=True)
    parsed = parse_status_json(probe)
    if not parsed or not parsed.get("ok") or parsed.get("document_creation_id") != expected_document:
        return Envelope(ok=False, error="document_mismatch", state=probe.state,
                        message="Cannot verify that the recorded document is currently active")
    return reconcile(operation_id, endpoint=adapter.url, expected_document=expected_document,
                     acknowledgement=acknowledgement, evidence=evidence,
                     probe_operation_id=(probe.state or {}).get("operation_id"),
                     allow_missing_identity=allow_missing_identity,
                     **({"journal_dir": journal_dir} if journal_dir else {}))
