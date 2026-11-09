# fusion-cad-mcp

An MCP server that gives Autodesk Fusion a named, typed tool surface: `create_sketch`, `extrude`, `add_hole`, `create_joint`, and `export`.

Fusion ships its own MCP server with broad tools for Python execution and queries. This package adds validated tools with structured errors for common operations. Custom Python remains a first-class option whenever an agent judges it better suited than a premade tool, including operations outside the typed surface. Agents can combine both approaches.

**Status: 0.5.0 beta, 104 tools.** Scoped evidence comparisons, selected-face distances, local OBJ references and bounded mesh rays extend the existing tools. Live Fusion Personal tests cover parameter propagation, checkpoint/restore, mesh preservation, cache reuse and recovery. [0.5.0 validation and limitations](docs/field-followup-validation.md) · [Workspace validation](docs/workspace-validation.md)

The 0.3.0 reliability report records earlier work on local receipts, rigid-group scope, compact inspection, constrained rectangles and archive and image workflows. [0.3.0 reliability report](docs/reliability-validation.md)

## Requirements

- Autodesk Fusion, running, with a design open
- **Preferences > General > API > Fusion MCP Server** enabled
- Python 3.11+

## Install

**Using Codex?** Follow the setup guide for an isolated Python environment, the `fusion_cad` MCP registration, and the companion skill. The earlier setup verification records 88 discovered tools and successful read-only calls to live Fusion. [Local Codex setup](CONNECT.md#codex) · [Earlier setup verification](docs/codex-setup-validation.md)

**Not a developer?** Start with [AI-SETUP-PROMPT.md](AI-SETUP-PROMPT.md). Paste its guided prompt into your assistant to install, connect, and verify a first model in an empty design.

```bash
pip install "fusion-cad-mcp @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
```

Then point your MCP client at it. For Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "fusion": {
      "command": "fusion-cad-mcp"
    }
  }
}
```

See [CONNECT.md](CONNECT.md) for per-client details and troubleshooting.

## How it works

```
MCP client (Codex, Claude, Cursor, Cline, ...)
  -> fusion-cad-mcp            this package, stdio MCP server
    -> httpx
      -> 127.0.0.1:27182/mcp   Fusion's own MCP
        -> adsk.fusion / adsk.core
```

Many modeling tools generate a Python script and run it through Fusion's `execute`. Agents can choose raw `execute` directly when a coordinated script or specialized API call fits the task. Raw scripts need their own target checks and geometric verification, and currently have no automatic operation receipts or general rollback guarantee.

## The tools

Availability depends on the Fusion API version. Full reference with signatures, enums, return keys, and error codes is in [`tools.md`](src/fusion_cad_mcp/knowledge/tools.md), or search it in place with the `find_tool` tool.

| Group | Tools |
|---|---|
| Document and state | `doc_state`, `list_open_docs`, `list_projects`, `search_docs`, `open_doc`, `save`, `save_as`, `close`, `undo`, `redo` |
| Parameters | `add_parameters`, `update_parameter`, `list_parameters` |
| Sketch | `create_sketch`, `create_parametric_rectangle`, `add_line`, `add_rectangle`, `add_circle`, `add_arc`, `add_ellipse`, `add_spline`, `add_polygon`, `add_geometric_constraint`, `add_dimension`, `assert_profiles`, `probe_sketch_dimensions`, `edit_sketch_dimension` |
| Construction | `create_construction_plane`, `create_construction_axis`, `create_construction_point`, `delete_construction` |
| Features | `extrude`, `revolve`, `combine`, `shell`, `add_hole`, `fillet_edges_by_geometry`, `chamfer_edges_by_geometry`, `mirror_feature`, `pattern_rectangular`, `pattern_circular`, `move_body`, `rebuild_feature`, `sweep`, `create_parametric_enclosure`, `create_mounting_boss`, `create_cutout` |
| Assembly and joints | `bodies_to_components`, `move_component`, `ground_component`, `unground_component`, `create_rigid_group`, `inspect_rigid_group`, `create_contact_set`, `interference_check`, `create_joint`, `set_joint_limits`, `drive_joint`, `create_component`, `insert_component`, `place_component` |
| Handles and measurement | `list_body_entities`, `measure`, `fillet_edges`, `chamfer_edges`, `project_to_sketch`, `find_mesh_using_ray`, `ray_collision_with_mesh` |
| Verify and visualize | `bounding_box`, `volume`, `mass`, `center_of_mass`, `audit_feature_health`, `inspect_assembly`, `inspect_feature`, `query_geometry`, `validate_design`, `screenshot`, `save_viewport`, `set_view`, `screenshot_compare_with_marker` |
| Import, export, knowledge | `export`, `import_geometry`, `open_local_archive`, `get_operation_status`, `reconcile_operation`, `get_capabilities`, `find_tool`, `find_gotcha`, `find_pattern`, `find_api`, `set_reference_metadata`, `get_reference_metadata` |
| Local workspace V1 | `workspace_init`, `workspace_bind`, `workspace_inspect`, `workspace_edit`, `workspace_checkpoint`, `workspace_restore`, `workspace_build`, `workspace_status`, `workspace_recover` |
| Custom Python | `execute` |

`rib` checks whether Fusion exposes a writable rib API and returns `rib_not_scriptable` if it does not. This refusal was verified on 2705.1.30; actual rib creation was verified on 2706.1.17. `is_flipped` controls the in-plane direction, and unhealthy features return an error with `model_may_have_changed`. `rebuild_feature` returns `safe_rebuild_unavailable` without changing the design.

The capability upgrade's disposable Fusion fixtures cover enclosure, boss, cutout, assembly and sweep workflows. Historical modeling recipes retain version-specific observations and some retired API examples. Use the current tools and Autodesk API documentation when adapting them. [Verification report](docs/prebenchmark-validation.md) · [Implementation plan](docs/prebenchmark-plan.md) · [Test review](docs/prebenchmark-test-review.md)

An accumulation of Autodesk's sanitized stdout writers caused recursive output failure in the tested session. Receipt-backed tools now publish their result without a final native stdout call and treat conflicting receipt records as unknown. Upstream writer accumulation can still affect raw script output. Inspect an uncertain write before retrying. [Output investigation](docs/workspace-validation.md#receipt-output-failure)

The earlier modernization has separate evidence and a test-by-test review. [Historical evidence](docs/validation.md) · [Review ledger](tests/audit/README.md)

The initial [field feedback and follow-up](docs/field-feedback.md) records working
inspection flows, corrected import guidance and reduced duplicate inspection
output. That pass did not exercise detailed redesign or export reliability.

## Local workspaces

Workspace V1 runs one guarded edit of an existing numeric length user parameter in a self-contained parametric F3D. It recomputes the model and reports the parameter readback and selected root-frame geometry as baseline, result and delta. Requirements specify the comparison, units and tolerance. Protected checks compare against the measured baseline. Read `execution` and `verification` independently. A completed edit can fail a requirement or have incomplete measurements. A verified result covers the declared checks.

Use `workspace_init` with an absolute host path. Bind an existing document with `workspace_bind` for inspection and checkpoint capture. Create an F3D checkpoint with `workspace_checkpoint` and use the fresh working copy it opens, or open another copy later with `workspace_restore`. Managed edits require a workspace-owned copy. Its target handle stays valid for that open document in the current Fusion session. A restore opens a new document from hash-checked bytes and bypasses Fusion Undo.

The workspace stores immutable F3D blobs, a job ledger, and separate `wip` and `accepted` branch pointers under `.fusion/`. Git can track the manifest and recipe inputs while `.fusion/` stays local. A checkpoint can advance an accepted pointer only after its declared checks pass on the bound design and the reopened archive. The manifest build path can reuse a verified artifact for matching inputs and a compatible executor. It opens and checks the artifact against the current requirements before accepting it.

The optional managed Python recipe runs inside the bound workspace copy and implements the requested expression edit. Use it when native Python fits the operation better, including when a typed call also exists. The recipe runs with Fusion's Python access. Verification comes from the coordinator's independent readback. The build cache covers the built-in parameter recipe.

If a reply is uncertain, retain the operation UUID and check `workspace_status`. Use `workspace_recover` for a clean late edit receipt and fresh readback. Failed custom recipes with an unknown model state keep the write barrier in V1. Treat stored results as historical evidence and inspect the bound document for current geometry.

The CLI uses the same operations. `init` and `status` work with Fusion closed. `inspect`, `edit` and `checkpoint` take JSON request files. The example includes a seed specification and runnable manifest. [Workspace example](examples/workspace/README.md) · [Workspace tool reference](src/fusion_cad_mcp/knowledge/tools.md#local-workspace-v1)

Broader build recipes, mechanical interface contracts and richer change analysis remain proposed work.

## Assembly and enclosure workflow

Start with offline `get_capabilities`, then use `doc_state` and `inspect_assembly` and `inspect_feature` to inspect the current design. Assembly inspection includes a paginated occurrence hierarchy by default; selecting an occurrence lists its descendants. Create a component with `create_component`, or use `insert_component` for a shared occurrence or independent copy. Place it with `place_component`, then confirm its root-frame pose and bounds. Use occurrence-qualified names when names repeat.

For a new constrained, editable rectangle sketch, use `create_parametric_rectangle`; `add_rectangle` adds geometry to an existing sketch without that guarantee. For an editable enclosure, define named parameters and use `create_parametric_enclosure`; add explicit `create_mounting_boss` and `create_cutout` features where needed. For a path-shaped solid, `sweep` takes a profile sketch and explicitly named path curves. `sweep` requires a unique feature name; inspect the result before retrying an uncertain operation. These primitives do not automatically design a complete enclosure, cable route, lid or manufacturing plan.

Import supplied geometry with `import_geometry`, or open a local F3D as a separate document with `open_local_archive`; use `set_reference_metadata` for user-supplied mounts, connectors and keep-outs. Metadata belongs to the **component definition**, in component-local millimetres; `get_reference_metadata` reports the selected occurrence's transform and root-frame body bounds. It does not infer or certify geometry. Shared or externally referenced definitions refuse metadata edits.

Use `query_geometry` to find current faces, edges or vertices with bounded results; its `near` filter compares a **sample point**, not minimum distance to the geometry. Topology and handles can change after edits; feature-face provenance is incomplete, and generic feature dependencies are unavailable. Use `validate_design` for explicit clearance, interference and root-axis bounds checks. A bounds pass does not prove containment in an irregular cavity; review each `pass`, `violation` or `unavailable` result and `passed`.

OBJ references use `import_geometry("obj", path, units="mm", operation_id=uuid)`, with explicit source units and local dependency preflight. Additional roots must be declared; missing appearance resources require explicit `geometry_only`. A content-addressed local manifest records dependencies and is a filesystem side effect. The result reports mesh identities and bounds, not verified texture, UV or display fidelity. Inspect the entire failure envelope, including `error` and `state`, and retain the UUID after an uncertain write.

`workspace_inspect` can capture and compare a versioned exact named `scope`; `workspace_checkpoint` can compare `evidence_scope` after archive reopen, including mesh witnesses. These are observations and checkpoint checks, not atomic guards for `workspace_edit`. `validate_design` supports minimum distance between current document-scoped face handles, returning root-frame witness points in mm; reacquire faces after edits or recompute. `ray_collision_with_mesh` also accepts bounded batch queries in root or mesh-local coordinates and replayable source-bound witnesses. A no-hit or untested area is unknown coverage, not proof of clearance. See the [tool reference](src/fusion_cad_mcp/knowledge/tools.md) for schemas, limits and statuses.

A failed or timed-out write may have partially changed the design. Trusted JSON-status scripts have local receipts. The journal holds 1,968 normal operation manifests and reserves 32 slots for recovery probes. If a reply is uncertain, retain the operation ID and inspect the current document. Use `workspace_status` and `workspace_recover` for workspace jobs. For other instrumented calls, use `get_operation_status` and, when needed, `reconcile_operation` with explicit acknowledgement and evidence. Confirm the actual result and execution state independently of reconciliation. Visual and timeline operations, native operations and raw `execute` use their own result paths.

## Every tool returns the same envelope

```python
{
  "ok": bool,
  "message": str,          # result text, diagnostics, or raw execute stdout
  "result": dict | None,   # the payload
  "image": dict | None,    # base64 PNG, for screenshots
  "error": str | None,     # error code when ok is False
  "traceback": str | None
}
```

The same envelope is sent as JSON text and MCP `structuredContent`; MCP `isError` is true when `ok` is false. Screenshot images remain native MCP image blocks.

Successful `inspect_assembly`, `inspect_feature` and `query_geometry` replies
leave `message` empty and retain the complete payload in `result`, avoiding a
second JSON dump inside the envelope. Consumers should read `result` for these
tools. Error diagnostics and raw `execute` stdout are unchanged.

**`ok: true` does not always mean the thing happened.** Fusion has several operations that decline silently, and the tools surface that rather than hiding it:

- `drive_joint` returns `applied: false` when a drive past a joint limit was ignored
- `move_component` returns `moved: false` when a joint solver overrode the move
- `export` reports an error if it cannot verify a nonempty output file
- `doc_state` returns `active_design: false` when no design is open
- `doc_state` on a direct (non-parametric) design reports `design_type: "direct"`, leaves `parameters_count` / `timeline_count` as `null`, and lists them in `unavailable`; every other field, `features_count` included, is reported normally

Read `result`, not just `ok`.

## The knowledge tools

Four tools answer questions without touching Fusion, so they work with it closed:

| Tool | Searches |
|---|---|
| `find_tool` | this server's tool reference |
| `find_gotcha` | a catalog of Fusion API failure modes |
| `find_pattern` | reusable Python recipes for `execute` |
| `find_api` | Autodesk's Fusion API help |

The first three ship with the package. `find_api` needs a local copy of Autodesk's help, which is their content and is not redistributed, so you build your own once:

```bash
pip install "fusion-cad-mcp[corpus] @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
fusion-cad-mcp corpus build --i-accept-autodesk-terms
```

That crawls at 1 request/second into `~/.fusion-cad/corpus/`, which is where the server looks first. Set `FUSION_CAD_CORPUS_DIR` to keep it elsewhere. Everything else works without it.

If Autodesk's own MCP is also connected, its `fusion_mcp_read` tool can query
`apiDocumentation` without this corpus. Specify `apiCategory` and a narrow
`searchPattern`. This is a separate documentation route, not an automatic
fallback inside `find_api`.

## The agent skill

`SKILL.md` in this repo is the agent-facing companion to the server: which tool
to reach for, the six rules that prevent most failures, and how to choose typed
tools, custom Python, or both. It reads alongside the same `tools.md`, `patterns.md` and
`gotchas.md` the knowledge tools search, so there is one copy of each.

From a checkout, install it into a Claude skills directory:

```bash
python install_skill.py
```

That writes `~/.claude/skills/fusion-cad/`. Use `--skills-dir` for another
location, `--name` for another folder name, and `--overlay DIR` to copy your own
markdown over the base, which is how a private variant adds house conventions
without forking the skill.

For Codex, use `--skills-dir` to select your Codex skills directory; the
[Codex guide](CONNECT.md#codex) includes the Windows PowerShell command.

## Development

```bash
pip install -e ".[dev,corpus]"
pytest              # offline tests; no Fusion required
```

The `corpus` extra installs dependencies needed by the corpus tests; it does not
download Autodesk documentation. Without it, those tests are skipped.

The tests cover script generation, envelope parsing, argument validation, and packaging integrity. They deliberately do not need Fusion running, which is also their limit: **offline tests do not prove that Fusion accepts every generated operation.** Several of the bugs found in this project passed every unit test and only surfaced against a live document, so verify behavioral changes in Fusion and check entity counts rather than trusting a success envelope.

One test guards a bug class worth knowing about: `json.dumps(None)` emits `null`, which is not valid Python, and `ast.parse` cannot catch it because `null` is a legal identifier. Generated code must embed Python values with `repr()`.

## License

MIT. See [LICENSE](LICENSE).
