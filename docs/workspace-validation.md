# Workspace V1 validation

Version 0.4.0 passed 24 workspace checks and nine existing-tool regression checks on a local Windows session of Fusion Personal 2706.1.17. The main disposable F3D fixture contained a solid driven by one length parameter, a separate protected solid and a placed occurrence. Each verified edit used a fresh bound read after the edit callback. Published archives were checked after opening a fresh document. [Live evidence](live-workspace-evidence.json)

## Observed results

| Check | Evidence |
|---|---|
| Parameter propagation | Width changed from 60 to 70 mm and solid volume from 9,000 to 10,500 mm³. The protected 1,000 mm³ solid and the occurrence's pose passed their baseline checks. |
| Target guards | A stale prior expression and an unresolved entity refused before mutation. The wrong active target, a user-bound target, an ambiguous document name and a closed handle also refused. |
| Scoped rollback | An invalid geometry edit restored the prior 70 mm width. The transaction snapshot and declared witnesses passed inside the callback, and a separate bound probe confirmed the restored baseline after the callback returned. |
| Failed requirement | An edit reached 75 mm while its width requirement called for 80 mm. The result recorded completed execution and failed verification. |
| Managed Python | Trusted Python applied the requested edit, and independent expression and geometry readback verified it. Repeated Python manifest builds ran the recipe each time. |
| Partial custom failure | A Python recipe changed the width and renamed a protected body, then raised. Targeted cleanup restored the 60 mm parameter while the rename remained. The receipt and workspace barriers held, generic reconciliation refused, and the fault workspace recorded zero checkpoints. The harness disposed of this test copy separately and retained the unresolved ledger and receipt. |
| Checkpoint and cache | A baseline checkpoint passed F3D reopen checks. The verified build reopened the same archive on a cache hit and ran zero construction calls. Changing only the checks reused the geometry, recorded the failed check and preserved the accepted pointer. Changing the parameter triggered a new construction call and produced a 65 mm result. |
| Restore isolation | Restore opened a fresh 70 mm document from the accepted archive. The ongoing 64 mm working copy and the 60 mm source remained available at their measured widths. |
| Late completion | A real 0.2 s client deadline returned an uncertain result after 392.4 ms and fenced the next write. Recovery waited for a clean terminal receipt, checked the same live target and linked a resolution without replaying the edit. The ledger retained the original uncertain record and a subsequent edit verified. |
| Cleanup | The final cleanup closed 15 owned fixture documents. The closed-target guard and partial-failure exercises each disposed of a separate fixture copy. The harness confirmed reference validity and dirty flags for all eight pre-existing documents, along with root body bounds and volumes and root body, occurrence and timeline counts where applicable. It restored the previous active document. |

The first ambiguity fixture assumed Fusion would keep duplicate body names. Fusion assigned unique names, so that setup did not exercise ambiguity. The replacement used two disposable documents with the same name and observed an explicit refusal before a parameter change.

The existing-tool regression covered constrained rectangles, parameter receipts and B-rep readback, rigid-group membership, duplicate refusal, viewport PNG export, archive reopen, the recovery barrier and fixture cleanup. An editable 0.4.0 installation exposed 104 tools over MCP stdio. `find_tool`, `workspace_bind` and `doc_state` returned successfully. The offline suite passed 1,083 tests, and Ruff passed for the package and tests.

## Timing

| Operation | Local observation |
|---|---:|
| Guarded edit with post-callback readback | 439.6 ms p95 over 20 samples |
| Seed build with archive reopen | 2,363.9 ms |
| Cache hit with fresh verification | 1,565.6 ms |

The edit timing includes the guarded mutation and separate native readback. It excludes fixture setup and archive operations. The build and cache observations are individual runs on this small fixture. Larger documents, different machines and other Fusion versions need their own measurements.

## Receipt output failure

In an earlier run, the instrumented wrapper wrote its terminal receipt and then attempted a final `print`. Autodesk's capture chains had accumulated 1,036 `NsSanitizedWriter` instances per stream. Printing through the chains raised `RecursionError`. The journal contained 36 pairs of terminal and failure records, including edit, export, open and read calls. A separate read of the edited target returned the pre-edit value even though the callback had recorded changed measurements.

A one-time repair collapsed the stale writer chains in the running test session and restored raw output. It changed the session's output wrappers only. The shipped correction is in the typed wrapper, which now writes the terminal result to the local journal and returns without a final stdout call. The private-writer repair was a local diagnostic. Upstream writer accumulation can still affect raw output, and empty replies can have other causes.

When terminal and failure records coexist, the adapter reports an unknown outcome and retains the write barrier. A terminal declaration records what the callback saw. A separate bound probe establishes current workspace edit evidence, and an archive reopen checks what was persisted. If the post-callback rollback probe is unavailable or differs from the baseline, the workspace job stays unresolved. Late-completion recovery uses a clean terminal receipt and a fresh probe from the reserved read-only receipt lane.

Offline fault tests recreate conflicting records, exercise a throwing stdout writer and simulate a native change that disappears after the callback. [Receipt tests](../tests/test_receipts.py) · [Workspace coordinator tests](../tests/test_workspace_coordinator.py)

## Scope and reproduction

Workspace V1 verifies declared checks in a self-contained parametric design. Measurements cover the selected root-frame B-rep and occurrence witnesses and their stated tolerances. Broader model correctness and unmeasured changes remain outside that claim. A failed custom recipe with unresolved effects stays fenced in V1. Recovery handles late completed edits with matching live evidence.

Raw `execute` remains a first-class option for coordinated native operations and specialized inspection. It uses Autodesk's Python execution and output path. Workspace write coordination covers participating calls through the local endpoint and receipt journal. Other clients and Fusion UI activity can still change the model.

Run the opt-in harness from an editable checkout with Fusion running and idle. It creates its own disposable documents and journal. [Live harness](../scripts/live_workspace.py)

```text
python scripts/live_workspace.py --run
```
