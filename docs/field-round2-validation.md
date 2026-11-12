# Field feedback: second-round validation

This record separates completed live observations from offline tests and remaining limitations. The live fixtures use disposable, owned Fusion documents; private designs and source files are not published or used as fixtures. Tests do not establish general behavior for other geometry, Fusion builds or license entitlements.

## Extrusion preflight

On Fusion Personal 2706.1.17, a missing sketch selector returned `sketch_not_found` with `mutation_state: not_started`. Replaying the same operation ID returned the recorded refusal, and selected feature and body counts remained unchanged. Two equally named sketches in separate child components returned an ambiguous selector refusal. A corrected call created a healthy 20 × 30 × 1 mm extrusion with volume 600 mm³. Selected original-document and separate protected-fixture witnesses remained intact; owned fixtures were closed. The initial same-component ambiguity setup failed because Fusion did not retain the intended duplicate names; its uncertain receipt was reconciled after inspection before the successful setup. See [preflight outcomes](preflight-outcomes.md) for scope and offline tests.

One separate SAT attempt returned a restriction exception and created no file in that observed run. It does not establish a universal Personal entitlement rule, a typed-export classifier or the effects of other failed writes.

## Durable custom execution

A live disposable fixture changed a parameter and independently measured the expected height and volume. Replay did not execute the script again, and a changed request using the same ID was refused. A subsequent script recorded a stage for a 12 mm parameter, then raised. The independent readback was 11 mm, illustrating that a stage records the script's assertion rather than final geometry. A coordinated write was blocked until explicit reconciliation with a fresh identity probe and readback; a targeted operation then restored the original 10 mm measurement. Selected original and protected witnesses remained intact and the two owned documents were closed.

A simulated reply loss was injected **after** the native call completed; the terminal receipt was recovered without another dispatch. This observation does not demonstrate a genuinely late completion after a timeout. A raw stdout probe was visible in this session; it does not reproduce or resolve the earlier empty-output report. Stream identities differed between separate native calls, which does not establish whether streams were restored within a call. In-process tests exercise restoration and other failure paths. See [durable execute](durable-execute.md).

## Surface and measurement evidence

On Fusion Personal 2706.1.17, box outside and inside probes with analytic distances of 5 mm passed with 5.00001 mm witness upper bounds. An edge probe passed with a 0.00001 mm bound; a nested-occurrence outside probe passed at 5.00001 mm. A cylinder outside probe returned a 5 mm scalar with inconsistent witnesses and remained unavailable. A cylinder inside probe had an inconsistent 10 mm raw result, but a separate valid cap witness established a 10.00001 mm upper bound and a pass. A planar face pair measured 10 mm after normalizing reversed native witnesses by face membership; direct `measure` also passed. Stale handle and face-budget checks were refused.

A synthetic 20 × 30 × 10 mm STEP source and imported copy both passed a local surface probe before alteration. A deliberate 1 mm cube notch in the copy left its bounds unchanged and reduced volume from 6,000 to 5,999 mm³, within a 0.1% gross-volume threshold. At the notch opening, a 0.05 mm check was unavailable: the native zero-distance candidate failed trimmed-face membership. A separate 0.6 mm check passed using a valid 0.50001 mm witness upper bound. This demonstrates a bounded local observation, not an automatic STEP translator repair or complete shape comparison.

Native distance and witness consistency alone do not establish membership on a trimmed face. Fusion's point-membership tolerance is included in a satisfying witness upper bound; a pass can use that bound even if other faces are unavailable. A violation requires complete valid face coverage, and an unresolved check is unavailable. Curved-surface native witness inconsistencies remain an observed limitation. Bounds and volume alone do not prove local surface fidelity. Directional point samples do not establish whole-shape equivalence. Selected witnesses for all three pre-existing documents and the separate protected fixture remained intact, and both owned fixtures were closed. See [measurement evidence](measurement-evidence.md) for scope and methods.

## Assembly poses

On Fusion Personal 2706.1.17, two separated samples passed using strictly disjoint bounds. Translation and rotation samples produced independently expected intersection volumes of 500, 1,000 and 2,000 mm³; a nested occurrence sample produced 500 mm³. A touching sample returned unavailable, rather than a clearance claim. Selected primary, original-document and protected-fixture witnesses remained unchanged. Both owned fixtures were closed and the prior active document was restored. See [assembly pose checks](assembly-pose-checks.md) for methods and limits.

Offline tests exercise temporary body copies, root-frame transforms, bounded pose inputs and uncertain Boolean outcomes. Supplied poses are discrete samples, not a continuous-motion proof or clearance measurement. A failed or uncertain Boolean is unavailable, not a pass.

## Observed timings

These single observations include different work and are not benchmarks or cache guarantees: preflight refusals, corrected extrusion and preservation inventory took about 4,447 ms; durable edit and readback took 265 ms; measurement recheck including inventory took 4,752 ms; and seven pose samples including inventory took 7,799 ms. They do not predict performance for other models or machines.

## Final integration checks

The integrated local suite passed 1,335 tests, with one directory-symlink test skipped because that capability was unavailable on the Windows host. Ruff, whitespace checks, the 0.6.0 wheel build and installed-wheel knowledge smoke test passed. A fresh stdio MCP process advertised 104 tools, exposed the durable-execution arguments and sampled-validation capabilities, searched its packaged knowledge and refused an empty validation request without contacting Fusion.

Independent review reproduced receipt overflow in large face-pair batches and mixed pose/surface batches. The shared reducer now budgets the complete payload, retaining statuses, IDs, coordinates, scalar candidates and satisfying bounds while explicitly marking omitted diagnostic detail. Actual-receipt regressions cover 50 face pairs, large surface batches and 128 pose samples combined with 46 surface probes, including long control/Unicode names and native exceptions. An independent mixed reproduction retained all results in a 162,428-byte terminal receipt. These are offline serialization and recovery tests, not kernel verification.

A subsequent live integration run repeated the seven pose cases with the same expected results. A single mixed surface/pose/bounds call returned pass/violation/pass, a 5.00001 mm surface upper bound and 500 mm³ overlap. Selected primary, protected and original-document witnesses remained unchanged; both owned documents were closed. Later diagnostic-size compaction changes did not change the geometry operations; their large-result paths were verified offline.

## Scope

The work is local Fusion/MCP functionality. It does not require or validate a remote simulation service, paid cloud model, license upgrade, manufacturing fit or automatic path planning. Independently verify the selected design, geometry and current application state. Retain uncertain operation IDs and reconcile only after establishing execution and model state by appropriate evidence.
