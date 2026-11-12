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

## Verification on November 11

`scripts/live_durable_execute.py` exercised local Fusion Personal 2706.1.17 in two
disposable fixtures. A parameter edit from 10 to 11 mm produced an independently
measured 11 mm body height and 6,600 mm³ volume, with Unicode stdout and separate
stderr retained. Reusing the same ID did not execute again; changed input under
that ID was refused. Edit-to-readback time was 265 ms in this run.

A second script recorded its recompute stage, then deliberately raised an optional
diagnostic exception. The failure and stage survived; a subsequent mutation was
blocked. Fresh geometry inspection found 11 mm, although the stage recorded the
intermediate 12 mm expression: Fusion reverted this particular failed parameter
operation. The first test draft incorrectly expected persistence and was corrected
after inspecting the outcome. No general rollback guarantee is inferred. Explicit
reconciliation preceded a targeted restoration to 10 mm / 6,000 mm³.

Another live native script completed while the test deliberately replaced its
transport reply with a failure. Its terminal receipt was recovered without a
second dispatch. This is injected reply loss, not a reproduced native timeout.
The ordinary raw print probe worked in this session; the intermittent Autodesk
output defect was not reproduced or declared fixed. Cross-call stream identities
differed because Autodesk manages capture between calls; stream restoration inside
the wrapper is covered separately by in-process automated tests.

Selected witnesses in all three pre-existing documents and the protected fixture
remained intact. Both owned fixtures were closed and the original active document
was restored. Independent code reviews covered receipt limits, failure handling,
async/generator refusal and the live fixture. Automated results and CI accompany
the PR; the checks establish only the stated execution and geometry outcomes.
