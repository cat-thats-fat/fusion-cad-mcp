# Local reliability release 0.3.0

## Result

The release has 95 tools. Generated JSON-status modeling, inspection, parameter,
import/export and local-file operations now publish a bounded local result receipt
before attempting stdout. The existing Python escape hatch remains uninstrumented.
No benchmark or user design was modified.

Verified locally on Fusion Personal 2706.1.17:

- Nine live checks passed in disposable documents; numeric evidence is in
  [live-reliability-evidence.json](live-reliability-evidence.json).
- Named parameter changes produced the expected 50 mm enclosure width and
  6,952 mm³ solid volume. Reopening its F3D preserved volume and rigid-group state.
- Fully constrained corner/center rectangles worked on XY, XZ and YZ planes.
- Rigid groups reported two selected members with descendants excluded and
  three actual members with descendants included. Preview and pagination matched.
- A repeated non-idempotent creation request created one occurrence, verified by
  the actual occurrence count. Conflicting inputs for one operation ID were refused.
- A 1,200 × 800 PNG passed dimensions, checksum/structure, size and SHA-256 checks;
  temporary camera changes were restored before publication.
- An explicit failure blocked subsequent writes, allowed diagnostic geometry
  inspection, and required reconciliation before further mutation.
- Both fixtures were closed and the prior active document restored. Existing
  unsaved user documents remained open.

Offline validation: 905 tests passed; Ruff and whitespace checks passed. The
0.3.0 wheel built and loaded all 95 schemas and packaged knowledge in a separate
installation directory. Fault tests cover lost output, repeated dispatch, process
crash, cross-process locking, corrupt receipts, failed file publication, lexical
output capture and recovery capacity. Unit doubles do not establish native CAD
behavior. Independent reviewers checked the implementation before publication.

## Empty-output investigation

Native calls still returned `success: true` with an empty message during this
session. A read-only diagnostic observed 841 nested Autodesk sanitized writers,
ending at the native capture stream. None was truncated or at its byte limit;
the largest recorded count was 448,009 bytes against a 1,048,576-byte limit.
This observation rules out writer quota exhaustion in that sample. It does not
prove the lifecycle cause or establish that nesting alone causes the failure.

The new live tests recovered their results from local receipts while the native
output channel remained empty. Autodesk writers, limits and installed binaries
were not changed. A separate parser fix recognizes an explicit boolean-status
structured result; an empty native wrapper still cannot establish success.

## Boundaries and deferred work

Receipts are historical script declarations, not a transaction log for the CAD
kernel, current geometry proof or exactly-once guarantee. A process can fail
between a model change and recording its result. Uncertain writes stay blocked;
reconciliation needs an explicit acknowledgement and a fresh document probe.
Missing original identity requires a separate weaker-evidence opt-in.

Coordination covers participating typed calls using the same endpoint spelling
and journal. Raw Python, native document/view operations, other clients and UI
actions remain outside it. After timeout, diagnostic reads may overlap a still
running call; cancellation cannot prove execution stopped. Storage is bounded
to 2,000 operation manifests, including 32 reserved recovery probes. There is no
automatic journal retirement; retained IDs must not silently become reusable.

The live assembly is small. Large assembly performance, arbitrary nested/shared
models, every export format and every camera setting have not been exhaustively
tested. A verified PNG is not proof of a useful rendered view or correct CAD.
Existing scoped cleanup does not establish whole-document rollback. Explicitly
provide length parameter units (for example `mm`) when adding length expressions.

Service-path/tool-envelope validation, generalized feature editing and richer
mechanical diagnostics remain deferred. Native SolidWorks import is not exposed
by the documented local Fusion ImportManager API; this release adds no cloud
conversion or paid dependency.

## Official contracts used

- [RigidGroups.add](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_RigidGroups_add.htm): descendant inclusion is an explicit argument.
- [ImportManager.importToNewDocument](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ImportManager_importToNewDocument.htm): returns a separate unnamed, unsaved document.
- [Viewport.saveAsImageFile](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_Viewport_saveAsImageFile.htm): accepts the requested image dimensions.
- [ImportManager](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ImportManager.htm): supported local importer factories.
- [MCP cancellation](https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/cancellation): cancellation is advisory.

Reproduce with `PYTHONPATH=src python scripts/live_reliability.py --run --report REPORT.json`.
The harness identifies owned documents inside each generated script and uses its
own receipt journal. It refuses to discard a fixture that was saved or whose
identity cannot be confirmed.
