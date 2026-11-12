# Native measurement witnesses and sampled surface fidelity

## Motivation and evidence boundary

An external field run passed its declared STEP bounds/volume checks while 10 of
49 sampled section boundary comparisons failed 0.05 mm, with maximum
point-to-segment difference 1.342516 mm. Retained native measurements reported
about 0.777739 mm to the source versus 0.0000584 mm to the STEP import. The native
`positionTwo` repeated the input point despite nonzero distance. That is a
contradictory position witness, not evidence that the scalar is necessarily wrong.
Independent section evidence and native scalar evidence must remain available.

These changes do not repair that STEP translation, retest the source model or certify
an export. They provide a reusable, bounded way to expose contradictory native
witnesses and rerun explicitly named local surface probes.

## Evidence contract

`measurement.DISTANCE_EVIDENCE_SCRIPT` is shared by `measure`, `face_distance`,
body clearance and `surface_distance`. It separately reports the finite native
scalar, native witness positions and `witness_consistency`:

- `consistent`: scalar and Euclidean witness separation agree numerically.
- `inconsistent`: finite positions contradict the scalar, or the first position
  differs from a surface probe's input point.
- `unavailable`: missing, invalid or non-finite native result/coordinates.

Finite scalars and readable coordinates survive witness failure. Non-finite scalar
representations and field errors remain JSON-safe diagnostics. `scalar_valid`
requires a finite nonnegative scalar and a valid native result; it does not assert
that Fusion computed the correct geometric minimum. Consistency alone does not
prove that points lie on the surfaces. Face witnesses additionally require native
`BRepFace.isPointOnFace` membership, which accounts for trimmed boundaries and
voids. `surface_membership` records the selected face's second witness check;
`face_membership.a/b` record both face-distance witnesses. `measure` applies the
same checks to each input whose handle kind is `face`; other entity kinds retain
the scalar/separation consistency contract. Native membership is additional
evidence, not an independent nearest-point solver.

For two face inputs, native witness order is checked against both selected faces.
If the original assignment fails and both swapped assignments pass membership,
a valid consistent measurement can be reassigned explicitly. `witness_order` is
`swapped_to_match_entities`; `native_witnesses` retains the original coordinates
and memberships. Accepted checks are in `face_membership`; failed alternate
assignments retain `swapped_face_membership` diagnostics.
Otherwise order stays `as_returned`. Already valid or identical/shared-edge points
are not reordered. Scalar and separation are unchanged.

The numerical test uses `1e-6 mm + 64 * ulp(scale)`, where scale is the maximum of
1 mm, absolute witness coordinates and distance. The absolute floor is a chosen
diagnostic allowance, and the ulp term allows coordinate subtraction/roundoff.
It is independent of caller acceptance thresholds. This is not an Autodesk kernel
precision guarantee; the selected live fixtures below establish only the reported
platform behavior. Widening `max_mm` or `tolerance_mm` cannot hide a contradiction.

Face-distance validation returns `unavailable` on witness failure or false,
missing, raised or nonboolean native face membership. Surface maximum checks
can pass using a separate valid upper-bound witness, as described below.
Membership passes a finite positive `Application.pointTolerance` explicitly in
cm, separately reported in mm. Zero is refused because it is the API's default
sentinel. Caller acceptance thresholds never widen this membership tolerance.
For a proxy face, the root-frame point is transformed using an inverted copy of
`assemblyContext.transform2` and queried on `nativeObject` in component-local
coordinates. Missing native face/context, failed inversion/point transform or
non-finite coordinates fail closed. Evidence records input/local points,
conversion and a bounded occurrence-path diagnostic. No point snapping, tolerance
increase or alternative nearest-point solver is used.
Clearance also returns unavailable unless interference independently establishes a
violation, in which case that violation is preserved. Aggregate `passed` is false
when any check is unavailable. `measure` distance/min_distance return `ok:false`
with `measurement_witness_unavailable` for witness failure while retaining the
scalar, including rejected face membership; invalid native scalars/results and native call exceptions use
`measurement_unavailable`. Angles keep their existing contract. Existing distance
point arrays remain in cm; the added `distance_evidence` uses mm.

## Bounded named probes

```json
{
  "checks": [
    {
      "type": "surface_distance",
      "name": "corner-probe",
      "body": "body:Air|doc=REACQUIRE_CURRENT_SCOPE:REACQUIRE_CURRENT_TOKEN",
      "point_mm": [15, 5, 5],
      "frame": "root",
      "max_mm": 0.05,
      "tolerance_mm": 0,
      "max_faces": 20
    }
  ]
}
```

The handle above is a placeholder: acquire the actual current document-scoped
body handle from inspection. Each field is mandatory. Names must be unique in the
call and contain 1..100 characters. There are at most 50 checks overall;
`max_faces` is an integer in 1..512 and its sum cannot exceed 4096. A selected body
with more faces than its declared cap returns unavailable before measurement.
Bodies need occurrence-scoped handles for child-component geometry. The probe
refuses missing/ambiguous/stale document/entity/occurrence resolution and refuses
component-local geometry when a root context cannot be established.

The implementation measures the caller's root-frame Point3D against every
B-rep face on the selected body, then validates the returned face witness against
that face's trimmed boundaries. Enumerating faces avoids a solid-volume distance
interpretation for interior points. The native API supports these input types,
but observed minima can ignore a face's voids. All faces are attempted within the
declared budget and every failed face is counted. The smallest
returned finite scalar is retained under `minimum_scalar_candidate`, with native
positions and face index. `face_failures` retains representative exceptions and
contradictory or missing witnesses: at most three entries and 2048 escaped-JSON
bytes per check, sharing a 16384-byte diagnostic budget per call. Native exception
and field-issue text is bounded to 256 escaped-JSON bytes with truncation flags.
`face_failure_count`, `face_failures_omitted` and `diagnostics_truncated` preserve
the number and presence of every failure even after detailed diagnostics are
omitted. `faces_attempted` counts native calls, `faces_measured` counts returned
calls (including malformed results), and `faces_valid` counts usable measurements.
Large output can omit input check
echoes, reporting `check_index` and `input_echo_omitted`; every check status and
minimum scalar candidate remains.

A usable scalar/separation-consistent, on-face witness supplies a conservative
upper bound without claiming that it is the closest point. The actual
query-to-witness separation plus the finite known application point tolerance is
stored as `satisfying_witness.witness_upper_bound_mm`. If it is at most
`max_mm + tolerance_mm`, the maximum requirement passes with
`proof: validated_witness_upper_bound`. Other faces can remain unavailable;
their failures and the unvalidated raw minimum candidate remain visible. Unknown
membership tolerance cannot prove this bound. A strict zero maximum can be
unavailable even at a nominal surface point because native membership has a
positive tolerance. `proof_scope` identifies this native tolerance qualification.

Without a satisfying witness, `violation` requires every face measurement to be
usable and the minimum native scalar to exceed the maximum plus acceptance
tolerance. Its `proof: native_face_minima_above_maximum` is explicitly conditional
on the correctness of the native minima, not an independent global proof.
Otherwise the result is unavailable. Faces are never discarded to claim an
established replacement minimum.

For translation comparison, retain the same named root points, units, frame,
thresholds and source provenance; acquire current handles separately in the
original and imported documents and run the same probes on each. Save both full
results and compare names/frames/points before comparing scalars externally. A
point taken from an imported surface and checked against the source is a
directional sample. Repeating in the reverse direction adds different samples.
The tool does not establish where caller-supplied points originated, register
geometry, import/export files, generate samples or compare saved result files.
No transient model body, document activation or mutation is needed.

Passing probes are sampled directional evidence. They do not establish Hausdorff
distance, full surface equivalence, a complete cross-section comparison,
containment, an absence of self-intersections, or downstream solver acceptance.
Bounds, volume, topology and local probes answer different questions.

## Live limitation: native face minima can ignore a void

A separate synthetic fixture on Fusion 2706.1.17 exported/imported a 20 x 30 x
10 mm box, then cut a 1 mm cube notch from the top of the imported body. Bounds
were unchanged and volume changed from 6000 to 5999 mm3. At the center of the
notch opening, the API returned zero distance to the top face, with two identical
witnesses and consistent scalar/separation. `isPointOnFace` returned false: the
point was inside the face's void. The independently known surface distance was
0.5 mm. Some other face results also failed membership; a whole-body measurement
returned scalar 0.5 mm with inconsistent witness coordinates.

The earlier consistency-only implementation falsely passed this control. The
membership gate makes tight maximum checks unavailable when no acceptable bound
is established, while retaining the raw zero candidate and failed membership.
A relaxed maximum can pass if a separately validated witness and its native
membership tolerance establish that upper bound. This does not repair Fusion's native
measurement, discard the invalid face, or establish a replacement minimum from
other faces. A separate opposite-plane live measurement also returned its two
positions in reversed entity order; both swapped memberships passed, supporting
the explicit reassignment above. Native membership also returned false for
proxy-face centers and for tiny off-plane perturbations when using the documented
default tolerance. Querying the native face after inverse occurrence conversion
with an explicit, unchanged application point tolerance succeeded. These findings
motivate the coordinate/tolerance handling above; they are not claims that the
underlying API has been repaired.

## Selected live verification

The revised tools were tested on separate synthetic fixtures in Fusion
2706.1.17. The application point tolerance was 0.00001 mm. The complete recheck,
including document inventories, took about 4.75 seconds; this is a scoped fixture
observation, not a general performance benchmark.

| Check | Observed evidence and result |
| --- | --- |
| Box exterior/interior | 5 mm valid witnesses, conservative upper bounds 5.00001 mm; pass |
| Box edge | Zero separation, upper bound 0.00001 mm; pass under 0.001 mm maximum |
| Nested rotated/translated box | Native-component membership after inverse occurrence conversion; upper bound 5.00001 mm; pass |
| Opposite box faces and direct measure | 10 mm scalar; both native positions reversed; explicit reassignment passed |
| Cylinder exterior | 5 mm scalar with inconsistent witnesses; unavailable |
| Cylinder interior | Raw 10 mm candidate inconsistent; a separate valid cap witness established upper bound 10.00001 mm; requested maximum passed without a minimum claim |
| Stale handle / face budget | Refused with no successful validation |
| Original and STEP-import fixture | Selected on-surface probes passed |
| Notched import, 0.05 mm maximum | Bounds unchanged, volume 6000 to 5999 mm3; raw zero candidate off-face; unavailable |
| Same notch, 0.6 mm maximum | Separate 0.5 mm witness, conservative upper bound 0.50001 mm; pass |

Both owned fixture documents were closed; the original three documents and their
protected state remained intact. The results establish these selected synthetic
requirements only. They do not certify arbitrary imported geometry or establish
a global minimum where native face measurements remain unavailable.

## Offline verification

The tests execute generated scripts against local API doubles, and independently
compute finite rectangle/box projections (including corners, interior points and
a rotated/translated nested occurrence). Pure helper tests use 3-4-5 point
geometry, repeated points matching the field defect, numerical tolerance
boundaries, missing and non-finite data, invalid native results and incorrect
input anchors. Validation tests retain non-nearest face failures, enforce budgets
before native calls, reject stale/foreign/ambiguous handles and retain an
independent interference violation. These are synthetic tests, not a Fusion
kernel or STEP fidelity test.

The initial implementation was developed offline while another agent used the
active session. Subsequent separate synthetic live checks exposed the trimming,
witness-order and membership-frame/tolerance behaviors recorded above; the final
selected live checks passed or conservatively remained unavailable as reported.

The full offline suite is rerun after changes, along with Ruff checks on changed
Python files and `git diff --check`. Receipt integration tests execute the actual
generated script through the real receipt runtime for 50 checks and 4096 native
attempts with long escaped exception text, and for oversized input echoes. They
verify receipt recovery retains every status and finite scalar candidate.
An independent real-receipt stress run used 50 nested probes, each with three long
native failures plus both candidate and satisfying-witness evidence. Its terminal
record was 187774 bytes, below the 256 KiB limit, and retained all 50 outcomes and
failure counts.

## Further live regression plan

For further verification, use a separate disposable design and
record the Fusion build/API version, document scope, units and all full envelopes.

1. Build a 10 x 20 x 30 mm box with named parameters. Probe known exterior corner,
   face and interior points. Independently compute point-to-finite-box-surface
   distances in Python, including zero, 5 mm and sqrt(75) mm cases. Check both
   scalar values and witness separation/anchor consistency.
2. Repeat on a cylinder/sphere with analytically known radial samples and on a
   trimmed planar face near an edge, so an infinite-plane projection cannot pass
   as a finite-face nearest point. Record unsupported or inconsistent native
   behavior as unavailable rather than relaxing tolerances to obtain a pass.
3. Place the box in a nested occurrence with a known 90-degree rotation and
   translation. Reacquire scoped handles; compare the same transformed root-frame
   probes with independently transformed analytic values. Exercise repeated
   instances to detect accidental component-local or wrong-occurrence selection.
4. Measure distinct box faces, touching faces and separated bodies with known
   distances; check `measure`, face-distance and clearance all report the same
   consistency contract. Add known interference and verify a contradictory
   witness cannot erase an independently established violation.
5. Test declared face-cap refusal and foreign/stale handle refusal without native
   measurements. Preserve results for unavailable kernel calls too.
6. Optionally export this fixture to STEP, open the import in a separate document,
   reacquire its handle and rerun the named probe set manually in both directions.
   Check gross metrics separately. This is a fixture smoke test, not proof for the source model
   or a general export guarantee. Never reopen/export the user's production design
   as an implicit part of this test.

## API sources

- [MeasureManager.measureMinimumDistance](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_MeasureManager_measureMinimumDistance.htm)
  supports transient Point3D and BRepFace/BRepBody inputs and returns a scalar and
  two positions in cm.
- [MeasureResults](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_MeasureResults.htm)
  defines the native scalar/position properties and result validity.
- [Autodesk measure sample](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/MeasureSample_Sample.htm)
  demonstrates transient point and B-rep measurement.
- [BRepFace.isPointOnFace](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_BRepFace_isPointOnFace.htm)
  checks face boundaries/voids with a cm tolerance, defaulting to the application
  point tolerance; introduced September 2024.

The installed Autodesk API Python package was inspected offline at the
installation-relative `API/Python/packages/adsk/core.py`,
`MeasureManager.measureMinimumDistance`. Its documented supported
types and cm output agree with the official pages. This inspection establishes
the installed binding signature, not successful live kernel behavior.
The installation-relative `API/Python/packages/adsk/fusion.py` also exposes
`BRepFace.isPointOnFace(point, tolerance=0)` with the documented boundary behavior.
