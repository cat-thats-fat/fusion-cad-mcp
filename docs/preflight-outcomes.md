# Extrusion preflight outcomes

An extrusion that explicitly refuses before adding a feature reports `mutation_state: not_started`. This includes no active design, a missing or ambiguous sketch, an unsafe shared sketch owner, an out-of-range profile, and a missing, ambiguous, or wrong-component participant. These declared outcomes release the receipt writer barrier. An optional `operation_id` allows callers to retain the receipt identity; use a new ID for a corrected request. Input validation can also reject a request before dispatch. Exceptions or failures after feature creation is attempted remain uncertain and require inspection and reconciliation. A receipt is evidence about execution, not a substitute for geometric verification.

A field report described a selector missing a generated sketch-name suffix. Inspection showed no extrusion, but the previous failure did not declare that it had not started and required reconciliation. Offline tests execute the generated script and receipt wrapper for this case.

## Verification

- Automated suite: 1,197 passed, one platform-dependent symlink test skipped;
  repository lint and whitespace checks passed. Independent code review found
  no blocking defect in the refusal classification.
- Live Fusion Personal 2706.1.17, November 11: a missing sketch name returned
  `sketch_not_found` / `not_started`, replay of its operation ID repeated the
  recorded refusal, and feature/body counts remained unchanged.
- Two separate child components containing equally named sketches produced a
  live ambiguous-name refusal. A subsequent corrected, distinct operation
  created a healthy extrusion measuring 20 x 30 x 1 mm and 600 mm³.
- All three pre-existing documents and the separate protected fixture retained
  their selected state/geometry witnesses. The final operation sequence took
  about 4,447 ms including the preservation inventory; this is one observation.
- The initial ambiguity fixture incorrectly assumed that equal sketch names
  would persist in one component. Its assertion failed. Fresh readback found
  no surviving added sketches and unchanged source counts; the failed receipt
  was explicitly reconciled before revising the fixture. This was a test-harness
  error, not a successful ambiguity test or an assumed rollback guarantee.

`scripts/live_round2.py` reproduces the synthetic scenario. Run its `prepare`,
`preflight`, and `cleanup` actions with one output directory and exclusive Fusion
automation. A failed run must be inspected and reconciled before continuing;
do not rerun `prepare` over an existing fixture registry.

A separate field report observed a SAT export `RuntimeError` stating that the user was restricted, with no SAT file created in that particular raw-script attempt. This single observation does not establish an entitlement rule for all users or prove that an arbitrary failed export wrote nothing. The typed exporter does not classify this exception as an entitlement error or release an uncertain receipt barrier on that basis. Inspect the receipt and destination before retrying; check the installed account's available export entitlement with Autodesk. A fake-API test captures the current exception behavior and destination preservation, not a live licensing guarantee.
