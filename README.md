# fusion-cad-mcp

An MCP server that gives Autodesk Fusion a named, typed tool surface: `create_sketch`, `extrude`, `add_hole`, `create_joint`, and `export`.

Fusion ships its own MCP server, but it exposes four broad tools that take raw Python. That works, and it means every call is an opportunity to get the Fusion API wrong. This sits in front of it and turns the common operations into validated tools with structured errors, so an agent can do CAD without carrying a large prose skill in context.

**Status: beta (0.2.x).** Automated tests cover validation, generated scripts, transport, and selected Fusion workflows. Check results and feature health in the active design; not every operation is verified against every Fusion version.

## Requirements

- Autodesk Fusion, running, with a design open
- **Preferences > General > API > Fusion MCP Server** enabled
- Python 3.11+

## Install

**Not a developer?** Start with [AI-SETUP-PROMPT.md](AI-SETUP-PROMPT.md). Paste one prompt into Claude and it walks you through the whole thing, install to first extrude, and verifies it worked.

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
MCP client (Claude, Cursor, Cline, ...)
  -> fusion-cad-mcp            this package, stdio MCP server
    -> httpx
      -> 127.0.0.1:27182/mcp   Fusion's own MCP
        -> adsk.fusion / adsk.core
```

Many modeling tools generate a Python script and run it through Fusion's `execute`. It is a peer of Autodesk's bridge, not a replacement, so anything you can do in the Fusion API you can still do here via the `execute` escape hatch.

## The tools

Availability depends on the Fusion API version. Full reference with signatures, enums, return keys, and error codes is in [`tools.md`](src/fusion_cad_mcp/knowledge/tools.md), or search it in place with the `find_tool` tool.

| Group | Tools |
|---|---|
| Document and state | `doc_state`, `list_open_docs`, `list_projects`, `search_docs`, `open_doc`, `save`, `save_as`, `close`, `undo`, `redo` |
| Parameters | `add_parameters`, `update_parameter`, `list_parameters` |
| Sketch | `create_sketch`, `add_line`, `add_rectangle`, `add_circle`, `add_arc`, `add_ellipse`, `add_spline`, `add_polygon`, `add_geometric_constraint`, `add_dimension`, `assert_profiles`, `probe_sketch_dimensions`, `edit_sketch_dimension` |
| Construction | `create_construction_plane`, `create_construction_axis`, `create_construction_point`, `delete_construction` |
| Features | `extrude`, `revolve`, `combine`, `shell`, `add_hole`, `fillet_edges_by_geometry`, `chamfer_edges_by_geometry`, `mirror_feature`, `pattern_rectangular`, `pattern_circular`, `move_body`, `rebuild_feature`, `sweep`, `create_parametric_enclosure`, `create_mounting_boss`, `create_cutout` |
| Assembly and joints | `bodies_to_components`, `move_component`, `ground_component`, `unground_component`, `create_rigid_group`, `create_contact_set`, `interference_check`, `create_joint`, `set_joint_limits`, `drive_joint`, `create_component`, `insert_component`, `place_component` |
| Handles and measurement | `list_body_entities`, `measure`, `fillet_edges`, `chamfer_edges`, `project_to_sketch`, `find_mesh_using_ray`, `ray_collision_with_mesh` |
| Verify and visualize | `bounding_box`, `volume`, `mass`, `center_of_mass`, `audit_feature_health`, `inspect_assembly`, `inspect_feature`, `query_geometry`, `validate_design`, `screenshot`, `set_view`, `screenshot_compare_with_marker` |
| Import, export, knowledge | `export`, `import_geometry`, `find_tool`, `find_gotcha`, `find_pattern`, `find_api`, `set_reference_metadata`, `get_reference_metadata` |
| Escape hatch | `execute` |

`rib` checks whether Fusion exposes a writable rib API and returns `rib_not_scriptable` if it does not. This refusal was verified on 2705.1.30; actual rib creation was verified on 2706.1.17. `is_flipped` controls the in-plane direction, and unhealthy features return an error with `model_may_have_changed`. `rebuild_feature` returns `safe_rebuild_unavailable` without changing the design.

The capability upgrade has a [verification report](docs/prebenchmark-validation.md), [implementation plan](docs/prebenchmark-plan.md), and [test review](docs/prebenchmark-test-review.md). The earlier modernization has separate [historical evidence](docs/validation.md) and a [test-by-test review ledger](tests/audit/README.md). Historical modeling recipes retain version-specific observations and some retired API examples; use the current tools and Autodesk API documentation when adapting them.

## Assembly and enclosure workflow

Start with `doc_state`, then use `inspect_assembly` and `inspect_feature` to inspect the current design. Assembly inspection includes a paginated occurrence hierarchy by default; selecting an occurrence lists its descendants. Create a component with `create_component`, or use `insert_component` for a shared occurrence or independent copy. Place it with `place_component`, then confirm its root-frame pose and bounds. Use occurrence-qualified names when names repeat.

For an editable enclosure, define named parameters and use `create_parametric_enclosure`; add explicit `create_mounting_boss` and `create_cutout` features where needed. For a path-shaped solid, `sweep` takes a profile sketch and explicitly named path curves. `sweep` requires a unique feature name; inspect the result before retrying an uncertain operation. These primitives do not automatically design a complete enclosure, cable route, lid or manufacturing plan.

Import supplied geometry with `import_geometry`, then use `set_reference_metadata` for user-supplied mounts, connectors and keep-outs. Metadata belongs to the **component definition**, in component-local millimetres; `get_reference_metadata` reports the selected occurrence's transform and root-frame body bounds. It does not infer or certify geometry. Shared or externally referenced definitions refuse metadata edits.

Use `query_geometry` to find current faces, edges or vertices with bounded results; its `near` filter compares a **sample point**, not minimum distance to the geometry. Topology and handles can change after edits; feature-face provenance is incomplete, and generic feature dependencies are unavailable. Use `validate_design` for explicit clearance, interference and root-axis bounds checks. A bounds pass does not prove containment in an irregular cavity; review each `pass`, `violation` or `unavailable` result and `passed`.

A failed write may have partially changed the design. Check `state`, any `rollback` scope and the current model before retrying; `execute` does not guarantee rollback on an exception. Typed tools implement targeted checks and, where reported, limited recovery. Raw `execute` remains available for unsupported operations and carries no general recovery guarantee.

## Every tool returns the same envelope

```python
{
  "ok": bool,
  "message": str,          # stdout from the generated script
  "result": dict | None,   # the payload
  "image": dict | None,    # base64 PNG, for screenshots
  "error": str | None,     # error code when ok is False
  "traceback": str | None
}
```

The same envelope is sent as JSON text and MCP `structuredContent`; MCP `isError` is true when `ok` is false. Screenshot images remain native MCP image blocks.

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
| `find_api`, `set_reference_metadata`, `get_reference_metadata` | Autodesk's Fusion API help |

The first three ship with the package. `find_api` needs a local copy of Autodesk's help, which is their content and is not redistributed, so you build your own once:

```bash
pip install "fusion-cad-mcp[corpus] @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
fusion-cad-mcp corpus build --i-accept-autodesk-terms
```

That crawls at 1 request/second into `~/.fusion-cad/corpus/`, which is where the server looks first. Set `FUSION_CAD_CORPUS_DIR` to keep it elsewhere. Everything else works without it.

## The Claude skill

`SKILL.md` in this repo is the agent-facing companion to the server: which tool
to reach for, the six rules that prevent most failures, and how to fall back to
raw scripts. It reads alongside the same `tools.md`, `patterns.md` and
`gotchas.md` the knowledge tools search, so there is one copy of each.

Install it into a Claude skills directory:

```bash
python install_skill.py
```

That writes `~/.claude/skills/fusion-cad/`. Use `--skills-dir` for another
location, `--name` for another folder name, and `--overlay DIR` to copy your own
markdown over the base, which is how a private variant adds house conventions
without forking the skill.

## Development

```bash
pip install -e ".[dev]"
pytest              # offline tests; no Fusion required
```

The tests cover script generation, envelope parsing, argument validation, and packaging integrity. They deliberately do not need Fusion running, which is also their limit: **offline tests do not prove that Fusion accepts every generated operation.** Several of the bugs found in this project passed every unit test and only surfaced against a live document, so verify behavioral changes in Fusion and check entity counts rather than trusting a success envelope.

One test guards a bug class worth knowing about: `json.dumps(None)` emits `null`, which is not valid Python, and `ast.parse` cannot catch it because `null` is a legal identifier. Generated code must embed Python values with `repr()`.

## License

MIT. See [LICENSE](LICENSE).
