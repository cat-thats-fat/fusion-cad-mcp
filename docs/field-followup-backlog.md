# Deferred field findings and evidence

Recorded 2026-11-09 from the field run against
`c671b4d6d562444d8df91774afd517288391a80e` and version 0.4.0. The actionable
implementation sequence is in [the follow-up plan](field-followup-plan.md).
Items here are retained for later evaluation, not commitments for that increment.

## What the baseline established

The saved evidence records a native two-solid design, 17 constrained sketches,
28 healthy features, a measured parameter change/restoration and a local archive
reopen. Custom Python supported complex native construction without requiring
changes to the MCP or its skill. The delivered archive's SHA-256 matched the
field report when checked during review. Repository HEAD was still unchanged
and clean at the start of that review.

The accepted archive checks include exact scan node/index records and occurrence
transforms, while physical scale remained provisional. A selected-face minimum
of 1.24556 mm was independently reproduced despite a healthy nominal 3.2 mm shell;
later construction restored 3.2 mm on the measured main-wall pairs. These are
scoped measurements, not global thickness certification.

The prototype retained explicit failures: 356 strong captured seating-overlap
samples, a 2.959114 mm clearance witness below a provisional 3 mm requirement,
and failed straight-axis assembly. Some sampled angled poses passed. Full fit,
continuous assembly motion and physical performance remained unknown.

## Deferred engineering work

| Finding | Retained direction | Condition for promotion |
| --- | --- | --- |
| A raw Python parameter trial completed and restored geometry despite a timed-out reply | Reproduce with separate dispatch/start/completion/readback timings. Consider opt-in durable custom-script execution using existing contracts. Keep raw Python available. | Identify the timeout boundary and a repeatable failure; demonstrate added value beyond existing receipts/status and file readback. Do not raise global timeouts or retry mutations on speculation. |
| Native shell, thicken, fillet and nearly coincident joins failed on some constructions | Capture exact error, affected feature state, protected witnesses and alternatives as compact diagnostic recipes. Preserve failed experiments. | A generalizable synthetic reproducer and a measurable diagnostic benefit. Do not build automatic surface redesign or arbitrary feature recovery. |
| Embedded diffuse JPEG bytes matched, but the reopened mesh displayed a metallic appearance | Separate image payload, UVs, material channels, display settings and archive portability. | The plan's controlled texture fixture identifies the failing stage. Repair scope follows evidence; no root cause is currently established. |
| Recursive Fusion Data API traversal found the intended older design where existing discovery surfaced another candidate | Consider bounded recursive discovery with project/folder path, lineage/version, ambiguity and pagination. Existing linked designs may be inspected without uploading new references. | Confirm current discovery ownership and API coverage and collect a small disambiguation fixture. Keep cloud discovery separate from the local modeling contract. |
| Positive assembled socket clearance did not establish insertability | Evaluate caller-supplied rigid poses on temporary body copies, reporting per-pose intersection/distance and sampling scope. | The base evidence/measurement contract is proven and a recurring assembly need justifies a typed extension. No motion planner or swept-volume certificate. |
| Local point-to-triangle and multi-axis containment scripts helped find scan contacts | Preserve methods, coverage rules and adverse witness coordinates as research leads. | A validated backend, coordinate contract and open/nonmanifold/tangent test set. Point samples do not establish global contact or missing-surface geometry. |
| Section/path analysis helped detect blockage and estimate passage areas | Potential bounded section or path-witness recipe with explicit frames and failed-loop handling. | Multiple designs demonstrate reuse; no automatic duct planner or airflow/thermal simulation. |
| Nominal dimensions and healthy features did not establish global wall thickness | Continue selected-face and section witnesses; retain unknown regions. | A feasible, validated general method is separately scoped. Full thickness/strength/manufacturing certification is not part of the current plan. |

## External workflow and physical information

Keep slicing and physical-test findings with the application project. They are
not evidence of an MCP defect. Successful slicing did not establish support
removability, fine-tip reproduction, material suitability or installation fit.
The run retained failed support-extraction and tip-height checks and supplied
small physical fit samples. No physical print was completed during the run.

Missing calibration, seating contact measurements, mounting anchors, moving-part
envelopes, final outlet target and thermal/load conditions require application
evidence. A software release cannot turn absent reference data into verified
geometry. The remaining seating problem is an application result, not an action
to hardcode into the generic MCP.

The report also records a blocked deletion of a temporary browser profile. That
is an environment approval-policy observation, not Fusion modeling feedback.

## Reusable test lessons

- The field archive comparator uses positional `zip` in some comparisons without
  strict cardinality or identity checks. Health/constraint predicates also need
  required-count checks to prevent empty collections from passing. Review found
  matching saved/reopened body and reference counts in this run, so the weakness
  is not evidence of actual geometry loss. The plan addresses the generic helper;
  preserve the original baseline script and report unchanged.
- Twelve archive checks passed in the saved report, but they mix equivalence,
  expected-value and current-state checks. Do not describe them as twelve full
  geometry-equivalence proofs. Bounds/volume equality has limited scope.
- The parameter demonstration restored observed gap, selected bounds, volumes
  and reference records. Its custom script lacks a general failure-safe restore
  path; do not transplant it as a transactional helper without designing and
  testing recovery. The lost reply was reconciled by separate evidence/readback.
- The accepted wall ledger stores scalar distances but omits some face identities
  and witness coordinates. Future measurements should retain both targets and
  witnesses. The 1.27359 mm tessellated planar witness is distinct from the
  1.24556 mm independent native selected-face result.
- An early contact method selected a rear scan surface; an exterior-most policy
  better matched the intended interface. Retain all candidate hits and the
  declared selection rule. Missing hits are unknown coverage.
- More favorable random clearance samples did not invalidate an earlier tighter
  witness. Re-evaluate known adverse witnesses before accepting a revision.
- Five historical parameters were inactive and two constrained 3D curves were
  fixed reference geometry. Parameter presence and fully constrained sketches do
  not imply every dimension drives editable geometry. Recipes should disclose
  fixed/provisional/inactive inputs and prove selected propagation.
- Custom Python and targeted local scripts were productive workflow choices.
  Count avoidable repeated logic, interventions and failures; do not treat script
  usage itself as a failure or require a typed wrapper for every recipe.

## Evidence locator

The original local run directory is `20261108-212000-followup`. Keep it intact.
Its reports and artifacts contain private design/account/path data; do not copy
them wholesale into the repository. The following basenames identify the useful
sources without publishing those identifiers or assets.

| Topic | Local evidence basenames |
| --- | --- |
| Overall findings and software provenance | `BASELINE-FEEDBACK.txt`, `FIELD-REPORT.txt`, `followup-software-provenance.json` |
| Independent shell finding and repaired witnesses | `shell-independent-recompute-audit.json`, `FRESH-bezel-wall-witness.json`, `smooth-floor-native-wall.json`, `ACCEPTED-native-wall-and-bolts.json` |
| Parameter completion and restoration | `18b_accepted_parameter_proof.py`, `ACCEPTED-parameter-proof.json` |
| Archive comparison and its limits | `22_reopen_readback.py`, `23_compare_roundtrip.py`, `ACCEPTED-reopened-native-readback.json`, `ACCEPTED-roundtrip-checks.json`, `ACCEPTED-parameter-roundtrip.json` |
| Import, scan coverage and retained clearance witnesses | `17_add_preserved_references.py`, `verify_fresh_scan.py`, `ACCEPTED-reference-checks.json`, `ACCEPTED-reference-and-scan-assessment.txt`, `ACCEPTED-final-scan-clearance.json` |
| Texture evidence | `ACCEPTED-final-archive-texture-evidence.json`, `ACCEPTED-reopened-fullscan-texture.png` |
| Export outcome and recovery | `receipts/`, `task-document-cleanup.json` |
| Failed surface edits and readback | `SEAT-TRIAL-after-failure-readback.json`, `SEAT-BAND-TRIAL-after-failure-readback.json`, numbered construction/seat-trial scripts |
| Assembly and external slicing | `ACCEPTED-native-joint-motion.json`, `ACCEPTED-joint-motion-fine.json`, `OFFLINE-SLICING-REPORT.txt`, `SLICE-RESULTS.json` |

Review used saved evidence, code inspection and an archive hash check. It did not
repeat native Fusion execution. Future implementation evidence must label its
own automated and live verification separately.
