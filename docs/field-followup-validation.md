# Field follow-up validation — 0.5.0

Validated on 2026-11-09 with local Autodesk Fusion Personal 2706.1.17 on Windows.
The base was `c671b4d6d562444d8df91774afd517288391a80e`. All modeling used synthetic,
disposable fixtures; the private field design, reference files and benchmark were
unchanged. Five existing user documents remained open and their selected state
witnesses were unchanged. Only positively identified test documents were closed.

## Implemented behavior

| Area | Delivered scope |
| --- | --- |
| Export recovery | Missing/ambiguous pre-write selectors report `not_started`; uncertain writes still fence later mutations. |
| Scoped evidence | Versioned exact inventories for selected entity kinds, explicit units/tolerances, compact differences and fail-closed missing/extra/duplicate/unavailable evidence. |
| Face requirements | Current distinct face handles, native minimum distance and root-frame witness points; explicit bounds and tolerance. |
| OBJ references | Native local import, explicit source units, bounded OBJ/MTL/image dependency checks and provenance, exact new-mesh accounting, protected existing-mesh witnesses and receipts. |
| Mesh inspection | Separate mesh inventory, counts, local/root bounds and opt-in native node/index digest. No conversion into B-reps. |
| Checkpoints | Scoped mesh witnesses compared after archive reopen. Reopened workspace copies, restore copies, build seeds and cache copies have a separate receipt-backed recompute before use. |
| Mesh rays | At most 32 finite-range queries, ordered hits, explicit root/local frames, coverage/ambiguity/truncation states, and replay tied to source geometry, entity, document and pose. |

The tool count remains 104. These changes extend the existing interfaces and
receipt/workspace helpers. Custom Python remains available whenever it better
fits an operation. Raw execution does not acquire verification or cache status
merely because a script returned successfully.

## Live evidence

The opt-in [field harness](../scripts/live_field_followup.py) confirmed:

- A missing export body created no destination parent and reported `not_started`.
  A following valid STL export wrote 684 bytes. Replaying the failed lookup's
  operation ID returned its refusal without dispatching again.
- Root face distance measured 10 mm. An explicit 9 mm maximum failed; same-face
  and stale-handle checks were unavailable. A rotated nested part measured 5 mm
  with root witness points `[95,95,10]` and `[95,95,15]` mm.
- A textured OBJ imported with eight native nodes and four triangles. Its local
  bounds were `[0,0,0]` to `[20,20,10]` mm; nested root bounds were `[80,60,10]` to
  `[100,80,20]` mm. Repeated operation IDs reused history; changed units under the
  same ID were refused. A second import with centimetre units measured ten times
  larger while the first mesh witness stayed unchanged. Missing units/resources
  were refused before CAD mutation.
- Local rays hit both surfaces at approximately 5 and 15 mm of travel. Root-ray
  points matched the known nested transform. A deliberate uncovered ray reported
  `no_hit`; interval filtering retained only the intended hit. Identical replay
  remeasured; changing occurrence pose made retained evidence stale.
- A dimensionless parameter with native units `""` passed self-comparison and accepted F3D checkpoint/reopen. Invalid unit types and empty expressions remain rejected.
- Reordered scoped records passed; an omitted record failed. A mesh checkpoint
  passed source/reopened comparison. A named thickness propagated 10 → 12 → 10 mm;
  actual face distance followed it and protected B-rep witnesses stayed intact.
  A stale starting expression was refused. A deliberate 11 mm result against a
  12 mm requirement failed, followed by a verified restoration.
- Restoring the mesh checkpoint yielded a recomputed, inspected working copy without
  claiming current verification. A mesh seed built successfully; a second branch
  reused the same build with no construction call. Separate post-build mesh
  inspection matched the original native geometry digest.

Two final edit-plus-reacquired-face-feedback cycles took **748 and 755 ms**. The
larger workspace regression sampled 20 small-fixture edits: **p95 527 ms**,
excluding setup/archive work. A measured cache miss took 2,829 ms and a hit took
1,918 ms. These are observations on one machine and small fixtures, not latency
guarantees or a claim that arbitrary new edits are cached. The workspace parameter
trial also retained native phase timings: 4.61 ms baseline, 49.18 ms edit and
1.51 ms measurement within 483.87 ms total feedback. Per-phase timings for the
two additional face-feedback cycles were not retained separately.

The existing [workspace regression](../scripts/live_workspace.py) passed all
25 scenarios, including real timeout/late completion recovery without replay,
targeted parameter rollback, stale/ambiguous targets, rejected cached requirements,
preserved accepted pointers and unresolved partial-script fencing. The
[reliability regression](../scripts/live_reliability.py) passed all nine scenarios,
including native constraints, rigid groups, duplicate creation prevention, PNG
readback and F3D reopen. Sanitized results are retained in
[field-followup-evidence.json](field-followup-evidence.json).

The older stdout-only `live_regression.py` stopped during fixture creation when
Autodesk returned empty output. Inspection established that one blank test
document had been created; that exact document was closed without retrying the
creation. This runner did not pass. Receipt-backed runners continued successfully.
The upstream raw-output failure remains a known limitation, not a claimed fix.

## Texture diagnostic

The synthetic archive contained the exact 120-byte source PNG (SHA-256
`b9a8be4033fadf90b3178dcbf8593f2ae6f2ee0df71f66e92dd08b43fab2453d`). The 16 reported
UV scalar values matched before export and after reopen. Both native probes
reported a texture-map control and the appearance name `Steel - Satin`.

The source viewport capture showed a plain surface rather than the red/green
checker. The reopened capture did not establish successful texture display.
Consequently **image bytes and UV preservation passed; appearance fidelity remains
unknown**. No clean-process/resource-isolated rendering test was performed, and
no root cause or texture repair is claimed. See the deferred texture investigation
in [the backlog](field-followup-backlog.md).

## Automated verification and review

- **1,187 passed, 1 skipped** in the final full automated suite. The skipped case
  requires Windows symbolic-link privileges; an independent real Windows junction
  retarget test exercised rejection before native dispatch.
- Ruff, whitespace validation, wheel build and installed-wheel knowledge checks
  passed. A fresh stdio MCP client discovered all 104 tools, checked the new
  argument schemas and capabilities, and read live document state. Fault tests
  exercise malformed/ambiguous evidence, source changes,
  bounded parsing, missing resources, malformed native return sets, stale replay,
  native recompute failure and unresolved execution.
- Independent reviewers checked the implementation and tests. Their findings
  led to fixes for alias retargeting, conflicting dependency roles, malformed
  line endings, bounded file reads, incomplete import accounting and recompute
  coverage for restored copies/build seeds. Automated PR review also identified
  empty units on dimensionless parameters; the correction passed offline and live
  archive tests. CI exposed an incomplete setup in the locally skipped symlink
  test; its fixture was corrected without weakening the dependency check.

Unit doubles establish validation and control flow, not Fusion modeling behavior.
Live tests establish only the fixture operations and inspected requirements above.
The field harness requires exclusive Fusion automation through cleanup because
it tracks new workspace-owned documents through a session registry snapshot.

## Limits and remaining work

- Scoped evidence is an inspection/checkpoint extension, **not an atomic
  precondition for every managed edit**. Existing V1 parameter-edit requirements
  still govern mutation. Face handles must be reacquired after topology edits.
- Face pairs and finite mesh samples do not prove global wall thickness,
  continuous clearance, containment, watertightness, strength or assembly motion.
  Coincident hits can be ambiguous; tangent classification is unavailable.
- Mesh digests are order-sensitive node/index witnesses, not canonical geometry
  or appearance equivalence. Token changes can conservatively invalidate replay.
- Scoped volume explicitly uses `LowCalculationAccuracy` (Autodesk specifies a
  ±1% margin). A tight comparison tolerance measures repeatability, not absolute
  volume accuracy. Existing V1 `BRepBody.volume` has no exposed accuracy bound.
- OBJ input is a deliberately bounded subset. Unsupported MTL options, network
  paths, ambiguous aliases, oversized dependencies or unavailable existing-mesh
  witnesses are refused. Fusion opens source files after the final dependency
  check; callers must keep the local bundle stable through import. A residual
  filesystem race remains. Provenance manifests are not model-build cache entries.
- Mesh-only cleanup cannot certify rollback of every native feature side effect;
  partial/uncertain mutations retain the barrier. An archive open that fails
  before identity registration remains uncertain and requires inspection.
- Build caching still covers the existing declared parameter recipe, with
  current requirement rechecks. No general mesh-processing cache, automatic
  texture repair, global thickness solver or cropping system was introduced.

Implementation follows Autodesk's current
[MeshBodies.add contract](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_MeshBodies_add.htm),
including automatic mesh base-feature creation, and its
[physical-properties accuracy contract](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_BRepBody_getPhysicalProperties.htm).
Other native behavior was checked against the installed Fusion Python SDK and
actual local execution.
