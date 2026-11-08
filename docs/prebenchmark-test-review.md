# Prebenchmark test review

This review describes the evidence from tests added or changed in the current
working tree. It does not replace the [historical baseline audit](../tests/audit/README.md),
which records a different revision. A generated-script double executes the Python
sent to Fusion against controlled stand-ins; it does not implement Fusion's
geometry kernel, solver, timeline, or persistence. Passing such a test is not a
live CAD result.

| Test file | Evidence reviewed | Practical limit |
|---|---|---|
| `tests/_assembly_fakes.py` | Shared collection and script-execution doubles make generated Fusion scripts executable offline. | They model only explicitly supplied properties and side effects; API acceptance and object identity must be checked in Fusion. |
| `tests/test_adapter.py` | Real loopback HTTP and controlled transports exercise stalled headers, partial/chunk framing, JSON and SSE drips, timeout cancellation, session expiry, no ambiguous mutation replay, locking, and closed or quarantined workers. | The local server is not Autodesk's server; protocol handling, OS scheduling, and worker cancellation do not establish server-side cancellation or rollback. |
| `tests/test_envelope.py` | Native and structured response blocks, images, error precedence, and malformed/missing wrapper status are checked. | Fake responses do not show what Fusion actually returned or whether an executed mutation changed a model. |
| `tests/test_script_outcomes.py` | Missing stdout is distinguished for mutating, read-only, export, and raw-script calls; write uncertainty is preserved. | Injected empty replies cannot establish why stdout was lost or recover an already executed mutation. |
| `tests/test_live_harness.py` | Executed fixture guards block a switched, renamed, saved or missing document before mutation. Cleanup refuses unknown creation outcomes, saved fixtures and missing acknowledgements. A cleanup failure cannot produce a passing report. | Controlled document objects test ownership/reporting logic, not the live Fusion document lifecycle. |
| `tests/test_server.py` | In-process FastMCP client/server sessions exercise legacy and new tools plus raw execute: JSON text equals `structuredContent`, `isError` follows the envelope, screenshot image blocks and before/after indices remain ordered, and inspection forwards its read-only request. Registration, selected input preflight, and adapter shutdown are checked. | These are wire tests against doubles, not Fusion geometry or all client implementations. MCP 1.30/Pydantic 2.14 and isolated MCP 1.20/Pydantic 2.12 passed. The package minimum is now MCP 1.30 because 1.20 fails to import with newer Pydantic. Representative tools no longer advertise an output schema; clients consume content/structuredContent. |
| `tests/test_reliability_resolvers.py` | Executed selectors reject shared/ambiguous targets and bad hints; edge-length/type filtering, health uncertainty, safe construction deletion and failed helper cleanup are exercised. A revolve test proves duplicate participant names stop before feature creation. | Synthetic edges and health objects do not establish Fusion's curve geometry, token stability, or actual cleanup behavior. |
| `tests/test_mutation.py` | Reverse cleanup, failed deletion, inventory/body/health/pose changes, repeated occurrence contexts, joint motion, timeline preflight and construction-axis drift are tested. | A `verified` double rollback means recorded observations match in the fake, not that topology, history, saved state, or a real Fusion model is restored. |
| `tests/test_parameters.py` | Batch dependencies, rollback on failure, no-op update, text/numeric handling, unit rejection, expression readback, and lost-formula cases run against parameter/units doubles. | The fake evaluator is not Fusion's expression parser; actual unit conversion, propagation, warning behavior and persistence require live evidence. |
| `tests/test_primitives.py` | Offline doubles cover named enclosure/boss/cutout workflows, dimension expressions, analytic fixture volume, direction/frame reporting, participant selection, no-op rejection, and cleanup. Broken linked anchors at creation and after recompute are refused. | The fake assigns areas, volumes, constraints and recompute behavior itself; it does not prove Fusion closed profiles, constraint solving, feature health, linked-parameter propagation, or precise geometry. Its tests must not be cited as a live modeling result. |
| `tests/test_native_features.py` | Generated sweep scripts check explicit path references, unique naming, bad input, unhealthy/empty results, cleanup and child proxy-token handling using doubles. | No fake sweep can prove path continuity, correct swept solid, actual proxy semantics, or Fusion rollback. |
| `tests/test_inspection.py` | Fake geometry exercises filters, expected counts, pagination, frame labels, feature provenance limitations, nested selection and proxy tokens. | Sample-point filters are not closest-surface queries. Fakes do not prove nested Fusion coordinate frames or stable topological identity. |
| `tests/test_placement.py` | Matrix and occurrence doubles cover absolute/reference-frame placement, snapshot reporting, grounding and shared-parent refusal, operation-ID duplicate guard, copy intent and contextual handles. | The fake snapshot and matrix implementations do not simulate the Fusion joint solver, nested proxy setter, actual persistence, or failure atomicity. |
| `tests/test_references.py` | Metadata input/axis validation, scoped read/write, shared/reference refusal, readback and rollback are exercised against fake attributes. | Fakes do not prove an attribute survives save/reopen, or that component/occurrence frames match a live assembly. |
| `tests/test_validation.py` | Behavioral doubles check clearance plus interference, volume thresholds, conservative bounding-box checks, per-check unavailable status, scoped handles, duplicates, and oversized input refusal. | Supplied distances and intersection volumes are fake; the tests do not establish kernel clearance, nested transform correctness, cavity containment or wall thickness. |
| `tests/test_construction.py` | Added tests require an unknown result when helper creation throws and prevent a successful delete return from masking a still-valid helper. | The other source-generation checks do not prove construction geometry or real Fusion deletion. |
| `tests/test_features.py` | Updated fake supplies actual edge length and healthy state while testing the `any` direction path. | It checks selection and call wiring, not actual fillet shape. |
| `tests/test_features_extras.py` | Existing generator-level feature checks still cover emitted inputs; an implementation-name-only revolve assertion was removed. | String/source checks are not geometry evidence; participant ambiguity is covered behaviorally in `test_reliability_resolvers.py`. |
| `tests/test_geometry_modernization.py` | Updated collections and health doubles exercise extrude/rib/pattern call wiring and selected geometry paths. | Expected results are provided by mocks and do not independently validate Fusion feature dimensions. |
| `tests/test_handle_tools.py` | Updated feature doubles allow handle-resolution and edge-set behavior to execute with explicit health state. | A fake resolved token or successful API call does not prove live token validity, topology, or resulting geometry. |
| `tests/test_sketch.py` | The fake adapter accepts the read-only classification used by sketch inspection calls. | This signature update is compatibility plumbing, not evidence of actual sketch geometry. |

The tests were retained when they exercised a distinct safety decision or API
contract. Source-string assertions were treated as wiring checks, never as proof
of modeling correctness. The revolve helper-name assertion was removed and
replaced with an executable ambiguity/no-feature-add check. Parameterized cases
are reported as cases rather than multiplied into historical audit totals.

Live evidence belongs in the live report, not in the fake-test claims above. At
review time the reported disposable Fusion checks included nested rotated
inspection, relative/absolute placement and no-op handling, shared insertion
and retry identity, clearance thresholds, contained-solid interference, health,
and an enclosure with analytic geometry and parameter propagation. A boss run
had returned empty stdout and was not a demonstrated pass; sweep and other
primitive coverage require their own completed live checks. No conclusion about
those results is inferred from the offline doubles. Unsupported exports/imports,
cloud-file workflows, cavity containment, topology equivalence, and arbitrary
CAD designs remain outside this review.

An independent source-level comparison of the wire change against `HEAD` found
all 75 previously registered tool argument signatures and forwarded return
calls unchanged, with no removed tools. All 88 current registered shims use the
shared envelope or image wrapper. This comparison checks source structure; it
does not itself exercise every tool over the wire or certify external clients.
