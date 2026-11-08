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
| Full suite, Python 3.12.14 | 785 passed, no skips |
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

The [P0 regression report](prebenchmark-p0-live.json) records 30 passed checks and
4 blocked checks on an intermediate upgrade revision: real geometry, parameter
propagation, nested authoring, handles, assembly poses, joints, interference,
local file exchange and camera/timeline restoration. All 36 inspected features
and sketches were healthy. The test's unsaved document was closed.

The [interrupted capability report](prebenchmark-capabilities-interrupted.json)
preserves seven completed checks and its overall failure status. It is **not a
completed live pass of the final working tree**. Completed observations include:

| Capability | Independently checked evidence |
| --- | --- |
| Nested rotated component inspection | Body bounds `[92,30,10]` to `[100,42,12]` mm |
| Absolute placement relative to a parent | Bounds `[72,40,13]` to `[80,52,15]` mm; same-pose retry reports no change |
| Semantic geometry and feature inspection | One top face at Z=15 mm; expected-count refusal for six planar faces; native feature parameters |
| Shared insertion and metadata | Duplicate operation refused; metadata round-trip; shared definition edit refused; second occurrence bounds checked |
| Clearance requirements | Measured 8 mm; 7 mm threshold passes, 9 mm threshold fails; bounds and no-interference checks pass |
| Contained solid interference | 125 mm³ overlap detected even with positive distance between surfaces |
| Parametric enclosure | 11,712 mm³ initially; width edit changes it to 13,232 mm³ with bounds ±35/±20/Z0..20 mm; duplicate name refused |

Fusion's own MCP subsequently returned an empty successful reply even for a
direct read-only print probe. The same symptom occurred independently of this
adapter. Restarting the desktop application temporarily restored output; the
cause is unresolved. No Autodesk output writer or security mechanism was patched.
The interrupted harness could not confirm cleanup through stdout. A separate
read-only diagnostic confirmed its fixture was closed and only a blank,
unmodified document remained. The original report is retained without rewriting
its cleanup status as a successful harness result.

The final live harness additionally checks hierarchy, independent component
copies, scoped-handle placement/validation, bosses, signed side cutouts, targeted
failed-cut recovery and sweep parameter propagation. The reports do not yet
record passing live results for those checks. A separate Fusion probe confirmed
that a linked sketch point followed its construction-plane offset when edited.
That observation covers the anchor only. Run both commands from the checkout
with `PYTHONPATH=src`.

```text
python scripts/live_regression.py --run --report outputs/regression.json
python scripts/live_capabilities.py --run --report outputs/capabilities.json
```

Both scripts create disposable unsaved documents. Typed test scripts check the
fixture's identity, name and unsaved status in the same call before their body
executes. Cleanup checks those properties again and requires an explicit close
acknowledgement. Missing creation or cleanup replies leave cleanup unverified.
Reports omit tokens, document identities, account data and local paths.

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
