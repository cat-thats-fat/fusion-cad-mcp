"""execute: raw script passthrough (escape hatch per V2 spec Section 4 Group 7)."""

from __future__ import annotations

from ..adapter import FusionAdapter
from ..envelope import Envelope


def run(adapter: FusionAdapter, script: str, *, durable: bool = False, operation_id: str | None = None) -> Envelope:
    """Pass the script straight through to Fusion. No validation, no wrapping."""
    if type(durable) is not bool:
        return Envelope(ok=False, error="invalid_input", message="durable must be boolean")
    if durable:
        return adapter.execute_durable_script(script, operation_id=operation_id)
    if operation_id is not None:
        return Envelope(ok=False, error="invalid_input", message="operation_id requires durable=True")
    return adapter.execute_script(script)
