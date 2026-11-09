# Field feedback implementation plan

Prepared 2026-11-09 from the completed field run against
`c671b4d6d562444d8df91774afd517288391a80e`, fusion-cad-mcp 0.4.0 and Fusion
2706.1.17 Personal. Status: implemented in 0.5.0 with the bounded scope below.
See [verification results and remaining limits](field-followup-validation.md).

The five delivery items are implemented and exercised against local Fusion.
Scoped evidence remains a read-only/checkpoint extension, not an atomic input
to every managed edit. Face-distance checks use reacquired current handles.
Texture bytes and UVs were preserved in the synthetic archive, but the expected
checker texture was not observed in the viewport; appearance fidelity is unknown.
Build cache recipes remain the existing parameter-edit contract. Cropping is
excluded. The acceptance list below records the design intent; the validation
report distinguishes live checks, offline fault tests and remaining limitations.

The run demonstrated native parametric construction, custom Python, local
archives and recovery. Its most useful findings concern the reliability of
measurements, comparisons and failure classification. The next increment should
make those workflows reusable before adding modeling operations.

Keep Autodesk Fusion as the engine, processing local, existing tool signatures
compatible and custom Python a first-class option. Extend existing helpers and
tools; do not introduce a parallel orchestrator, receipt system or model format.

## Delivery order

| Order | Deliverable | Completion condition |
| --- | --- | --- |
| 1 | Correct export preflight outcomes | Proven lookup refusals leave no writer barrier; uncertain operations still do. |
| 2 | Strict scoped evidence comparison | Missing, extra or ambiguous entities cannot silently pass; reordered equivalent records can. |
| 3 | Selected face distance requirements | Native witness points and explicit units/tolerances support an edit, check and restore demonstration. |
| 4 | OBJ references and mesh preservation | A synthetic reference bundle imports, can be inspected and survives a verified local archive reopen. |
| 5 | Bounded batch mesh rays | Ordered, finite-range probes report hits and missing coverage and recheck retained adverse witnesses. |

Steps 2 and 3 form the first complete edit-and-check increment. Step 4 extends
that proven contract to mesh references. Step 5 depends on explicit mesh identity
and frames from step 4. Preparation of synthetic fixtures can proceed in parallel;
one owner must coordinate shared evidence schema and resolver changes. Each step
can ship separately after its own checks pass.

## 1 Correct export preflight outcomes

The field export failed because a generated body name had gained a suffix. Its
selector returned before destination directory creation or export, but omitted
the existing `mutation_state: "not_started"` classification. The receipt layer
therefore correctly treated the incomplete declaration conservatively.

Update only proven pre-write body and occurrence selector returns in
`tools/io.py`. Preserve selector diagnostics and use existing receipt handling in
`receipts.py`; do not relax the writer barrier generally. Errors after filesystem
work or native export starts retain their current conservative handling until
separately proven safe.

Acceptance:

- Missing and ambiguous selectors retain their actionable errors and report no
  external write. A nonexistent destination parent remains absent; an existing
  destination remains byte-identical.
- A subsequent distinct valid export dispatches. Reusing an operation ID remains
  deduplicated and different inputs under the same ID remain refused.
- Injected failure after export starts and unresolved completion retain their
  barriers and cannot trigger automatic mutation replay. Lost replies with
  conclusive, nonconflicting durable terminal evidence follow existing recovery;
  test that path separately from unresolved reply loss.
- A disposable live Fusion document exercises both lookup refusal and a
  subsequent successful export; inspect the files and receipt outcomes.
- Update the agent recipe to read the complete failure envelope, including
  `error` and `state`, rather than projecting only `result`.

## 2 Strengthen scoped evidence comparison

Reuse and extend workspace capture/evaluation helpers. Keep the existing managed
mutation contract bounded; this work does not authorize arbitrary edits to user
documents. A reusable read-only capture must not implicitly call `computeAll`.
Recompute remains an explicit operation with its own outcome and follow-up read.

Define a versioned evidence shape with explicit document binding, component or
occurrence path, entity kind, selected fields, units and per-field tolerances.
Resolve live targets using existing native bindings and strict resolvers. Across
archive reopen, match declared logical identities within scope and reject
ambiguous matches; do not depend on collection order or portable native tokens.

Record selected body counts, names, bounds and volume accuracy; feature and sketch
identities/counts/status; named parameters; and occurrence transforms. Compare
only declared fields, but require all declared entities and fields. Missing or
unavailable data is explicit and cannot satisfy a required check. Bounds and
volume equality establish those measurements, not complete B-rep equivalence.

Acceptance:

- Reordered records compare correctly. Missing, extra, duplicate and renamed
  entities produce explicit differences according to declared scope policy.
- Empty collections cannot pass a nonempty requirement. Unequal array lengths,
  malformed transforms, nonfinite numbers and incompatible units are rejected.
- Preserve existing compact result contracts and bound capture size. Return
  affected entities and differences rather than repeating the entire design.
- Live fixtures demonstrate unchanged capture, recompute comparison, a changed
  named parameter, a deliberately failed requirement and archive reopen.
- Verify selected unrelated geometry and another open fixture document remain
  intact. Keep unknown execution and verification states distinct.

## 3 Add selected face distance requirements

Extend `validate_design` with a narrowly scoped face-distance check, reusing the
existing handle resolver and `measure(kind="distance")` native operation. Connect
it to the existing workspace requirement/evidence path where its targeting
contract can be preserved. Do not create another generic measurement tool.

Accept explicit face pairs, including faces on the same body; reject the same
entity twice. Return measured distance, both witness points in the documented
root frame and millimetres, target references, tolerance and unavailable reasons.
Faces meeting at an edge may correctly have zero minimum distance. A face-pair
distance does not establish that the faces are opposing walls or that the whole
part meets a minimum wall thickness.

Direct checks use current live handles. After an edit or recompute, reacquire
faces through existing scoped selectors before evaluating the requirement;
stale or ambiguous targets fail closed. Demonstrate reacquisition on a simple
fixture with an unambiguous selection rule. Generic correspondence across
topology changes or archive reopen is not required for the direct check and
must not be inferred from a reused token.

Acceptance:

- Cover same-body pairs, nested occurrence transforms, shared-edge zero distance,
  stale/ambiguous references, failed native measurements and threshold boundaries.
- Use a simple live parametric fixture with known face spacing. Change its named
  length parameter, recompute, measure the expected change, reject a deliberately
  failed requirement, then verify scoped restoration and protected witnesses.
- Reject stale baseline state before mutation. Preserve existing uncertain-write
  and recovery tests; never certify a timeout as successful from an old snapshot.
- Record total edit-to-feedback time and phase timings. Verification must pass on
  actual readback, independent of native feature-health flags.

## 4 Integrate OBJ references and mesh preservation

Extend `import_geometry` for OBJ with explicit source units and existing target
component scoping. Use native `MeshBodies.add`; retain the unit conversion and
occurrence placement explicitly. Additional mesh formats are separate scope
unless their implementation and live checks are equally complete.

Preflight the OBJ, MTL and referenced images as a local dependency bundle. Record
relative dependencies, hashes, missing resources and declared fidelity needs.
Default dependency access to the resolved OBJ directory; any additional local
roots must be explicitly declared. Bound parsing and dependency traversal and
reject paths escaping those roots, including through junctions. Do not fetch
remote resources. Missing appearance resources must not be reported
as full-fidelity import, even when geometry-only import is explicitly requested.

Return created mesh identities, component scope, counts and local/root bounds.
Add an optional caller-supplied operation ID to the existing import surface so a
caller can persist it before dispatch; forward it to existing receipt handling
and preserve compatibility for existing import formats. Reuse receipt
coordination and track created entities for targeted cleanup.
Distinguish verified cleanup from partial or uncertain import; never replay an
unresolved import automatically.

Extend existing inspection/reference paths to enumerate mesh bodies separately
from B-reps. Add scoped mesh witnesses to checkpoint verification: native node
and triangle-index digest, counts, coordinate frame and occurrence transform.
Keep large arrays and bundle manifests in local artifacts; return bounded
summaries and references.

Include declared units, placement, dependency hashes and relevant processing
options in provenance and evidence invalidation. Keep the existing workspace
cache scoped to its declared parameter-edit recipes and archive inputs; do not
add an OBJ import cache in this increment. Geometry verification, image-byte
preservation, UV/material state and observed display are separate results.
Unknown appearance cannot satisfy a required appearance check or be labeled
verified full fidelity; a geometry-only result retains its narrower scope.

Acceptance:

- Use original synthetic fixtures: a small textured OBJ, multiple mesh bodies,
  a missing MTL/image case and a nested occurrence with a known transform.
- Exercise absent/invalid units, duplicate names, changed dependencies, invalid
  paths and dependency escapes, partial import and uncertain completion. Test
  same-ID deduplication and changed-input refusal. Check actual dimensions.
- Export and reopen a local F3D. Compare mesh witnesses and transforms and verify
  unrelated geometry/documents remain intact. Retain existing B-rep regressions.
- Run one bounded texture diagnostic recording import, pre-export and reopened
  appearance/UV/image evidence. Where feasible, reopen from an isolated scratch
  source to distinguish embedded resources from external dependencies or caches.
  Unknown results stay unknown; a general texture repair is not a release claim.

## 5 Add bounded batch mesh rays

Extend the existing mesh ray operations with explicit root/local frame handling,
finite forward travel bounds in millimetres and a bounded batch size. Require a
valid direction, normalize it and use a declared interval
`0 <= start_mm < end_mm` from the caller's origin. Report ordered hits and travel
so a caller can select the nearest, exterior-most or all relevant hits
without silently choosing a surface. Record the selection rule and source mesh
identity, plus the tolerance and treatment of coincident/tangent hits. No-hit and
ambiguous results describe coverage, not free space.

Retain failing ray queries with source identity/frame, selection rule and hit
evidence, and re-evaluate them on later geometry. A more favorable random sample
cannot replace a still-valid failure. If a source identity or transform changes,
revalidate or mark the witness stale. Existing non-ray witnesses remain recorded
but cannot be marked revalidated until their originating method is rerun.

Acceptance:

- Synthetic single/double surfaces, open gaps, tangent/boundary cases, transformed
  occurrences, invalid directions/ranges and work limits.
- A live fixture with known two-surface ray hits and a deliberate uncovered area.
- Report sampling coverage and explicit unknowns; do not claim global clearance,
  containment, watertightness or continuous collision certification.
- Defer new point-to-triangle computation until a validated reusable backend is
  selected; it is not required for this first batch-ray increment.

## Release checks and evidence

For each delivered step, execute generated scripts in meaningful behavioral
tests, then verify the supported operation in disposable local Fusion documents.
Use synthetic fixtures with more than one geometry/parameter configuration. The
private field design is retained as evidence and an unchanged later field test,
not shipped as a fixture or encoded into geometry logic.

Run the full automated suite, lint and packaged-tool/knowledge checks before
integration. Have independent reviewers examine implementation and tests before
publication. Update README, tool reference, skill, capabilities and recipes with
the exact supported scope and measured limits. Keep custom Python explicitly
available even when a typed alternative exists.

Evidence must separate implementation, offline tests and live verification;
record pass/fail/unknown, recovery scope and latency. Use bounded outputs and
keep personal paths, cloud identifiers and private assets out of committed
reports. No acceptance thresholds or benchmark tasks change to favor the result.

See [deferred findings and evidence](field-followup-backlog.md) for remaining
research, case-specific information and limitations of the supplied test scripts.
