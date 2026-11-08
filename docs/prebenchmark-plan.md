# Pre-benchmark capability plan

Baseline: `7fed4e61450f25976a7bb836b38e6e739c966fac` (merged modernization PR #1).
Audit date: 2026-11-08. The unchanged baseline passed 584 automated cases;
local Fusion 2706.1.17 answered a read-only probe. No benchmark changes are planned.

## Audit and priorities

The existing 75 typed tools already cover parametric sketching, constraints,
extrude/revolve, patterns, joints and limits, interference, measurements, local
import/export, scoped handles, and raw Python. These should be extended rather
than replaced. Existing regression evidence is in [validation.md](validation.md).

| Priority | Existing capability / concrete gap | Bounded implementation |
| --- | --- | --- |
| P0 | Most body/occurrence selectors reject ambiguity, but older feature, construction and joint paths still select first matches. A dimension edit can ignore an invalid component hint. | Shared strict selectors; occurrence paths; explicit shared-definition edit refusal; correct true edge length and straight-edge predicates. |
| P0 | SSE body deadlines work, but headers/framing and waiting behind another call need stronger bounds. Lost replies are described only in prose. | Bounded transport/queue waits, machine-readable execution uncertainty, no automatic mutation replay, read-only dispatch. |
| P0 | Many `.add()` results are accepted without health or geometry checks; parameter/helper creation may leave partial work. | Preflight, tracked creation, recomputation, scoped postconditions and targeted cleanup with an explicit evidence level. Never default to document Undo. |
| P1 | Measurements exist; hierarchical assembly/feature inspection and semantic topology filters are fragmented. | Three bounded read-only operations: assembly inspection, feature inspection and geometry query, reusing existing handles. |
| P1 | Basic modeling is verbose for common repeated mechanical structures. | A small native parametric primitive set: open rectangular enclosure, mounting boss, and rectangular cutout. Expressions, explicit component scope, measurable postconditions and duplicate-operation refusal. |
| P1 | Occurrence transforms and joints exist, but creating/reusing components and absolute datum placement require Python. | Local component creation/insertion and deterministic coordinate-frame placement; verify full transforms and shared-definition semantics. |
| P2 | Interference and minimum-distance APIs exist without a reusable requirements report. | Bounded checks for clearance/interference and bounding constraints. Bounds do not certify cavity containment or arbitrary wall thickness. |
| P2 | Local STEP import already returns identities/hash/frame. | Portable JSON metadata for reference mounting points, connector frames and keep-outs; reuse import, do not build another catalog. |
| P1/P2 | Sweeps/lofts and feature revision require raw Python; general dependencies are not exposed by a stable generic API. | Select a small native operation only after reliability work and live verification; document precise omissions rather than invent dependency information. |
| All | README/skill contain obsolete edge, export and rollback claims. | Correct descriptions, add compact workflow recipes and exact verification limits. |

Work proceeds through reliability, inspection, composable modeling and placement,
then validation/reference metadata and documentation. Shared interfaces have one
owner; independent work is reviewed before publication. Existing signatures and
opaque handle format remain compatible. Unsupported scopes fail before mutation.

## Comparison projects and licensing

Research inspected source, tests and public issue/PR state at these revisions;
it did not run the competing systems or treat advertised features as verified.

| Project / revision | Useful evidence | Adaptation decision |
| --- | --- | --- |
| [AgentCAD `4b00aa5`](https://github.com/n3r/AgentCAD/tree/4b00aa503776eef9404bea9e489f3a244f423d3c) | Executable enclosure dimensions/clearance specifications, two-component examples, geometry assertions; Apache-2.0. | Adopt independent measurable fit requirements and small local metadata, not its build123d engine or full planner. |
| [CADPilot `c079d2f`](https://github.com/LBurny/cadpilot/tree/c079d2f6b1d17858d4b374610a6174deaacb0d34) | Bounded geometry queries, transform pitfalls, mutation ownership and no-op guards; MIT. | Implement Fusion-specific frames, scoped recovery and semantic query predicates. No FreeCAD runtime. |
| [Fusion360 MCP `8004ccc`](https://github.com/shinobi81/fusion360-mcp-server/tree/8004cccc396df609fc0da2833713aba10ced0399) | Sweep/loft participant handling and coordinate selectors; AGPL-3.0-or-later. Some advertised analysis APIs conflict with current official API documentation. | Independently implement only documented Fusion behavior; do not copy AGPL code or introduce its separate add-in. |
| [Onshape MCP `63f39d1`](https://github.com/gpambrozio/onshape-mcp/tree/63f39d1a6296ccf7a76f0b3fa04222afbe349f82) | Geometry filtering, before/after feature-health validation, applied-but-unverified state; MIT with third-party NOTICE. | Reuse the concepts with original local Fusion code; no cloud API or FeatureScript integration. |

The comparison projects reported no open issues except Onshape's security-reporting
issue; AgentCAD had roadmap PR #36 and Shinobi had joint-axis correction PR #48.
Issue counts are only a snapshot, not a quality measure. No competing source is
being copied; applicable notices would need review before any future code reuse.

## Verification gates

- Execute generated scripts in behavioral tests for ambiguous/missing references,
  invalid expressions, no-op and unhealthy results, partial failure and failed cleanup.
- Exercise real loopback HTTP stalls, cancellation, disconnect, session recovery,
  no mutation replay and reuse after failure.
- Use disposable unsaved Fusion documents only, preserving the prior document.
  Measure B-rep volume/bounds, native feature health, constraints, parameter-edit
  propagation and transforms independently of returned tool claims.
- Test more than one parameter set, repeated and nested occurrences, expected
  failures and retry/recovery behavior. Label exact, sampled and heuristic checks.
- Run the full suite, lint, packaging smoke, independent reviews, and live regression.
  Publish sanitized evidence without native tokens, local paths or account data.

General automatic topology naming, deletion/rebuild of arbitrary dependent
features, cable routing, thermal/electronics simulation, general wall-thickness
analysis, manufacturing certification and an online object catalog are deferred.
