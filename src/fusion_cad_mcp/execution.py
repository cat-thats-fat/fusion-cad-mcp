"""Explicit execution route for trusted, JSON-status tool scripts."""

from __future__ import annotations

from .envelope import Envelope


def execute_typed_script(adapter, script: str, *, read_only: bool = False,
                         operation_id: str | None = None, recovery_probe: bool = False) -> Envelope:
    method = getattr(adapter, "execute_typed_script", None)
    if method is not None:
        return method(script, read_only=read_only, operation_id=operation_id,
                      **({"recovery_probe": True} if recovery_probe else {}))
    # Compatible lightweight adapters used by embedders and offline tests do not
    # have durable local execution. Never claim receipt guarantees for them.
    if operation_id is not None:
        return Envelope(ok=False, error="receipts_unavailable",
                        message="This adapter does not support operation IDs")
    return adapter.execute_script(script, read_only=True) if read_only else adapter.execute_script(script)
