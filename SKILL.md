---
name: fusion-cad
description: "Use for creating, editing, inspecting or verifying CAD in Autodesk Fusion through fusion-cad-mcp or Autodesk's Fusion MCP. Covers native parametric geometry, constraints, assemblies, joints, reference components, measurements, guarded edits, local F3D checkpoints, exports and custom Python scripting. Use for Fusion/Fusion 360, its MCP or Python API, and Fusion workflows involving F3D/F3Z/STEP designs, including functional parts and electronics enclosures."
---

# Fusion CAD

Drive Autodesk Fusion safely and efficiently through an MCP server.

> Includes historical observations and scoped live tests on Fusion Personal 2706.1.17. The repository's workspace validation report describes the measured fixtures and results.

## Which server are you talking to?

This matters before anything else, because the tool surface is completely different.

**`fusion-cad-mcp` (this project).** named, typed tools: `create_sketch`, `extrude`, `add_hole`, `create_joint`. Each one validates arguments, returns a structured envelope, and encodes common API gotchas. Use a typed tool when it fits the task well. **Custom Python through `execute` is always an available option for the agent to choose**, including when a typed tool exists but scripting is better suited to the intended scope, API options, inspection, or efficiency. Mix both approaches as useful; there is no requirement to exhaust typed tools first.

**Autodesk's own Fusion MCP.** Four fat tools that take raw Python or query objects:

- `fusion_mcp_execute` runs a Python script, or does file operations
- `fusion_mcp_read` screenshots, API doc lookup, document and project queries
- `fusion_mcp_update` undo and redo
- `fusion_mcp_electronics_read` read-only Electronics data (Preview API; do not depend on it in distributed work)

`fusion-cad-mcp` sits **in front of** Autodesk's server rather than replacing it: it generates Python and pushes it through `fusion_mcp_execute`. So both are the same Fusion underneath, and every gotcha in `gotchas.md` applies to both.

If only the four-tool server is connected, skip to "Writing raw scripts" at the bottom and lean on `patterns.md`.

## Reference files

Load on demand. This file is the operating manual; those are the depth.

| File | What it is | When to load |
|---|---|---|
| `tools.md` | Tool reference: signatures, exact enums, return keys, error codes | Before calling a tool whose arguments you are not sure of |
| `gotchas.md` | Failure-mode catalog, each with symptom and fix | When something behaves unexpectedly, or before a risky operation |
| `patterns.md` | Reusable Python for `execute` | When custom scripting is useful, including alternatives to typed tools |

Call `get_capabilities` offline first to check package version, corpus availability, import formats and receipt scope. The server can also search these without loading them: `find_tool`, `find_gotcha`, `find_pattern`, and `find_api` for Autodesk's API help. **These work with Fusion closed.**

## Six rules that prevent most failures

1. **Parametric first.** Every dimension that might change goes in `add_parameters` and gets referenced by name. Hardcoded numbers in sketches are forbidden. Re-running should be safe.
2. **Bound the edit scope.** Small operations simplify diagnosis. A cohesive custom script can combine steps when that better serves the task; keep explicit targets, postconditions and a recovery boundary.
3. **Name everything.** Sketches, features, bodies, planes. Name-addressing is how you reach things later, and an unnamed timeline is unreadable when something breaks.
4. **Assert profiles after every sketch.** `assert_profiles(sketch, expected)`. A self-intersecting polygon returns 2 profiles rather than raising, and that is the only signal you get.
5. **Verify with numbers, not pictures.** Use `bounding_box`, `volume` and task-specific geometry checks after body edits. Compare with expected values using explicit units and tolerances; bounds and volume are scoped witnesses, not a complete correctness proof. Screenshots can hide orientation errors or render blank.
6. **Count entities across anything that replicates.** Patterns, mirrors, joins. Position and volume can all be correct while the count is wrong. This is not hypothetical: an unset pattern direction two silently tripled body counts with every other check passing.

## The two things every tool shares

**The envelope.** Every tool returns `{ok, message, result, state, image, error, traceback}`. One parsing contract. MCP provides it in JSON text and `structuredContent`; `isError` follows `ok`. Images use native image blocks.

**`ok: true` does not mean success.** `doc_state` returns `ok: true` with `{"active_design": false}`. `drive_joint` returns `ok: true` when Fusion silently ignored a beyond-limit drive; check `applied`. `move_component` returns `ok: true` when a joint solver overrode the move; check `moved`. `export` reports an error when output is missing or empty. **Read `result`, not just `ok`.**

**Handles.** Bodies, sketches, components, and joints are addressed by **name**. Faces, edges, and vertices have no stable name and are addressed by an opaque handle, `kind:path:token`. `list_body_entities` and `query_geometry` return handles for current faces, edges, and vertices. Handles go stale after edits and return `handle_invalid`, so never cache them across a feature change.

## Standard build

1. **`doc_state`.** Confirm a design is open and check units before touching anything.
2. **`add_parameters`.** Idempotent, so existing names are skipped. Do this before geometry to catch typos early.
3. **`create_sketch` then geometry.** XY is the safe default plane. XZ maps sketch Y to *negative* world Z; YZ inverts sketch X. On any non-XY plane, verify a point's world position before committing coordinates.
4. **Constrain and dimension.** Use parameter-name expressions in `add_dimension`; they propagate when the parameter changes. Anchor rectangles to a specific corner rather than a diagonal midpoint.
5. **`assert_profiles`.** Non-negotiable.
6. **Feature.** `extrude`, `revolve`, `add_hole`, and so on, one per call, each named.
7. **`bounding_box` and `volume`.** Compare against intent.
8. **`audit_feature_health`** after anything that edits sketches, parameters, or features. It catches downstream breakage the API otherwise reports as healthy.
9. **Export** with an absolute path, and check `bytes_written`.

## Inspect, assemble and validate

Use `inspect_assembly`, `inspect_feature` and `query_geometry` before selecting geometry. For large assemblies use `detail="summary"`, explicit `fields` and per-section pagination; a health filter retains unknown sketch/feature health. Assembly inspection includes a paginated occurrence hierarchy by default; a selected occurrence's listing covers its descendants. Specify occurrence-qualified names for repeated components; handles can become stale after edits. `query_geometry` uses root-frame coordinates and its `near` test is distance to a sample point, not minimum distance. Feature-created faces are only currently surviving faces; a generic dependency graph is unavailable.

Read successful inspection data from `result`; `message` is empty to avoid
duplicating that JSON. Select only needed assembly `include` sections and use
small pages: the limit applies per section. Consume either MCP text or structured
content once. Failure diagnostics and raw `execute` stdout remain intact.

Use `create_component`, `insert_component` and `place_component` for local assembly structure and poses. `place_component` takes absolute origin in mm and perpendicular unit axes, optionally relative to another occurrence; read back its transform. The parametric `create_parametric_enclosure`, `create_mounting_boss` and `create_cutout` tools are individual primitives, not an automatic planner. `sweep` uses explicit path-curve references and requires a unique name; inspect any unknown outcome before retrying.

`set_reference_metadata` stores user-provided mount, connector and keep-out data on a component definition in component-local mm. `get_reference_metadata` returns that data, the selected occurrence transform, and root-frame body bounds. Shared or linked definitions refuse edits. `validate_design` checks explicit bounds, interference and minimum clearance; inspect every status and `passed`. Axis-aligned bounds do not establish cavity containment.

## Guarded workspace edits

Use the workspace tools when a local F3D checkpoint, scoped evidence or recoverable operation record would help. V1 supports one existing numeric length user parameter in a self-contained parametric design.

1. Call `workspace_init` on an absolute path. Bind a source document with `workspace_bind` for inspection and checkpoint capture. Use the fresh workspace-owned copy from `workspace_checkpoint`, `workspace_restore` or `workspace_build` for managed edits.
2. Call `workspace_inspect` on the active bound copy and retain the exact prior expression, resolved value in mm and named baseline measurements. Use `root / Body` or `<full occurrence path> / Body` and root-frame units. Include a `body_extent` requirement with axis and an explicit tolerance.
3. Call `workspace_edit` with the handle, a new operation UUID, the unit-bearing expression and the baseline guards. A trusted Python `run(_ctx)` recipe can implement the requested edit when a script fits better. Use the supplied document and design. Independent readback determines verification.
4. Read `execution`, `verification` and the per-check baseline, result and delta. Check protected witnesses and any unknowns. Accepted checkpoint publication requires passing checks after archive reopen. For another attempt, restore a fresh document from the selected checkpoint and capture its baseline again.
5. If execution is uncertain, keep the original UUID and call `workspace_status`. `workspace_recover` uses a clean late edit receipt and fresh bound readback. Conflicting receipt records or failed custom recipes with unresolved effects keep the write barrier. Inspect the affected copy and keep the original operation record.

See `tools.md` for workspace signatures and `patterns.md` for a guarded edit contract and managed Python example.

## Traps that cost the most time

Full catalog in `gotchas.md`. These bite first.

**Assembly scope matters.** Sketch operations can resolve occurrence-qualified names; ambiguous names and edits to shared component definitions are refused. Not every feature tool supports child components. Verify the owning component and use explicit occurrence paths where supported.

**Units depend on the quantity.** Typed geometry tools document their input/output units, commonly mm. Raw Fusion uses cm for length, radians for angles and cm³ for volume; a numeric parameter value uses its quantity's internal units. `Point3D.create` takes cm. Prefer unit-bearing expressions such as `ValueInput.createByString("30 mm")` and use quantity-specific conversions for readback.

**Edge lengths use Fusion curve length.** Axis-parallel filtering applies only to straight edges; `parallel_to="any"` can include curves. Use `query_geometry` for bounded semantic selection.

**`add_hole` targets one body.** Provide a face handle and a point on that planar face, or omit the handle to select the nearest upward planar face containing the XY point. The Z coordinate selects among candidate faces. Child-component bodies are currently refused; inspect `actual_position_mm`.

**Verify parameter propagation on actual bodies.** In Fusion 2706.1.17 a projected boss center moved in its sketch while the B-rep stayed stale until an explicit coincidence constraint was added. Check cylindrical faces, volumes and bounds after edits; the tested negative cut depth also required expression and B-rep readback.

**Failures may leave changes.** Do not assume an exception or timeout rolled a script back. Inspect the design and reported mutation/rollback state before retrying; targeted cleanup has only the scope explicitly reported. Undo can also affect unrelated work, so inspect before using it.

**Joint limits and drives report in internal units** (radians, centimeters), not what you passed in.

**`rib` checks API capability.** If the installed Fusion API lacks the required methods, it returns `rib_not_scriptable`. Model a rib as a thin join-extrude when unavailable. `rebuild_feature` currently refuses with `safe_rebuild_unavailable` rather than deleting a feature.

**Screenshots are the weakest check you have.** Keep width at or below roughly 400 px or the base64 blows the tool-result token limit. Use them to show a human, not to verify geometry.

**Preview API badges are release gates.** If `find_api` reports a page as Preview, do not build distributed work on it without the user explicitly accepting the risk.

## Writing raw scripts

Instrumented JSON-status scripts return through local receipts. Preserve the operation ID and inspect status and the document after an uncertain write. Use workspace recovery for workspace jobs and explicit evidence for reconciliation of other instrumented calls. Establish the actual model result and execution state independently. Raw `execute` uses Autodesk's script output path.

`execute` is a first-class custom scripting path, available for operations missing from the typed surface and whenever the agent judges Python a better fit than a premade tool. Useful cases include coordinated native edits, specialized inspection, and reusable build recipes. Reuse existing helpers where they fit, and consult `find_api` for unfamiliar APIs and `find_pattern` for adaptable examples.

Raw `execute` currently has no automatic target validation, operation receipt, or general rollback guarantee. Supply explicit target checks and measure the resulting geometry for the selected requirements, with units and tolerances. Report baseline, result and differences where relevant. Verification requires measurements and their comparisons. To apply the guarded V1 parameter-edit contract to a trusted recipe, use `workspace_edit` with `custom_script` on a workspace-owned copy.

Direct raw execution also bypasses receipt-backed writer coordination. Do not use that bypass to resume writes or replay a mutation while its outcome is unresolved; inspect status and establish recovery evidence first.

```python
import adsk.core, adsk.fusion

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    # work here
    print(f"bodies={root.bRepBodies.count}")
```

1. The entry point must be exactly `def run(_ctx):`. No other name works. (Autodesk's `fusion_mcp_execute` uses `def run(_context: str):`.)
2. **Preserve failures.** Let exceptions propagate; use `try/finally` for cleanup or re-raise caught exceptions with their traceback. Do not swallow an exception or print success after a failed edit.
3. `print()` is the return channel; stdout becomes `message`.
4. Print the bounding box after any extrude.

Autodesk's capture writers accumulated in one tested session and caused recursive
stdout failure. Receipt-backed calls avoid the final native stdout write. Raw
scripts still use that output path. After an empty reply or timeout, inspect the
actual design before retrying a write. A deliberately silent raw script can
validly return an empty message.

Look the API up before writing against an unfamiliar method. `find_api` costs one call; a failed round trip plus debugging costs far more. Always pass a category when using Autodesk's `apiDocumentation` query, since omitting it returns success with empty data.

## Setup

`fusion-cad-mcp` is at **https://github.com/cat-thats-fat/fusion-cad-mcp**:

```bash
pip install "fusion-cad-mcp @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
```

Then point your MCP client at the `fusion-cad-mcp` command. `CONNECT.md` in that repo has per-client config and troubleshooting.

For Codex, follow the `CONNECT.md` Codex section: install into a dedicated Python
environment, register its absolute interpreter path as a stdio server, and copy
this skill with `install_skill.py --skills-dir` into the Codex skills directory.
The installer includes all three reference files. A saved client registration is
not proof of a live connection; verify `find_tool`, `doc_state` and
`inspect_assembly` before modeling.

The server talks to Autodesk's MCP at `127.0.0.1:27182`, which is enabled in **Preferences > General > API > Fusion MCP Server**. Fusion must be running with a design open.

`find_api` needs a local corpus of Autodesk's API help, which is not redistributed. Build it once:

```
pip install "fusion-cad-mcp[corpus] @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
fusion-cad-mcp corpus build --i-accept-autodesk-terms
```

Everything else works without it.

If `find_api` reports `corpus_not_built` and Autodesk's own MCP is connected, use
its `fusion_mcp_read` with `queryType="apiDocumentation"`, an explicit
`apiCategory` and a narrow `searchPattern`. This does not build the local corpus.

If Fusion is restarted, the adapter re-handshakes on a rejected session. If errors
persist, inspect the error and listener state, then reconnect the MCP client as
needed. Do not diagnose a client fault solely from repeated failures.

For a new editable rectangle profile use `create_parametric_rectangle`; it creates a named sketch and datums, not edits to an existing sketch. Preview rigid-group membership and set a maximum before creating large groups. `open_local_archive` opens an F3D separately; `save_viewport` saves a local PNG. Check all outcomes and use geometry readback for verification.
