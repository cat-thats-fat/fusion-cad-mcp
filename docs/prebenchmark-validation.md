# Pre-benchmark capability verification

Baseline: `7fed4e6` (modernization PR #1). Local environment: Windows, Python
3.12.14, Autodesk Fusion 2706.1.17 Personal. This work preserves the local Fusion
engine and Python escape hatch. No competing CAD engine, paid service, reference
catalog or benchmark change was introduced.

## Changes and scope

- Strict component, feature, plane, axis and joint resolution replaces remaining
  first-match selections. Unsupported child/shared-definition edits refuse before
  changing the wrong component. Existing tool input signatures and handle formats
  remain accepted.
- Transport deadlines now cover headers and framing as well as JSON/SSE bodies.
  Cancellation, session cleanup, queue waits and worker shutdown are bounded.
  Timeouts and malformed output preserve unknown execution outcomes; an uncertain
  mutation is never automatically replayed. Server-side cancellation is not proven.
- Parameter edits and new primitives record their own creations and expression
  edits, check recomputation/health, and attempt targeted cleanup. Recovery reports
  distinguish verified restoration, pose-only restoration, failed recovery and
  unknown outcomes. There is no default document-wide Undo.
- Thirteen composable tools add bounded assembly/feature/geometry inspection,
  component creation/copy/placement, clearance/interference/bounds requirements,
  local reference metadata, enclosure/boss/cutout primitives, and a native sweep.
  General feature dependencies, arbitrary topology naming and an autonomous
  enclosure planner are not claimed.
- All 88 MCP tools return the existing JSON envelope as text and structured
  content, with MCP `isError` matching `ok`. Image blocks remain native. Missing
  typed-script output is an unknown protocol outcome; empty raw-script output
  remains valid when the raw script succeeds.

The [plan and source comparison](prebenchmark-plan.md) records audited revisions,
license decisions and prioritization. No peer-project source code was copied.
The [test review](prebenchmark-test-review.md) documents what each changed test
file proves and what requires live Fusion.

## Automated checks

| Check | Observed result |
| --- | --- |
| Full suite, Python 3.12.14 | 790 passed, no skips |
| Static checks | Ruff over source/tests/scripts and `git diff --check` passed |
| Packaging | Wheel built; installed into a temporary target; all three bundled knowledge searches passed with checkout fallback disabled |
| MCP wire | In-process client/server checks pass for legacy/new/raw tools, structured/text equivalence, error flags, images and required arguments |
| Compatibility review | All 75 existing input signatures and forwarding calls retained; all 88 registrations reviewed |
| Independent review | Separate transport/wire, modeling/recovery, inspection/placement/metadata and test-evidence reviews completed |

These automated checks do not verify Fusion geometry. Real local HTTP servers
exercise headers, chunk framing, silent and heartbeat streams, response drips,
cancellation, recovery and cleanup stalls; none establishes that Autodesk actually
cancelled a dispatched modeling operation.

The seven in-memory wire tests pass with MCP 1.30.0 and Pydantic 2.14.3.
The package now declares MCP 1.30.0 as its tested minimum, retaining `<2`.
An isolated MCP 1.20.0 environment passed the same tests with Pydantic 2.12.5,
but failed to import with Pydantic 2.14.3 because it references a removed private
Pydantic helper. No broad Pydantic cap was added. This is dependency compatibility
evidence, not live Fusion geometry verification.

## Live evidence

The fresh-session [baseline report](prebenchmark-p0-live.json) on Fusion 2706.1.17 completed **30 checks**, with **4 blocked** for unsupported IGES/SAT exports and missing paired import fixtures. All 36 inspected features and sketches were healthy, and the disposable fixture was closed. These checks include real geometry, parameter propagation, nested authoring, scoped handles, assembly poses, joints, interference, local file exchange and camera/timeline restoration.

A separate [capability fixture](prebenchmark-capabilities-live.json) completed **16 checks** and closed its disposable document; all 30 inspected features and sketches were healthy. Its scoped evidence includes:

| Capability | Measured evidence |
| --- | --- |
| Nested inspection, placement and hierarchy | Rotated and nested occurrence bounds and relative absolute-placement readback; repeated placement was a no-op. |
| References and assemblies | Shared insertion, duplicate-operation refusal, scoped-handle placement and validation, reference metadata round-trip, and shared-definition edit refusal. Metadata save/reopen persistence remains untested. |
| Mechanical checks | 8 mm clearance passes a 7 mm requirement and violates 9 mm; contained solids report 125 mm³ interference. Bounds checks remain axis-aligned. |
| Enclosure | 11,712 mm³ initially; width parameter edit produced 13,232 mm³ and bounds ±35/±20/Z0..20 mm; duplicate name refused. An independent variant measured 25,112 mm³. |
| Annular boss and cutout | Boss added 54π mm³ and side cutout removed 64 mm³. Moving the boss anchor updated the actual B-rep. An explicit coincidence constraint was needed: the prior shared-point approach moved sketch geometry but could leave the solid stale. |
| Targeted recovery | A deliberately missed cut reported verified targeted rollback, with the fixture's recorded timeline, body inventory and measurements restored. This does not prove identical topology or general rollback. |
| Native child sweep | A radius parameter edit from 2 to 3 mm changed volume from 80π to 180π mm³, with scoped-body-handle validation and duplicate-name refusal. Child path curves required occurrence proxies; the profile remained native. |

The completed capability report includes the rotated nested sweep. After changing its radius to 3 mm, volume was 180π mm³ and root-frame bounds were `[36,71,12]` to `[56,77,18]` mm.

The same capability report exercises negative cut depth: named `side_depth` changed 3→2→3 mm and total removed volume across two cuts changed 192→128→192 mm³. A cutout X parameter change from -4 to 8 mm was checked against actual B-rep faces at 8 and 16 mm. These observations cover the stated fixtures, not all parameter expressions or future edits.

The [interrupted capability report](prebenchmark-capabilities-interrupted.json) is retained as an interrupted run, not a final pass. In that run Autodesk's own MCP returned an empty successful reply even for a direct read-only print probe. The symptom later recurred during a baseline run after the completed 16-check capability run. An unmodified blank design was independently confirmed through Autodesk's document read; restarting the hidden background Fusion process restored output, and the fresh baseline above completed. The cause remains unresolved; no Autodesk output writer or security mechanism was patched. Do not blindly retry a write after a missing reply.

Run the two disposable-fixture harnesses from the checkout with `PYTHONPATH=src`:

```text
python scripts/live_regression.py --run --report outputs/regression.json
python scripts/live_capabilities.py --run --report outputs/capabilities.json
```

Both scripts check fixture identity, name and unsaved status before mutation and require explicit close acknowledgement. Missing creation or cleanup replies leave cleanup unverified. Reports omit tokens, document identities, account data and local paths.

The related Fusion API references are [assembly proxies](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ComponentsProxies_UM.htm), [path creation](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Path_create.htm), [one-sided extrude extents](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_ExtrudeFeatureInput_setOneSideExtent.htm) and [geometric constraints](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_GeometricConstraints.htm).

## Verification limits and deferred work

- Cleanup verification compares recorded identities, inventories, expressions,
  health, body volume/bounds and poses. It does not prove identical topology,
  timeline history, saved state or viewport state. Partial creation without a
  returned identity remains uncertain.
- Clearance and intersection checks use B-rep measurements. Bounds are axis-aligned
  and conservative; they do not establish cavity containment or minimum wall
  thickness. `query_geometry.near` uses sampled points, not minimum surface distance.
- Feature-face provenance covers current surviving faces; generic dependency
  graphs are explicitly unavailable. Handles can become stale after topology edits.
- The primitive subset is rectangular enclosure/cutout and cylindrical/annular
  boss. Lid interfaces, vent planners, dedicated hole-pattern helpers, threads,
  lofts, arbitrary feature editing/suppression and automatic face mating are
  deferred. Existing lower-level tools and `execute` cover specialized workflows.
- Reference metadata stores supplied geometry intent. The optional source digest
  is validated for format. Keep-outs remain metadata, and attribute persistence
  through save and reopen has not been tested.
- Personal Fusion refused IGES and SAT export; the paired import checks lacked
  fixtures. Those four checks are blocked, not passed. No cloud document save/open
  or paid-extension operation was exercised.
- MCP success/error wire semantics are corrected additively. Clients that ignored
  envelope failures previously may now correctly report a tool error. Input
  schemas remain unchanged; clients should read content/structuredContent rather
  than depend on a generated output schema.
