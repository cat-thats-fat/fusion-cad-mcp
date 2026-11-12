# Field feedback: second implementation increment

This plan responds to the completed November 9 field reports against 0.5.0 and
the November 11 assembly review. Private design files remain unchanged and are
not test fixtures. Four separate changes keep reliability work reviewable.

| PR scope | Implementation | Required evidence |
| --- | --- | --- |
| Extrusion preflight (merged) | Classify only explicit pre-add refusals as not started; retain an optional operation UUID. Document the observed SAT restriction without inferring a universal entitlement rule. | Generated-script and receipt tests; live missing/ambiguous selectors followed by a valid extrusion; real dimensions and preserved documents. |
| Measurement evidence (live fixture complete; merge pending) | Check scalar/witness consistency; add bounded, named point-to-surface probes to existing validation for localized translation checks. | Independent numeric tests, malformed results and stale scopes; live known surfaces and a deliberately altered local region. Gross bounds/volume remain separate evidence. |
| Durable custom Python (merged) | Opt-in existing receipt coordination, bounded output capture, persistent stages and explicit partial-failure records. | Duplicate/conflicting IDs, missing replies, failed scripts, stream restoration and size limits; live successful and deliberately failed staged scripts with independent readback. |
| Assembly pose checks (review and integration pending) | Extend existing validation with caller-supplied rigid poses using temporary bodies. Preserve per-pose failures and kernel-unavailable results. | Independently computed box intersections/poses, strict scope and work limits; live separated/overlapping/touching poses, nested frames and original-document preservation. |

Each PR receives independent review and automated checks before publication.
Live testing has one coordinator and uses disposable synthetic documents. Never
reset Autodesk's output writers, restart Fusion, save unrelated dirty documents,
or use whole-document Undo to make a test pass. Exact tested versions, outcomes,
latencies and limits belong in the accompanying validation records. An unavailable
native result cannot satisfy a requirement, and execution records cannot certify
geometry or admit an unverified cache entry.

The localized STEP discrepancy is evidence for detecting translation loss, not
proof that this compatibility layer can repair Fusion's translator. Point probes
are directional samples, not full shape equivalence. Pose checks are discrete
samples, not continuous motion planning or tool-access certification. Coincident
Boolean failures remain unknown; no perturbation or relaxed tolerance silently
turns them into passes. Physical fit, simulation service acceptance, entitlement
changes and missing engineering measurements remain outside this increment.


The preflight and durable changes have merged. Measurement and pose live fixtures and owned-document cleanup are complete; their changes remain pending integration. The [validation record](field-round2-validation.md) tracks observed results separately from pending work.
