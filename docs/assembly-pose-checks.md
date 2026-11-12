# Sampled assembly pose interference

`validate_design` accepts a `pose_interference` check:

```json
{"type":"pose_interference","body_a":"Moving","body_b":"Stationary","frame":"root","max_overlap_mm3":0,"poses":[{"id":"closed","translation_mm":[0,0,0]},{"id":"open","translation_mm":[10,0,0],"rotation_deg":90,"rotation_axis":[0,0,1],"pivot_mm":[0,0,0]}]}
```

`body_a` moves relative to its current root-frame pose; `body_b` stays fixed. Each pose rotates around the root-frame pivot, then translates in root-frame millimeters. Rotation defaults to zero, the axis to root Z, and the pivot to the root origin. IDs must be unique within the check. The threshold is a nonnegative overlap volume in mm³. Results include each sample's status, method and measured overlap when available. Violation takes precedence over unavailable; the overall check passes only if every sample passes.

The check resolves current bodies and occurrence contexts, copies native BReps, applies occurrence and pose transforms to temporary copies, and uses a temporary intersection. Originals are never moved or inserted into the model. Strictly disjoint axis-aligned boxes prove zero overlap. Contact, failed Boolean operations, empty/non-solid Boolean output, stale selectors, and invalid geometry may be unavailable; unavailable is not a pass. The limit is 32 poses per check, 128 per call, 512 faces per body and 1,024 per pair.

These are discrete caller-supplied poses, not a continuous-motion proof, path planner, clearance measurement, or moving-tool envelope. Include every relevant moving body and stationary obstacle in separate checks, sample appropriate poses, and inspect all statuses. Geometry and kernel tolerances can make intersections unavailable. Test representative model geometry in the installed Fusion version before relying on results.

Fusion's documented [temporary Boolean operation](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_TemporaryBRepManager_booleanOperation.htm), [temporary transform](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_TemporaryBRepManager_transform.htm), and [occurrence transform2](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Occurrence_transform2.htm) describe the native operations used here.

## Verification

On November 11, local Fusion Personal 2706.1.17 produced the expected synthetic
intersection volumes: 500 mm³ for translation, 1,000 mm³ after a 90-degree
rotation, 2,000 mm³ for a 45-degree rotated body contained by a larger box, and
500 mm³ in a nested rotated/translated occurrence. Two separated poses passed
the strictly disjoint-bounds check. The touching pose returned `unavailable` for
an empty/non-solid intersection; no positive clearance was inferred.

The nested body's independently expected root bounds were [80,60,10] to
[100,70,20] mm. Selected primary body/parameter/occurrence witnesses matched
before and after checking, as did the protected fixture and all three existing
documents. The seven-pose check plus preservation inventories took about
7,799 ms in this run. This is a measured fixture observation, not a latency bound.

`scripts/live_pose_checks.py` supplies prepare/check/inspect/cleanup actions.
Its offline box kernel tests only right-angle rotations for intersection volume;
the arbitrary-angle case above was measured in real Fusion. Automated tests also
cover false/raised Boolean results, missing/ambiguous/stale targets, repeated
definitions, source preservation, invalid input and work limits. Kernel failure
remains unavailable; the implementation does not perturb geometry to force a pass.
