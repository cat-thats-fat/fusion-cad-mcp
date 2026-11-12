# Field feedback: second-round validation

This record separates completed live observations from offline tests and pending work. The live fixtures use disposable, owned Fusion documents; private designs and source files are not published or used as fixtures. Tests do not establish general behavior for other geometry, Fusion builds or license entitlements.

## Extrusion preflight

On Fusion Personal 2706.1.17, a missing sketch selector returned `sketch_not_found` with `mutation_state: not_started`. Replaying the same operation ID returned the recorded refusal, and selected feature and body counts remained unchanged. Two equally named sketches in separate child components returned an ambiguous selector refusal. A corrected call created a healthy 20 × 30 × 1 mm extrusion with volume 600 mm³. Selected original-document and separate protected-fixture witnesses remained intact; owned fixtures were closed. The initial same-component ambiguity setup failed because Fusion did not retain the intended duplicate names; its uncertain receipt was reconciled after inspection before the successful setup. See [preflight outcomes](preflight-outcomes.md) for scope and offline tests.

One separate SAT attempt returned a restriction exception and created no file in that observed run. It does not establish a universal Personal entitlement rule, a typed-export classifier or the effects of other failed writes.

## Durable custom execution

A live disposable fixture changed a parameter and independently measured the expected height and volume. Replay did not execute the script again, and a changed request using the same ID was refused. A subsequent script recorded a stage for a 12 mm parameter, then raised. The independent readback was 11 mm, illustrating that a stage records the script's assertion rather than final geometry. A coordinated write was blocked until explicit reconciliation with a fresh identity probe and readback; a targeted operation then restored the original 10 mm measurement. Selected original and protected witnesses remained intact and the two owned documents were closed.

A simulated reply loss was injected **after** the native call completed; the terminal receipt was recovered without another dispatch. This observation does not demonstrate a genuinely late completion after a timeout. A raw stdout probe was visible in this session; it does not reproduce or resolve the earlier empty-output report. Stream identities differed between separate native calls, which does not establish whether streams were restored within a call. In-process tests exercise restoration and other failure paths. See [durable execute](durable-execute.md).

## Surface and measurement evidence — pending

Offline tests exercise witness consistency, bounded probes, scope, and failure handling. A live diagnostic on a deliberately altered synthetic surface found that native point-to-face measurements can report a witness outside a trimmed face. A numerical match between the scalar and its endpoints alone is therefore insufficient to establish a valid surface result. The membership safeguard and its live retest are in progress; this record does not claim the original implementation detected the altered surface. Bounds and volume alone do not prove local surface fidelity. Directional point samples do not establish whole-shape equivalence.

## Assembly poses

On Fusion Personal 2706.1.17, two separated samples passed using strictly disjoint bounds. Translation and rotation samples produced independently expected intersection volumes of 500, 1,000 and 2,000 mm³; a nested occurrence sample produced 500 mm³. A touching sample returned unavailable, rather than a clearance claim. Selected primary, original-document and protected-fixture witnesses remained unchanged. Fixture cleanup verification is pending. See [assembly pose checks](assembly-pose-checks.md) for methods and limits.

Offline tests exercise temporary body copies, root-frame transforms, bounded pose inputs and uncertain Boolean outcomes. Supplied poses are discrete samples, not a continuous-motion proof or clearance measurement. A failed or uncertain Boolean is unavailable, not a pass.

## Scope

The work is local Fusion/MCP functionality. It does not require or validate a remote simulation service, paid cloud model, license upgrade, manufacturing fit or automatic path planning. Independently verify the selected design, geometry and current application state. Retain uncertain operation IDs and reconcile only after establishing execution and model state by appropriate evidence.
