# Durable custom execute

`execute` remains a first-class custom Python path. Its default is the original raw passthrough. For a receipt-backed custom script, provide `durable=True` and a caller-generated canonical UUID `operation_id` that is retained before dispatch. The script still defines a synchronous `run(_ctx)` and receives the original context. Async and generator entry points, and returned awaitables or generators, are rejected as failed execution; top-level code may already have run, so the write barrier remains. Durable mode runs as a potentially mutating operation and uses the same local endpoint lock, ID reservation, unresolved-write barrier, status and reconciliation as typed receipts. A duplicate ID with the same input retrieves its existing outcome; different input conflicts. Never retry an unknown mutation with another ID.

```python
# execute(script=..., durable=True, operation_id="<retained canonical UUID>")
def run(_ctx):
    # Validate the target, then perform a bounded mutation.
    receipt_stage("essential_edit_completed", {"feature": "Bracket"})
    # Optional diagnostics can fail after that checkpoint.
    print("diagnostic completed")
```

`receipt_stage(name, data=None)` is injected into the script's global namespace. It atomically stores up to 16 ordered checkpoints; names are 1–128 UTF-8 bytes and each JSON-compatible checkpoint is at most 4096 bytes. Invalid, non-finite, or oversized data raises. A later failed checkpoint does not erase the last persisted checkpoint. Checkpoints are script assertions, not verification or transaction commits. A failure after a checkpoint remains unresolved and cannot clear the write barrier by itself.

Durable execution captures Python stdout and stderr separately, each limited to 32 KiB UTF-8 and 64 KiB of escaped JSON, with truncation flags. Scripts are limited to 1 MiB UTF-8. Inspect `declared_result` via `get_operation_status`; completed execution also returns the captured output in its JSON message. A bounded exception message, exception type and saved stages are available in failure status; a recorded exception does not establish that all execution has ended or determine the geometry state. Streams are restored in `finally` even on exceptions; exceptions propagate from the wrapper. Redirecting Python streams affects the shared interpreter while the script runs, so unrelated threads can also write to those streams. Native output and background work are not guaranteed captured. Imports and the script body run inside the wrapper; syntax is checked before dispatch. A script that starts asynchronous work is responsible for its lifetime.

A completed receipt means the Python call returned, not that Fusion's geometry is correct or remains unchanged. A failed script can leave edits in place. Status is historical evidence; independently establish execution termination and inspect the target before using the existing `reconcile_operation` process. Reconciliation does not prove success or rollback. Scripts and external clients that bypass this coordinator are outside its locking and barrier protection. Durable mode does not patch Autodesk's writers or assert a root cause for transport failure.

## Live fixture plan

On a disposable, identified Fusion document, retain a fresh UUID and run a small named mutation followed by `receipt_stage`, print Unicode output, and verify the receipt and independent geometry measurements. Repeat with an optional diagnostic deliberately raising after the checkpoint: inspect stage and failure status, independently establish the execution ended and examine the actual document, confirm a later coordinated write is blocked, then use documented reconciliation with fresh bound readback. Separately exercise a deliberately lost/empty transport response and retrieve its late terminal receipt without redispatch. Record Fusion version, document identity, operation IDs, expected and actual measures, and restoration of any test document. These fixtures require a live session and are not implied by offline tests.
