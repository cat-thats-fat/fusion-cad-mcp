# Current inspection, validation, assembly and parametric tools

The signatures below are the current typed interface. `limit` is 1..200 and `offset` is nonnegative where present. Selectors accept unique names or occurrence-qualified paths where stated; ambiguous names fail. Read every result and reported state. Native feature and primitive behavior depends on the Fusion version; see the repository validation report for live evidence.

Successful `inspect_assembly`, `inspect_feature` and `query_geometry` replies put
their complete data in `result`; `message` is empty rather than a duplicate JSON
dump. Failure diagnostics and raw `execute` stdout are preserved. For large
assemblies, select needed `include` sections and paginate: `limit` applies to each
section, not the combined reply. MCP text and structured content carry the same
envelope for client compatibility; consume either representation once.

## `inspect_assembly(component=None, include=None, limit=50, offset=0, detail="full", fields=None, health_filter=None)`

Read the root or a uniquely selected occurrence. By default returns paginated descendant occurrences, bodies, sketches, features and joints; `include` can also select `as_built_joints`. For a selected occurrence, its descendants are listed and the selected occurrence itself is excluded. Occurrence entries include parent path, repeated-definition count, grounding, root and immediate-parent transforms, and handles where available. Matrices use Fusion `Matrix3D.asArray` with cm translations. Body bounds are root-frame mm. Native definition sections are not a universal assembly dependency graph. `detail="summary"` returns counts without rows. In full detail, `fields` selects optional groups `handles`, `transforms`, `health`, `bounds` (`None` means all; `[]` means none). Each section reports `returned_count`, `next_offset`, and `truncated`; pagination is per section. `health_filter="non_healthy"` filters only sketches and features, retaining unknown health; other sections are unaffected.

## `inspect_feature(feature, limit=50)`

Read feature health, model parameters, current faces, bodies and linked features. A feature's surviving faces can be incomplete after later edits; generic dependency relationships are unavailable. Inspect again after topology changes.

## `query_geometry(body, kind, filters=None, limit=50, offset=0, expected_count=None)`

Find current face, edge or vertex handles. `filters` accepts `type`, `normal`, `parallel`, `radius`, `length`, `near`, `tolerance`, and `feature` subject to entity-kind restrictions. Coordinates and lengths are root-frame mm. `near` tests the reported sample point, not minimum distance to the whole entity. Direction comparisons use a dimensionless tolerance; radius, length and near use mm. `expected_count` checks current matches; zero feature-face matches cannot prove a feature never produced a face. Re-query after edits.

## `validate_design(checks)`

Accepts 1..50 checks: `{"type":"clearance","body_a":...,"body_b":...,"min_mm":...}`, `{"type":"interference","body_a":...,"body_b":...,"max_overlap_mm3":0}`, or `{"type":"bounds","body":...,"min_mm":[x,y,z],"max_mm":[x,y,z],"tolerance_mm":0}`. Face pairs use `{"type":"face_distance","face_a":handle,"face_b":handle,"min_mm":...,"max_mm":...,"tolerance_mm":...}` with at least one bound and document-scoped current face handles. The result includes minimum distance and witness points in root-frame mm. Same-face pairs are refused; shared-edge pairs can measure zero. Reacquire unique faces after edits or recompute. A face-pair minimum does not establish opposing walls or global wall thickness. Body names, occurrence-qualified names and body handles are supported. Read `result.passed` and each `pass`, `violation` or `unavailable`. Bounds compare root-axis-aligned boxes, not actual containment in an irregular cavity.

## `create_component(name, parent=None)`

Create a uniquely named local component under root or a unique parent. Refuses shared-parent edits, duplicate names, non-parametric/rolled-back timeline and pending transforms. Returns a scoped occurrence and pose.

## `insert_component(source, operation_id, parent=None, independent=False)`

Insert a shared local occurrence or independent definition. `operation_id` guards duplicate insertion within the parent; independent copies use it as their component name. Cloud insertion is outside this tool's scope.

## `place_component(component, origin_mm, x_axis=None, y_axis=None, relative_to=None)`

Set and verify an absolute frame; `origin_mm` is mm and axes are perpendicular unit vectors. The default frame is root; `relative_to` uses the referenced occurrence frame. Grounded and unsupported shared-parent/pending-transform cases are refused.

## `set_reference_metadata(component, metadata)` / `get_reference_metadata(component)`

Set or read user-supplied v1 metadata: required `schema_version: 1`, `reference_id`, `units: "mm"`; optional `mounting_points` (`id`, `position_mm`, `axis`, optional `hole_diameter_mm`), `connectors` (`id`, `origin_mm`, `x_axis`, `y_axis`), `keepouts` (`id`, `min_mm`, `max_mm`) and `source_sha256`. IDs are unique, axes unit length and connector axes perpendicular. Data belongs to the component definition and uses its local frame; read returns the selected occurrence transform and root-frame body bounds. Metadata is not inferred from or certified against imported geometry. Shared and referenced definitions refuse edits; use `import_geometry` separately.

## `create_parametric_enclosure(name, width, depth, height, wall, floor, component="root")`

Creates an editable open rectangular enclosure centered on local XY with floor at local Z=0. Dimensions are Fusion length expressions. It does not create a lid or automatically plan an assembly.

## `create_mounting_boss(name, x, y, z, outer_diameter, inner_diameter, height, component="root", target_body=None)`

Create an editable local +Z boss from length expressions. Zero inner diameter makes a solid boss. It creates a new body unless `target_body` explicitly names a same-component solid to join.

## `create_cutout(name, body, x, y, z, width, height, depth, plane="xy", direction="positive", component="root")`

Create a rectangular cut from one selected body. Values are length expressions; x/y/z anchor the local sketch's lower-left corner. `plane` is `xy`, `xz` or `yz`; `direction` follows or opposes its normal. Fusion's XZ sketch Y maps to negative Z. Checks material removal and sibling identity, volume and bounds; it does not prove unchanged sibling topology.

## `sweep(sketch, path_sketch, path_entities, name, profile_index=0)`

Create one new solid using a profile and explicit `line:N`, `arc:N` or `circle:N` path references from a same-component sketch. No implicit curve chaining; path must satisfy Fusion's sweep requirements. Returns handles, body bounds/volume and verification. `name` is required and must be unique within the component. An uncertain failure must be inspected before retrying.

---

# Fusion MCP tool reference

Reference for typed tools exposed by `fusion-cad-mcp`. Capabilities vary by Fusion version; verify results in the active design.

`rib` checks whether the required Fusion API methods are available. `rebuild_feature` currently refuses safely without modifying the design.

`SKILL.md` is the operating manual and tells you *which* tool to reach for. This file tells you *exactly* how to call it. Load this when you need a signature, an enum, or an error code.

## How to read this file

Tools are grouped the way the server registers them. Within a group, shared behavior is stated once at the top of the group and not repeated per tool.

Three conventions hold everywhere and are not repeated:

- **Units in, units out.** You pass mm. You get mm back. Fusion's internal unit is cm and the server converts at the boundary. The exceptions are called out explicitly per tool (angles, volumes, and a handful of `*_cm` fields).
- **Expression strings are not numbers.** Any parameter documented as an expression (`radius`, `offset`, `expression`, `min_value`, …) is passed verbatim to Fusion's `ValueInput.createByString`. It must carry its own unit (`"8 mm"`, `"45 deg"`) or be a user-parameter name (`"corner_r"`, `"wall_t * 2"`). A bare number inherits the document's default units.
- **Two failure layers.** Client-side validation fails before Fusion is ever called, and returns a generic code (`invalid_input`, `invalid_confirm`, `invalid_count`) with the specific message in `message`. In-Fusion failures return a specific code (`body_not_found`, `handle_invalid`, …). Match on both.

## The envelope

Tools use a common envelope. The MCP response includes the same envelope in JSON text and `structuredContent`; `isError` is true when `ok` is false. Image bytes use native image blocks. This is the single parsing contract:

```python
{
  "ok": bool,              # always present
  "message": str,          # result text, diagnostics, or raw execute stdout
  "result": dict | None,   # the tool's payload
  "state": dict | None,    # execution and receipt metadata when available
  "image": dict | None,    # {data: <base64>, mime_type: "image/png"}
  "error": str | None,     # error code, present only when ok is False
  "traceback": str | None  # Fusion traceback, when one exists
}
```

Keys whose value is `None` are dropped, except `ok`.

Two traps worth internalizing:

- **`ok: True` does not mean what you asked for happened.** `doc_state` returns `ok: True` with `{"active_design": false}` when no design is open. `interference_check` returns `ok: True` with `pair_count: 0`. `assert_profiles` is the tool that turns a mismatch into `ok: False`. Read `result`, not just `ok`.
- **Parse failures are their own class.** If a generated script raises before printing its JSON line, you get `error: "<tool_name>_parse_failed"` and the raw traceback in `message`. That means the operation may have partially applied. It is not a validation error and re-running blindly can double-apply.

## Handles: the two addressing modes

The server uses two ways to name things, and mixing them up is the most common source of confusion.

**Name-addressed.** Bodies, sketches, components, occurrences, joints, features, construction geometry. You pass a plain string: `"main_body"`, `"outer"`, `"Hinge:1"`. Lookup behavior varies by tool. Updated body and sketch resolvers detect ambiguous names and support occurrence-qualified names; older tools may still use first-match lookup.

**Handle-addressed.** Faces, edges, vertices, sketch points. These have no stable name, so they are addressed by an opaque handle:

```
kind:path:token
```

- `kind` is one of: `sketch, profile, body, face, edge, vertex, feature, component, occurrence, param, joint, ucs, plane, axis, point`
- `path` identifies the entity and may include occurrence and document scope metadata used during resolution.
- `token` is Fusion's `entityToken` and is the actual resolver key.

`list_body_entities` and `query_geometry` return current face, edge and vertex handles.

Handles go stale. When an edit deletes or regenerates the entity behind a token, any tool consuming it returns:

```json
{"ok": false, "error": "handle_invalid",
 "detail": {"handle": "<the handle>", "reason": "not_found_in_active_doc"}}
```

Re-run `list_body_entities` to get fresh handles. Never cache handles across a feature edit.

**Handle producers include** `create_sketch`, `add_rectangle`, `list_body_entities`, `query_geometry`, the inspection tools, placement tools and `sweep`. Check each tool result; `create_joint` reports a bare `joint_token`.

---

# Group 1: Document and state

Shared: none of these tools take a component scope. `doc_state` and the counts it returns are **root-component only**.

## `doc_state()`

The orientation tool. Call it first when the document state is uncertain.

**Returns one of two structurally different shapes.** Branch on `result.active_design`, not on `ok`:

No design open (still `ok: True`):
```json
{"active_design": false}
```

Design open, exactly ten keys:

| Key | Type | Meaning |
|---|---|---|
| `active_design` | bool | always `true` in this shape |
| `doc_name` | str \| null | `null` if no document object |
| `is_dirty` | bool \| null | unsaved changes. Gate `close()` on this |
| `workspace` | str \| null | display name, e.g. `"Design"` |
| `units` | str | document default length units, e.g. `"mm"` |
| `bodies_count` | int | root only |
| `sketches_count` | int | root only; includes leaked construction helper sketches |
| `features_count` | int | root only |
| `components_count` | int | `allOccurrences`, which counts *instances*, so one component placed 3 times contributes 3 |
| `parameters_count` | int | user parameters only |

Errors: `doc_state_parse_failed`.

## `list_open_docs()`

Passthrough to Fusion's `document/recent`. Despite the name this is Fusion's **recent** list, not strictly currently-open documents. Result shape is Fusion's, not normalized by the server.

## `list_projects()`

Passthrough. Returns all projects/hubs visible to the user. Result shape is Fusion's.

## `search_docs(query, project=None)`

Fuzzy document-name search. `project` scopes it; omit to search everywhere. No client-side validation, so an empty query reaches Fusion. Result shape is Fusion's.

## `open_doc(name, project=None, file_id=None)`

Fuzzy-matched open by name. No validation. Result shape is Fusion's.

## `save()`

Saves the active document. **Untitled documents are refused by Fusion**: the initial Save As must happen in the Fusion UI. The refusal surfaces as a structured error; surface it to the user rather than retrying.

## `save_as(path)`

Note: on the wire this is still `operation: "save"` with a `path` key, because Fusion's execute schema has no documented save-as. Fusion may accept it or return its own error, passed through verbatim. Fusion may still refuse programmatic Save As on Untitled documents. No client-side path validation.

## `close(confirm="prompt")`

| `confirm` | Effect |
|---|---|
| `"prompt"` (default) | Fusion shows its own dialog if dirty |
| `"save"` | saves, then closes |
| `"discard"` | closes, losing unsaved changes |

Error `invalid_confirm`: `confirm must be one of ['discard', 'prompt', 'save'], got '<x>'`.

**Policy, not enforced by code:** never pick `"save"` or `"discard"` on your own initiative for a dirty document. Both are accepted values; the constraint is on you. Pass `"prompt"` or ask the user. Check `doc_state().is_dirty` first.

## `undo(count=1)` / `redo(count=1)`

Error `invalid_count`: `count must be >= 1`. No upper bound.

**Undo can affect more than the intended feature.** Inspect the timeline and document before using it; do not assume undo or exceptions restore the full previous state.

The loop returns early on first failure and returns only the last envelope on success, so a partial failure at step 3 of 5 is not distinguishable from a step-1 failure. Result shape is Fusion's.

---

# Group 2: Parameters

## `add_parameters(defs)`

Idempotent. Existing names are **skipped, never overwritten**, so use `update_parameter` to change one.

`defs` is a list of objects: `{name, expression, units?, comment?}`. `units` and `comment` default to `""`.

Validation (fail-fast on the first bad def, all returning `error: "invalid_defs"`):

| Condition | Message |
|---|---|
| not a list | `defs must be a list` |
| empty | `defs must be non-empty` |
| element not an object | `defs[i] must be an object` |
| name not a non-empty string | `defs[i].name must be a non-empty string` |
| expression not a non-empty string | `defs[i].expression must be a non-empty string` |
| name is a reserved math identifier | `defs[i].name '<x>' is a reserved math identifier; ...` |
| name starts with a digit | `defs[i].name '<x>' must start with a letter or underscore` |

**Reserved math names are blocked at generation time** because Fusion silently binds expressions referencing them to the math function instead of your parameter. All 27, matched case-sensitively (so `PI` and `Max` slip through and will bite you):

```
sin cos tan asin acos atan atan2 sinh cosh tanh
abs sqrt exp log ln log10 min max round floor ceil pi e
```

Returns: `ok`, `added` (`[{name, expression}]`, the *requested* expression, not read back), `skipped` (`[{name, current_expression}]`), `total_params`.

**Partial-application trap:** the per-parameter `add` is not individually guarded. If Fusion rejects one def mid-loop, earlier adds are already applied, no JSON is printed, and you get `parse_failed` with no record of what landed. Re-run is safe because the tool is idempotent.

## `update_parameter(name, expression, operation_id=None)`

Errors: `invalid_name`, `invalid_expression` (both `must be a non-empty string`), `param_not_found`.

Only `userParameters` is searched. Model and feature parameters are unreachable.

Returns: `ok`, `name`, `before`, `after` (read back), `value` (**raw internal units**: cm for lengths, radians for angles, no conversion).

Note this tool does *not* apply the reserved-math-name guard. A rejected expression raises inside Fusion and surfaces as `parse_failed`, not a descriptive error.

## `list_parameters()`

No arguments. Returns `ok`, `count`, `parameters`, each entry `{name, expression, value, units, comment}`.

`value` is raw internal units (cm / radians), unconverted. The output key is `units` but it comes from Fusion's singular `unit` property. User parameters only.

---

# Group 3: Sketch geometry

Sketch geometry tools resolve occurrence-qualified sketch names and reject ambiguity or edits to shared component definitions. `probe_sketch_dimensions` and `edit_sketch_dimension` have their own `component_name` hint.

Shared errors across the group: `no_active_design`, `sketch_not_found`, `<tool>_parse_failed`.

Points are `[x, y]` in mm. Z is always 0, since these are 2D sketch coordinates.

## Entity references

`add_geometric_constraint` and `add_dimension` address sketch geometry through a small ref grammar, parsed before Fusion is called:

| Ref | Resolves to |
|---|---|
| `origin` | the sketch origin point |
| `line:N` | line N |
| `line:N:start` / `line:N:end` | its endpoints |
| `circle:N` | circle N |
| `circle:N:center` | its center point |
| `arc:N` | arc N |
| `arc:N:start` / `arc:N:end` / `arc:N:center` | its points |
| `point:N` | sketch point N (no sub-entity allowed) |
| `dim:N` | dimension N (no sub-entity allowed) |

**There is no `ellipse:` or `spline:` ref form.** Ellipses and fitted splines cannot be constrained or dimensioned through these tools at all.

Indices are positional into the live collection and shift when geometry is deleted. Ref errors all surface as `invalid_input`: `malformed entity ref`, `<ref>: index must be an int`, `<ref>: unknown sub-entity`, `<ref>: unknown entity kind`.

## `create_sketch(plane, name, component=None)`

`plane` accepts a principal plane or a named plane. `component` selects an occurrence; omitted selects root. Ambiguous selections and shared-definition edits are refused.

Errors include `invalid_input`, `sketch_name_taken`, and `plane_not_found_or_ambiguous`.

Returns: `ok`, `sketch_name`, `plane` (lowercased), `handle`, formatted as `sketch:<name>:<token>`.

**Sketch plane orientation is a live trap.** XY is the safe default. XZ maps sketch Y to *negative* world Z. YZ extrudes along world X and inverts sketch X. For any non-XY plane, drop a test point and read its `worldGeometry` before committing coordinates.

## `add_line(sketch, p1, p2)`

`p1`, `p2` are `[x, y]` mm, exactly 2 elements. Error `invalid_input`: `p1 and p2 must be [x, y] in mm`.

Returns: `ok`, `line_index`.

## `create_parametric_rectangle(name, width, height, x="0 mm", y="0 mm", z="0 mm", plane="xy", centered=False, component=None)`

Create a new named, editable rectangle sketch with construction datums in root or a uniquely selected component. Dimensions and coordinates are Fusion length expressions; sizes must resolve positive. `plane` is `xy`, `xz` or `yz`; the anchor is the lower-left sketch-frame corner unless `centered=True`. It checks for one closed, fully constrained profile and retained bindings, with targeted cleanup on failure. It refuses duplicate names and unsafe component edits. This does not constrain an existing sketch. Inspect output and verify live geometry and coordinate orientation in the installed Fusion build.

## `add_rectangle(sketch, kind, p1, p2, p3=None)`

| `kind` | Points |
|---|---|
| `"center"` | p1 = center, p2 = a corner |
| `"corner"` | p1, p2 = opposite corners |
| `"3pt"` | p1, p2 define one edge; p3 sets the other edge direction |

Errors. `invalid_input`: `p1 and p2 must be [x, y] in mm`, `kind must be one of ['3pt', 'center', 'corner']`, `kind=3pt requires p3 as [x, y] in mm`.

Returns: `ok`, `kind`, `line_indices` (the 4 new indices), `line_handles`.

`line_handles` is a list of `{index, start_point_handle, end_point_handle}` where the handles are `point:<sketch>/line[<i>]/start|end:<token>`. Sketch lines themselves have no `entityToken`; only their endpoints do, which is why you get point handles rather than line handles.

**Silent degradation:** if token capture throws for a line, its entry degrades to just `{index}` with the handles missing, and the call still reports `ok: True`. Check for the keys before using them.

## `add_circle(sketch, kind, center=, radius_mm=, p1=, p2=, p3=)`

| `kind` | Required |
|---|---|
| `"center_radius"` | `center` `[x,y]` mm, `radius_mm` (radius, not diameter) |
| `"3pt"` | `p1`, `p2`, `p3` each `[x,y]` mm |

Errors. `invalid_input`: `kind must be one of ['3pt', 'center_radius']`, `kind=center_radius requires center=[x,y] and radius_mm`, `kind=3pt requires p1, p2, p3 each as [x, y] in mm`.

Returns: `ok`, `circle_index`, `kind`. Unknown keyword arguments are silently ignored, so a typo like `radius=5` produces the "requires radius_mm" error rather than a name error.

## `add_polygon(sketch, sides, center, vertex, inscribed=True)`

`sides` >= 3. `vertex` is a point on the polygon and defines both radius and rotation. `inscribed=True` puts vertices on the radius; `False` circumscribes.

Errors. `invalid_input`: `sides must be >= 3`, `center and vertex must be [x, y] in mm`.

Returns: `ok`, `sides`, `line_indices`.

## `add_ellipse(sketch, center, major_axis_end, minor_axis_end)`

All three are `[x, y]` mm. Error `invalid_input`: `center / major_axis_end / minor_axis_end must each be [x, y] in mm`.

Returns: `ok`, `ellipse_index`. **Cannot be constrained or dimensioned** (no `ellipse:` ref form).

## `add_arc(sketch, kind, ...)`

| `kind` | Required |
|---|---|
| `"3pt"` | `p1`, `p2`, `p3` |
| `"center_start_end"` | `center`, `start`, `end` |
| `"center_start_sweep"` | `center`, `start`, `sweep_radians` |

**`sweep_radians` is radians, passed through unscaled.** Positive is counter-clockwise. Not degrees, not mm.

Errors: `invalid_input` with `kind must be one of ['3pt', 'center_start_end', 'center_start_sweep']` or the matching per-kind requirement message.

Returns: `ok`, `arc_index`, `kind`.

## `add_spline(sketch, points, closed=False)`

`points` is a list of at least 2 `[x, y]` mm points.

Errors. `invalid_input`: `points must be a list of at least 2 [x, y] points in mm`, `points[i] must be [x, y]`.

Returns: `ok`, `spline_index`, `point_count`, `closed`. **Cannot be constrained or dimensioned** (no `spline:` ref form).

## `add_geometric_constraint(sketch, kind, entities)`

| `kind` | Entities | Expects |
|---|---|---|
| `horizontal`, `vertical` | 1 | a line |
| `fix` | 1 | any |
| `parallel`, `perpendicular` | 2 | lines |
| `coincident`, `tangent`, `equal` | 2 | any |
| `concentric` | 2 | circle or arc |
| `midpoint` | 2 | point and line |
| `symmetric` | 3 | two entities about a line |

**The expected shape is not checked.** Passing `parallel` two circle refs generates valid Python that fails inside Fusion. Entity count *is* checked.

Errors. `invalid_input`: `kind must be one of [...]`, `kind=<x> requires N entity refs, got M`, plus any ref-grammar error.

Returns: `ok`, `kind`, `applied_to` (your refs, echoed). No constraint index is returned, so you cannot address the constraint afterwards.

## `add_dimension(sketch, kind, entities, expression, text_pos=None)`

| `kind` | Entities | Expects |
|---|---|---|
| `distance_h`, `distance_v`, `distance` | 2 | point refs |
| `angle` | 2 | line refs |
| `radial`, `diameter` | 1 | circle or arc ref |

`expression` is passed verbatim to the dimension's parameter. **Parameter-name expressions work and propagate** when the parameter changes (`"length / 2"`, `"wall_t * 2"`). Use a literal like `"25 mm"` only when you want a baked value.

`text_pos` is `[x, y]` mm for the dimension text. A wrong-length value is **silently replaced with `[0, 0]`**, not rejected.

Errors. `invalid_input`: `kind must be one of [...]`, `expression must be a non-empty string`, `kind=<x> requires 2 point refs`, etc. Note `kind` and `expression` are validated *before* entity count, so a bad expression masks a bad count.

Returns: `ok`, `kind`, `dim_index`, `expression` (read back, Fusion-normalized), `value_cm`, `value_mm`.

**Angular dimensions report garbage in `value_cm` / `value_mm`.** The internal value is radians and this tool multiplies by 10 regardless. For angles, ignore both fields and read back with `probe_sketch_dimensions`.

## `assert_profiles(sketch, expected)`

The correctness gate. Run it after closing every sketch, before extruding.

`expected` must be a non-negative int. Error `invalid_input`: `expected must be a non-negative int`.

Returns (on both pass and fail): `ok`, `actual`, `expected`, `isFullyConstrained`.

On mismatch the envelope carries `error: "profile_count_mismatch"` with the counts still in `result` for diagnosis. This is deliberately distinct from a script error so you can tell "the assertion failed" from "the script broke".

**Why this matters:** a self-intersecting polygon returns `profiles.count == 2` silently rather than raising. A closed simple profile is 1. Zero means the sketch never closed. Checking this is the only reliable signal.

## `probe_sketch_dimensions(sketch, component_name=None)`

Recursive lookup. Dumps every dimension with its parameter name, expression, value, and the coordinates of the geometry it constrains. Use it to work out which auto-named dim (`d278`) drives which feature.

Errors: `sketch_name_ambiguous` (includes `matches` and a hint to pass `component_name`), `sketch_not_found`.

Returns: `ok`, `sketch_name`, `component`, `plane`, `isFullyConstrained`, `profile_count`, `dimension_count`, `dimensions`.

Each dimension entry: `dim_class`, `param_name`, `expression`, `unit`, `entity_one`, `entity_two`, plus **one of** `value_mm` (length units), `value` + `unit` (other units), or `value_error`.

Entity descriptors carry `type` and whichever of `point_mm`, `start_mm`/`end_mm`, `center_mm`, `radius_mm`, `major_radius_mm`/`minor_radius_mm` apply.

## `edit_sketch_dimension(sketch, dim_name, new_expression, component_name=None)`

Recursive lookup. Changes a dimension's expression by parameter name, then runs `computeAll()` before returning, so the reported values reflect the recomputed model.

Errors: `invalid_input` (`new_expression must be a non-empty string`), `sketch_name_ambiguous`, `sketch_not_found`, `dim_not_found`, `expression_rejected`.

`dim_not_found` helpfully includes `available_dims` so you can correct course without another call. `expression_rejected` includes `tried`, `old_expression`, and `detail`.

Returns: `ok`, `sketch`, `component`, `param_name`, `old_expression`, `new_expression`, `unit`, `isFullyConstrained_after`, plus **one of** `new_value_deg` (angular), `new_value_mm` (length), or `new_value_raw`.

**Always follow this with `audit_feature_health`.** Moving a dimension commonly breaks downstream fillets whose target edges shifted.

---

# Group 4: Construction geometry

Shared: all coordinates are `[x, y, z]` in mm. `offset` and `angle` are expression strings. Everything resolves against the **root component only**.

**Plane and axis name resolution:** the principal names `xy`/`xz`/`yz` and `x`/`y`/`z` are matched case-insensitively and **shadow any user-created entity with the same name**. Anything else is an exact, case-sensitive scan of root construction geometry.

**Helper sketches leak.** Three of these tools create a permanent named sketch on root XY that is never cleaned up: `_cplane_helper_3pt`, `_caxis_helper_2pt`, `_cpoint_helper`. They inflate `doc_state().sketches_count` and repeated calls produce Fusion-disambiguated duplicates.

## `create_construction_plane(kind, name=None, ...)`

| `kind` | Required parameters |
|---|---|
| `"offset"` | `base_plane` + `offset` |
| `"midplane"` | `plane_a` + `plane_b` |
| `"at_angle"` | `axis` + `base_plane` + `angle` |
| `"3_points"` | `p1` + `p2` + `p3`, each `[x,y,z]` mm |

Errors. `invalid_input`: `kind must be one of ['3_points', 'at_angle', 'midplane', 'offset']`, or the per-kind requirement message. In-Fusion: `base_plane_not_found`, `plane_a_not_found`, `plane_b_not_found`, `axis_not_found`.

Note `base_plane_not_found` includes a `name` key from the `offset` branch but **not** from the `at_angle` branch. Do not rely on it.

Returns: `ok`, `kind`, `name`. **`name` is the handle**, so feed it back to other tools or to `delete_construction`.

## `create_construction_axis(kind, name=None, ...)`

| `kind` | Required |
|---|---|
| `"2_points"` | `p1` + `p2`, each `[x,y,z]` mm |
| `"normal_to_face_by_geometry"` | `body` + `face_normal` `[nx,ny,nz]` |

Face selection scans `body.faces` in index order and takes the **first** face whose normal matches componentwise within `1e-3`. On a cylinder or a patterned part several faces can match and you get the lowest index. Pass a unit vector; the comparison is not normalized.

Errors: `invalid_input` with `kind must be one of ['2_points', 'normal_to_face_by_geometry']` or the per-kind message. In-Fusion: `body_not_found`, `face_not_found_by_normal`.

Returns: `ok`, `kind`, `name`.

`face_point` appears in the signature but is **never read**. It is dead; passing it does nothing.

## `create_construction_point(coords, name=None)`

`coords` is `[x, y, z]` mm. Error `invalid_input`: `coords must be [x, y, z] in mm`.

Returns: `ok`, `name`, `coords_mm` (an echo of your input, not a readback).

## `delete_construction(name)`

Exact, case-sensitive. **No principal-name aliasing here**. `"xy"` matches only an entity literally named `xy`, and the principal planes cannot be deleted this way.

Searches planes, axes and points. It deletes only a single unique match; duplicate names are refused as ambiguous.

Errors: `invalid_input` (`name must be a non-empty string`), `construction_not_found`, `construction_ambiguous`, `construction_delete_failed`.

Returns: `ok`, `deleted`, a list of `"<collection>/<name>"` strings.

---

# Group 5: Features

Shared behavior across this group:

- `extrude` and `revolve` resolve sketches with occurrence-aware selection; ambiguous or unsupported shared-definition edits are refused.
- **Features resolve in the root component only** for `mirror_feature`, `pattern_rectangular`, and `pattern_circular`. These look up a *feature* name first and fall back to a *body* name searched tree-wide, so a feature in a sub-component yields `target_not_found` unless a body happens to share the name.
- Body selection behavior varies by tool; updated selectors reject ambiguous names.
- `name` always renames *after* creation, so the returned `feature_name` is your name.
- Plane and axis arguments accept the principal names (`xy`/`xz`/`yz`, `x`/`y`/`z`, lowercased) or the exact case-sensitive name of root construction geometry.

## `extrude(sketch, profile_index=0, operation="new_body", extent_kind="distance", expression=None, direction="positive", is_full_length=True, participants=None, name=None, operation_id=None)`

Retain an optional operation UUID before dispatch. Explicit target/profile/participant
refusals before feature addition report `mutation_state: "not_started"`; a corrected
request uses a new UUID. Failures after an attempted add remain unresolved until
inspection establishes their effects. Reusing the same UUID does not rerun a
completed request; different inputs with that UUID are refused.

| `extent_kind` | Requires | Also uses |
|---|---|---|
| `"distance"` | `expression` | `direction` (`"positive"` / `"negative"`) |
| `"symmetric"` | `expression` | `is_full_length` |
| `"all_positive"` | nothing | nothing |
| `"all_negative"` | nothing | nothing |

`operation`: `new_body`, `join`, `cut`, `intersect`, `new_component`.

**`direction` and `is_full_length` are each consumed by exactly one extent kind** and silently ignored by the others. `all_positive` / `all_negative` ignore `expression` entirely.

`participants` (body names) scopes a cut/join/intersect to specific bodies. Without it, a cut can consume bodies you did not intend.

Errors: `invalid_input` (bad `operation`, bad `extent_kind`, `extent_kind=<x> requires an expression`, bad `direction`), `sketch_not_found`, `profile_index_out_of_range` (carries `got` and `count`), `participant_not_found`.

Returns: `ok`, `feature_name`, `operation`, `extent_kind`, `bodies_added` (names; `[]` for a cut).

**Use `all_negative` rather than a distance when cutting through unknown depth.** A fixed distance stops short when a flange or divider sits below the sketch plane.

## `revolve(sketch, profile_index=0, axis="z", operation="new_body", extent_kind="full", angle=None, is_symmetric=False, participants=None, name=None)`

`extent_kind` is `"full"` or `"angle"`. `"angle"` requires `angle` and honors `is_symmetric`.

**`"full"` hardcodes 360 degrees and ignores both `angle` and `is_symmetric`.**

`axis` must be a construction axis. Sketch lines cannot be used as the axis of revolution here.

Errors: `invalid_input`, `sketch_not_found`, `profile_index_out_of_range`, `axis_not_found`, `participant_not_found`.

Returns: same shape as `extrude`.

## `fillet_edges_by_geometry(body, radius, parallel_to="z", min_length_mm=None, is_tangent_chain=True, name=None)`

Selects edges by geometry rather than by handle, so no `list_body_entities` round trip.

`parallel_to`: `x`, `y`, `z`, or `any`. "Parallel to axis A" means the other two coordinates of the two endpoints match within `1e-6` cm and the A coordinate differs by more than that.

`min_length_mm` uses Fusion's curve length, including circular and arc edges. Axis filtering selects only straight edges; `any` does not restrict curve type.
Errors: `invalid_input` (`parallel_to must be one of ['any', 'x', 'y', 'z']`, `radius must be a non-empty expression string`, `min_length_mm must be >= 0`), `body_not_found`, `no_edges_matched` (carries `parallel_to` and `min_length_mm`).

Returns: `ok`, `feature_name`, `edges_filleted`, `radius`.

## `chamfer_edges_by_geometry(body, distance, parallel_to="z", kind="equal", distance2=None, angle=None, min_length_mm=None, name=None)`

Uses the same edge filter and curve-length measurement as the fillet above.

| `kind` | Requires |
|---|---|
| `"equal"` | `distance` |
| `"two_dist"` | `distance` + `distance2` |
| `"dist_angle"` | `distance` + `angle` |

Errors: `invalid_input` (bad `parallel_to`, bad `kind`, `distance is required`, `kind=two_dist requires distance2`, `kind=dist_angle requires angle`), `body_not_found`, `no_edges_matched` (no diagnostic keys here, unlike the fillet).

Returns: `ok`, `feature_name`, `edges_chamfered`, `kind`. Note it echoes `kind`, not the distances.

**Chamfer has no tangent-chain option**, unlike fillet. It is hardcoded off.

## `add_hole(body, position_mm, diameter, kind="simple", cbore_diameter=None, cbore_depth=None, csink_diameter=None, csink_angle=None, extent_kind="all", depth_expression=None, name=None, face_handle=None)`

Creates a simple, counterbore, or countersink hole on a root-component body. Counterbores require diameter and depth; countersinks require diameter and angle. Distance extent requires `depth_expression`.

With `face_handle`, the supplied 3D point must lie on that body's planar face. Without it, the tool selects the nearest upward planar face containing the XY point, using Z to select between levels. It refuses ambiguity or a missing face and scopes participant bodies to the selected body. Child-component bodies return `hole_component_unsupported`. The result includes `actual_position_mm`.

## `shell(body, thickness, face_normals_to_remove=None, direction="inside", name=None)`

`direction` is `"inside"`, `"outside"`, or `"both"`. There is no direction enum in the Fusion API; direction selects which thickness property gets set.

**`"both"` gives a total wall of 2x `thickness`**, since it sets inside and outside to the same expression rather than splitting one thickness.

`face_normals_to_remove` is a list of `[nx, ny, nz]` unit vectors. Every face whose sampled normal matches within `1e-3` per component is removed, so `[[0,0,1]]` on a stepped part removes *every* upward face, not just the top one. Omit it for a closed hollow shell.

Normals are sampled at one arbitrary point per face, so this matches planar faces reliably and curved faces unpredictably.

Errors: `invalid_input` (`thickness expression required`, `direction must be inside/outside/both`), `body_not_found`, `no_faces_matched_normals` (only when you passed normals).

Returns: `ok`, `feature_name`, `faces_removed`, `thickness`, `direction`.

Verify with volume. For a 20 mm cube: open-top 2 mm inside is 1952 mm3; closed 2 mm outside is 4064 mm3.

## `pattern_rectangular(feature_or_body, x_axis="x", x_count=2, x_distance=None, y_axis=None, y_count=1, y_distance=None, name=None)`

`x_distance` and `y_distance` are **total extents**, not spacing. Four instances over `"60 mm"` sit at 0, 20, 40, 60.

`x_distance` is required when `x_count > 1`; `y_distance` when `y_axis` is given and `y_count > 1`.

Direction two is always configured internally, pinned to one instance when you omit `y_axis`. This is not optional: leaving it unset makes Fusion produce three coincident copies of every instance. See `gotchas.md`.

The internal fallback axis is Y, or Z when `x_axis` is `"y"`. **A named custom construction axis that is geometrically the Y axis will collide with the fallback**, since only principal names are known at build time.

Errors: `invalid_input` (`x_count must be >= 1`, `y_count must be >= 1`, `x_distance required when x_count > 1`, `y_distance required when y_axis given and y_count > 1`), `target_not_found`, `x_axis_not_found`, `y_axis_not_found`.

Returns: `ok`, `feature_name`, `x_count`, `y_count`.

**Count bodies after patterning.** Positions and volumes can be right while the count is wrong.

## `pattern_circular(feature_or_body, axis="z", count=6, total_angle="360 deg", name=None)`

`count` must be **>= 2** (stricter than the rectangular pattern).

Spacing depends on the angle: a full 360 spaces at `total/count` (no duplicate at the seam), a partial angle spaces at `total/(count-1)` so the last instance lands on the angle.

Errors: `invalid_input` (`count must be >= 2`, `total_angle required`), `target_not_found`, `axis_not_found`.

Returns: `ok`, `feature_name`, `count`, `total_angle`.

## `mirror_feature(feature_or_body, plane, name=None)`

`plane` is a principal plane or a root construction plane name. **No build-time validation**, so a bad plane surfaces as a runtime `plane_not_found`.

Feature names win over body names on a tie.

Errors: `target_not_found`, `plane_not_found`.

Returns: `ok`, `feature_name`, `mirrored_kind` (`"feature"` or `"body"`), `source`, `plane`.

## `combine(target_body, tool_bodies, operation="join", keep_tools=False, name=None)`

`operation` is `join`, `cut`, or `intersect`. **`new_body` and `new_component` are not valid here**, unlike `extrude`.

Errors: `invalid_input` (`operation must be one of ['cut', 'intersect', 'join']`, `tool_bodies must be non-empty`), `target_body_not_found`, `tool_body_not_found` (names the offending body).

Returns: `ok`, `feature_name`, `operation`, `keep_tools`.

**Body count is the cheapest correctness check after a join.** Two bodies that should have merged into one and did not is otherwise invisible.

## `move_body(body, translation_mm=None, rotation_axis=None, rotation_angle_deg=None, rotation_origin_mm=None, name=None)`

Needs at least one of `translation_mm` or `rotation_axis`; `rotation_axis` requires `rotation_angle_deg`. `rotation_origin_mm` defaults to the world origin and is ignored without a rotation.

Creates a real `MoveFeature` in the timeline, so it is parametric, not a free transform.

**Translation is applied before rotation** in one composed matrix. If exact placement matters, issue translation and rotation as two separate calls rather than reasoning about the composition.

Errors: `invalid_input`, `body_not_found`.

Returns: `ok`, `feature_name`, `body`.

## `rebuild_feature(feature_name, component_name=None)`

Returns `safe_rebuild_unavailable` without editing the design. Inspect the feature and repair it manually; earlier automatic delete-and-recreate behavior is disabled.

## `rib(sketch, thickness, side="symmetric", extend_profile=True, name=None, target_body=None, is_flipped=False)`

Requires a target body, symmetric side, and an extending profile. `is_flipped` reverses the in-plane extent direction, which depends on curve direction and sketch normal; select the direction toward the target. The tool checks for writable rib APIs and returns `rib_not_scriptable` when they are absent, as observed on Fusion 2705.1.30. On 2706.1.17 a flipped rib on a bracket produced the expected volume and healthy state; the unflipped direction produced a warning and unexpected geometry. Root-component bodies are supported; child bodies are refused. A non-healthy feature returns `rib_feature_unhealthy`, including health details and `model_may_have_changed: true`; inspect or undo the created feature. A thin join-extrude is an alternative.

---

# Group 6: Assembly and joints

Shared: components and joints are addressed by **name**, except `create_joint`, which consumes entity handles. Updated occurrence selectors accept full occurrence paths and reject ambiguous matches. Check each tool for its component scope.

Joint lookup is flat over the root component. **Joints owned by sub-components are not found.**

## `bodies_to_components(mapping)`

`mapping` is `{body_name: component_name}`. World positions are preserved.

**The body keeps its name.** Fusion renames a converted body to `Body1`; this restores your name afterwards, because every other tool here addresses bodies by name. Without that, converting several bodies would leave several bodies all called `Body1`.

**Not atomic.** A `body_not_found` or `create_component_failed` partway through aborts and leaves earlier conversions in place.

Errors: `invalid_input`, `body_not_found`, `create_component_failed`.

Returns: `ok`, `converted` (`[{from_body, to_component}]`), `total_components` (all root occurrences, not just new ones).

## `move_component(name, translation_mm=None, rotation_axis=None, rotation_angle_deg=None, rotation_origin_mm=None)`

Same argument rules as `move_body`. Translation is relative to the occurrence's current transform.

`moved` compares the full before and after transforms, including rotation. The tool returns `move_not_achieved` with the before, expected and observed transforms when the read-back differs from the requested transform.

An occurrence with either grounding flag set returns `occurrence_grounded`. Use `unground_component` explicitly before moving it.

Errors include `invalid_input`, `occurrence_not_found`, `occurrence_ambiguous`, `occurrence_grounded`, and `move_not_achieved`.

Returns `ok`, `component`, `translation_mm`, `rotation_axis`, `rotation_angle_deg`, `before_translation_mm`, `after_translation_mm`, `before_transform`, `after_transform`, and `moved` on success.

## `ground_component(name)` / `unground_component(name)`

`ground_component` pins the occurrence at its current world pose and verifies the pin and transform. Release Ground To Parent explicitly before adding a pin. `unground_component` clears both the pin and the ground-to-parent flag where supported.

Errors include `invalid_input`, `occurrence_not_found`, `occurrence_ambiguous`, `occurrence_grounded_to_parent`, `grounding_change_not_achieved`, and `pin_changed_transform`.

Returns `ok`, `component`, and the read-back values `isGrounded` and `isGroundToParent`.

## `create_rigid_group(component_names, name=None, include_children=True, preview=False, max_occurrences=None, operation_id=None)`

Resolve at least two distinct occurrences. The legacy default includes descendants; use `preview=True` to count selected and expanded membership without creating a group. A positive `max_occurrences` refuses a larger expansion before creation. Preview is a point-in-time estimate, not a lock. A duplicate explicit group name is refused. Inspect after an unknown outcome; do not blindly retry. The result includes selected, expanded and actual membership counts when available. Health or suppression can be unavailable; unavailable is not healthy.

## `inspect_rigid_group(name, limit=50, offset=0)`

Read a uniquely named root rigid group and bounded actual occurrence paths, count and suppression. Health information can be unavailable. Pages report `returned_count`, `next_offset` and `truncated`.

## `create_contact_set(body_names)`

Two or more bodies, for physics and motion.

**Bodies inside components resolve to assembly-context proxies**, which is what the API requires. This is handled internally, but it is why the name restoration in `bodies_to_components` matters: the proxy inherits the body name.

Errors: `invalid_input`, `body_not_found`, `contact_set_add_failed`.

Returns: `ok`, `contact_set_name`, `bodies`.

## `interference_check(entity_names)`

Two or more names, resolved **body first, then occurrence**, so a body wins a name tie. Mixed lists are fine.

Coincident faces are excluded, so touching parts are not reported as interfering.

**Zero interference is a success**, not an error: `pair_count: 0`.

Errors: `invalid_input`, `entity_not_found`.

Returns: `ok`, `checked` (`[{name, kind}]`), `interference_pairs` (`[{entity_one, entity_two, interference_volume_cm3, interference_volume_mm3}]`), `pair_count`.

## `create_joint(geometry_one, geometry_two, motion_type="rigid", axis="z", offset_mm=None, angle_deg=None, name=None)`

Both geometries are **entity handles** of kind `face`, `edge`, `vertex`, or `point`. Get them from `list_body_entities` or the sketch tools.

| Handle kind | Joint origin |
|---|---|
| `face` | face center; planar, falling back to cylindrical and conical |
| `edge` | edge midpoint |
| `vertex` | the vertex |
| `point` | a sketch point |

| `motion_type` | DOF | Uses `axis`? |
|---|---|---|
| `rigid` | 0 | no |
| `revolute` | 1 rotation | yes |
| `slider` | 1 translation | yes |
| `cylindrical` | rotation + translation | yes |
| `ball` | 3 rotation | **no** |

**`axis` is relative to the first geometry's local frame, not world.** For the common "pin coming out of a face" hinge, use `z`, which is the face normal.

**Ball joints ignore `axis` entirely.** Fusion accepts only pitch=Z with yaw=X, so it is hardcoded. The `axis` you pass is still validated and echoed back but has no effect; do not infer the ball joint's orientation from it.

`offset_mm` and `angle_deg` are expression strings despite the names, so include units (`"9 mm"`, `"90 deg"`).

Errors: `invalid_input` (bad `motion_type`, bad `axis`, unsupported handle kind, malformed handle), `handle_invalid`, `joint_geometry_failed` (carries `kind` and `detail`), `joints_add_failed` (carries `detail`, `motion_type`, `axis`; usually over-constrained or an incompatible geometry pair).

Returns: `ok`, `joint_name`, `joint_token`, `motion_type`, `axis`, `offset_mm`, `angle_deg`.

**Carry `joint_name` forward**, not `joint_token`: the limit and drive tools address joints by name. `joint_token` is a bare token, not a full handle.

## `set_joint_limits(joint_name, min_value=None, max_value=None, rest_value=None)`

At least one of the three. All are expression strings with units.

| Motion | Limits used |
|---|---|
| Revolute | rotation |
| Slider | slide |
| Cylindrical | **rotation**; its slide limits are unreachable here |
| Rigid, Ball | none, returns `joint_has_no_limits` |

Omitted values leave that limit untouched. **This tool never disables a limit.**

**Not atomic.** A bad expression returns immediately, so limits applied earlier in min, max, rest order stay applied.

Errors: `invalid_input`, `joint_not_found`, `joint_has_no_motion`, `joint_has_no_limits` (carries `motion_class`), `expression_invalid` (carries `expression`, `expected_units`, `detail`).

Returns: `ok`, `joint_name`, `limits_kind` (`"rotation"` or `"slide"`), `applied` (a dict of `min_enabled` / `min_value_internal` and the max and rest equivalents).

**`applied` is a readback in Fusion internal units: radians for rotation, centimeters for slide.** Setting `"45 deg"` reads back roughly `0.785`. Convert before comparing.

## `drive_joint(joint_name, value)`

`value` is an expression string with units.

Drives the first available attribute in order: `rotationValue`, `slideValue`, `rollValue`, `pitchValue`, `yawValue`, then stops. Consequences: a cylindrical joint always takes rotation, and **a ball joint can only ever be driven on roll**; pitch and yaw are unreachable.

**Check `applied`.** Fusion silently ignores a drive beyond an enabled limit: no exception, the value simply stays put. `ok: true` does not mean the joint moved. When `applied` is `false`, compare `value_internal` (where it actually sits, usually clamped) against `requested_internal`.

Both are in internal units, radians or centimeters, not your input units.

Errors: `invalid_input` (`value expression required`), `joint_not_found`, `joint_has_no_motion`, `no_drivable_motion_attribute` (carries `motion_class`).

Returns: `ok`, `joint_name`, `drove` (the attribute name), `value` (your expression), `value_internal`, `requested_internal`, `applied`.

---

# Group 7: Handles, measurement, and rays

`list_body_entities` is the bridge from names to handles. Everything else here consumes them.

`fillet_edges`, `chamfer_edges`, and `project_to_sketch` create their features on the **root component** regardless of which component owns the edges.

## `list_body_entities(body, kinds=None, face_normal_filter=None, edge_parallel_to=None, min_edge_length_mm=None)`

`kinds` defaults to all three of `face`, `edge`, `vertex`. **Walks sub-components** to find the body.

| Entry | Keys |
|---|---|
| Face | `index`, `handle`, `pointOnFace_mm` |
| Edge | `index`, `handle`, `length_mm` |
| Vertex | `index`, `handle`, `position_mm` |

**The filters compute more than they return.** `face_normal_filter` evaluates each face normal and then discards it; faces carry no normal, no area, and no centroid, only an arbitrary point on the surface. Edges carry no endpoints and no direction. If you need normals, filter *for* them and infer.

`face_normal_filter` matches componentwise within `1e-3` against a unit normal sampled at one arbitrary point, so pass a unit vector, and note it is direction-sensitive: `[0,0,1]` and `[0,0,-1]` differ.

`edge_parallel_to` accepts only straight edges and compares their endpoints within `1e-6` cm. `length_mm` is Fusion curve length, not endpoint chord distance.

`index` is the raw index into the body's collection and survives filtering, so filtered results have gaps. The `*_count` fields are post-filter lengths, and an empty list is ambiguous between "not requested" and "everything filtered out".

Errors: `invalid_input` (`unknown entity kind`, `face_normal_filter must be [nx, ny, nz]`, `edge_parallel_to must be x, y, or z`, `min_edge_length_mm must be >= 0`), `body_not_found`.

Returns: `ok`, `body`, `face_count`, `edge_count`, `vertex_count`, `faces`, `edges`, `vertices`.

## `measure(entity_a, entity_b, kind="distance")`

| `kind` | Returns |
|---|---|
| `"distance"` | `value_cm`, `value_mm`, `point_a_cm`, `point_b_cm` |
| `"min_distance"` | `value_cm`, `value_mm` |
| `"angle"` | `value_radians`, `value_degrees` |

`distance` and `min_distance` call the same Fusion API; they differ only in whether the closest-approach points come back.

**The point arrays are in centimeters**, the only mm-less coordinates in the tool surface.

Occurrence-scoped handles preserve assembly context for measurement; use handles from the intended occurrence.

Errors: `invalid_input` (bad `kind`, malformed handle), `handle_invalid`.

## `fillet_edges(edge_handles, radius, is_tangent_chain=True, name=None)`

Handle-driven fillet, the UI-selection equivalent. `edges_filleted` counts the handles you passed, **not** the edges Fusion actually touched after tangent chaining.

Errors: `invalid_input` (`edge_handles must be non-empty`, `radius expression required`), `handle_invalid`.

Returns: `ok`, `feature_name`, `edges_filleted`, `radius`.

## `chamfer_edges(edge_handles, distance, kind="equal", distance2=None, angle=None, name=None)`

Same `kind` rules as `chamfer_edges_by_geometry`. **No tangent-chain option**, unlike `fillet_edges`.

Errors: `invalid_input` (`edge_handles must be non-empty`, bad `kind`, `kind=two_dist requires distance2`, `kind=dist_angle requires angle`), `handle_invalid`.

Returns: `ok`, `feature_name`, `edges_chamfered`, `kind`.

## `project_to_sketch(sketch, entity_handles)`

Projects linked entities onto a named sketch, for cut-extrudes that reference body geometry.

The sketch resolver accepts occurrence-qualified names and rejects ambiguous or unsupported shared-definition edits.

`entities_projected` counts handles submitted, not those that produced geometry. `curves_added` is the real output count, normally larger since one face yields several curves. **No handles are returned for the new curves.**

Errors include `invalid_input`, `handle_invalid`, `sketch_not_found`, `sketch_ambiguous`, `sketch_proxy_unavailable`, and `shared_component_edit_unsupported`.

Returns: `ok`, `sketch`, `entities_projected`, `curves_added`.

## `find_mesh_using_ray(origin_mm, direction, component_name=None)`

Casts a ray and returns the intersected mesh bodies in hit order, with a scoped `mesh_body` handle for each name. The ray starts at `origin_mm` and uses a unitless `direction`. The search starts at the root component unless `component_name` selects a sub-component.

Errors: `invalid_input` (`origin_mm must be [x, y, z] in mm`, `direction must be [dx, dy, dz]`), `component_not_found`.

Returns `ok`, `meshes_found`, `names`, and `mesh_handles`.

## `ray_collision_with_mesh(mesh_handle, origin_mm=None, direction=None, queries=None, frame="root", tolerance_mm=1e-6, replay=None)`

Legacy `origin_mm` and `direction` cast one ray and return `collisions` and `points_mm`. Alternatively supply 1-32 `queries`, each `{"origin_mm":[x,y,z],"direction":[x,y,z],"start_mm":0,"end_mm":100,"selection":"all"}`; `selection` is `all`, `nearest` or `farthest`, the direction must be finite and nonzero, and `0 <= start_mm < end_mm`. `frame` is `root` or component `local`; positions, travel and tolerance are mm. A bounded query requires a document-scoped mesh handle. Hits are ordered by travel, with coincident returns marked ambiguous; tangent classification is unavailable. Review per-query `hits`, `no_hit`, `ambiguous`, `unavailable` or `truncated` and the coverage counts. No hit means missing sampled coverage, not free space.

Retain a returned query witness and pass it as `replay` with the mesh handle to repeat that same method, frame, interval and tolerance. Source geometry and occurrence pose fingerprints are checked; changed or unavailable source is stale or unavailable, not a favorable replacement sample. Other witness methods are not revalidated by this ray tool. This is bounded sampling, not a global clearance, containment or watertightness proof.

---

# Group 8: Verify, visualize, import and export, knowledge

## `bounding_box(body_name=None)` / `volume(...)` / `mass(...)` / `center_of_mass(...)`

All four take an optional body name (exact, case-sensitive) and default to every body in the design. All walk sub-components, prefixing nested paths with the parent component name.

| Tool | Returns per body |
|---|---|
| `bounding_box` | `bbox_mm` (`min`, `max`), `extent_mm` |
| `volume` | `volume_cm3`, `volume_mm3` |
| `mass` | `mass_kg`, `volume_cm3`, `material`, `density_kg_m3` |
| `center_of_mass` | `center_of_mass_mm` |

Every entry carries `path` and `name`. A per-body failure becomes `{path, error}` rather than aborting the sweep.

Errors: `no_active_design`, `body_not_found` (only when a filter matched nothing), `<tool>_parse_failed`.

**These are scoped numeric witnesses.** Bounding boxes can reveal orientation errors and volume can reveal missed cuts or wrong participant bodies. Compare them with explicit expectations and tolerances, alongside task-specific geometry checks; matching bounds and volume do not prove identical geometry or correct function.

## `audit_feature_health(component_name=None, include_healthy=False)`

Sweeps the feature tree and reports features whose health is not clean.

| `healthState` | Label |
|---|---|
| 0 | `healthy` |
| 1 | `warning` (typically a stale profile reference using cached geometry) |
| 2, 3 | `failed` |

**States 2 and 3 both label as `failed`**, so read the integer to tell them apart.

`summary` counts every feature regardless of `include_healthy`. A feature whose health read failed increments `total` only, so the buckets can sum to less than the total.

Only component features are audited: not sketches, construction geometry, or joints.

Returns: `ok`, `summary` (`total`, `healthy`, `warning`, `failed`), `features`.

**Run this after any tool that edits sketch geometry, parameters, or features**, especially `edit_sketch_dimension`. The API can report healthy geometry while Fusion's UI shows stale cached display.

## `screenshot(direction="iso-top-right", width=None, height=None, transparent=True, anti_aliasing=True)`

Eleven directions: `current`, `front`, `back`, `top`, `bottom`, `left`, `right`, and the four `iso-*` corners. Fusion fits the view internally for named directions.

Returns the PNG as base64 in `envelope.image`.

**Keep `width` at or below roughly 400 px.** Larger images blow the tool-result token limit and the call fails after Fusion has already done the work.

**Screenshots are the weakest verification you have.** They deceive on orientation and sometimes render blank. Use `bounding_box` and `volume` to verify geometry; use screenshots to show a human.

Errors: `invalid_direction`.

## `set_view(direction="iso-top-right", fit=True)`

Orients the camera without capturing. `"current"` orients nothing but still honors `fit`.

Errors: `invalid_direction`, `no_active_viewport`.

## `screenshot_compare_with_marker(before_marker_position, direction="iso-top-right", ...)`

Rolls the timeline back, captures, rolls forward, captures again, from the same camera. An honest visual diff of one change.

`before_marker_position` must be within `[0, timeline.count]`.

**`restore_marker_failed` is the one to watch**: the roll forward failed and the design is left rolled back with features suppressed from view. The user must drag the marker back manually.

Returns: `before_image`, `after_image`, `before_marker`, `after_marker`, `direction`.

## `export(format, path, body=None, refinement="medium", units="mm", component=None, operation_id=None)`

Formats: `stl`, `3mf`, `step`, `iges`, `obj`, `f3d`, `sat`, `smt`.

`body` is supported only by `stl`, `3mf`, and `obj`; other formats reject it. Select `component` separately to export that occurrence scope where supported.

`refinement` (`low`, `medium`, `high`) applies to `stl` and `obj`. `units` applies to `stl` only.

Paths must be **absolute**. Relative paths, `..` segments, and Windows reserved device names are rejected. Directories are created automatically and overwrite is silent.

Errors: `invalid_path` (`path must be absolute`, `path contains '..' parent-dir reference`, `path uses Windows reserved name`), `invalid_input` (bad format, refinement, or units), `body_not_found`.

Returns: `ok`, `path`, `bytes_written`, and `format` for the mesh formats.

**Check `bytes_written`.** Missing or empty output is reported as an error rather than a successful export.

## `import_geometry(format, path, target_component=None, operation_id=None, units=None, dependency_roots=None, appearance_policy="require_resources")`

Import `step`, `iges`, `sat`, `smt`, `f3d` or `obj` into the active design, at root if `target_component` is omitted. A selected occurrence must resolve uniquely and must not edit a shared or referenced definition. For a separate F3D document use `open_local_archive` (Fusion `importToNewDocument`). Paths must be absolute; STL and 3MF import are not supported here. Supply a canonical UUID as `operation_id` before dispatch to preserve it for receipt status and deduplication. Read the full envelope (`ok`, `error`, `message`, `result`, and `state`) on failure; an uncertain native import must not be replayed automatically.

OBJ requires explicit source `units` of `mm`, `cm`, `m`, `in` or `inch`. Preflight follows local OBJ `mtllib` and supported MTL image references only within the resolved OBJ directory or explicit absolute `dependency_roots`; it rejects remote/escaping references, including resolved junction escapes. It accepts UTF-8 OBJ and MTL, bounded to 256 MiB per OBJ, 16 MiB per MTL, 256 MiB per image and 256 dependencies (512 MiB total). Texture options, backslash paths, continued lines, network paths, unsupported `map_` directives and MTL file aliases resolving to another parent directory are refused; quote paths with spaces. Existing meshes in the target require available bounded native witnesses so their identities and geometry can be checked after import. Missing appearance files refuse by default; explicitly choose `appearance_policy="geometry_only"` to proceed with missing resources. Preflight and immediate pre-import rehash narrow but cannot eliminate filesystem races.

OBJ preflight writes a content-addressed provenance manifest under local app data `fusion-cad-mcp/manifests-v1`; `bundle` returns its path, SHA-256 and counts and `state` reports the local artifact write. The manifest records dependency hashes and declared options. The native import reports mesh handles, counts and local/root bounding boxes in mm and the target-to-root matrix in cm. It does not prove UV, material, image embedding or display fidelity: `appearance.status` remains `unknown` even if files exist. Read `cleanup` and the receipt after a partial failure; verified mesh-body cleanup does not prove every native effect was undone. Legacy formats return source hash, direct B-rep and occurrence count deltas and object identities where available.

## `open_local_archive(path, operation_id=None)`

Open an absolute local `.f3d` file as a separate unsaved document; this is distinct from importing geometry into the active design. Check the returned document identity and file metadata. An uncertain outcome requires inspection before any retry. Other formats, including SolidWorks, are not accepted by this tool.

## `save_viewport(path, width=1600, height=1000, direction="current", fit=False, operation_id=None)`

Save the viewport to an absolute `.png` path in an existing directory. Dimensions are 1..8192 pixels. Directions are `current`, `front`, `back`, `top`, `bottom`, `left`, `right`, and `iso-top-right`, `iso-top-left`, `iso-bottom-right`, `iso-bottom-left`. The tool attempts to restore temporary camera changes and reports file metadata; inspect failures and possible external writes. Images are visual evidence, not a geometry check.

## `get_operation_status(operation_id)` / `reconcile_operation(operation_id, expected_document, acknowledgement, evidence, allow_missing_identity=False)`

Trusted JSON-status modeling and inspection scripts return through local receipts and use automatic operation IDs. Selected write tools also accept explicit canonical UUIDs. The journal admits 1,968 normal operation manifests and reserves 32 slots for recovery probes. After a lost response, inspect the operation record and current design. A terminal declaration and a failure marker for the same operation leave the outcome unknown and keep the write barrier.

For an unresolved workspace job, use `workspace_status` and the supported `workspace_recover` path. Other instrumented writes use `reconcile_operation` with acknowledgement, bounded evidence and a fresh matching document probe. `allow_missing_identity=True` allows a weaker recovery path when the original document identity is unavailable. Establish model state and execution termination independently of reconciliation. `insert_component` uses its `operation_id` as a component-level duplicate guard, separate from receipt IDs.

## `get_capabilities()`

Offline summary of package version, local API corpus presence, Autodesk API documentation fallback, supported imports and receipt coverage/capacity. It does not contact Fusion or check licensing, connectivity, model state or installed translator availability.

## `find_api(query, kind=None, namespace=None, limit=5)` / `find_pattern(query, limit=5)` / `find_gotcha(query, limit=5)` / `find_tool(query, limit=5)`

Local search. These and `get_capabilities` work with Fusion closed; they never touch the adapter.

`find_tool` searches this file, `find_pattern` searches `patterns.md`, `find_gotcha` searches `gotchas.md`. All three search markdown shipped with the package and always work.

`find_tool` exists so a client can look up a signature or an error code on demand rather than carrying this whole reference in context, which is the point of the typed tool surface.

`find_api` searches a corpus of Autodesk's API help that is **not** redistributed. Until you build it, it returns `corpus_not_built`:

```
fusion-cad-mcp corpus build --i-accept-autodesk-terms
```

That writes to `~/.fusion-cad/corpus/`, which is where the server looks first. It needs the optional extras: `pip install "fusion-cad-mcp[corpus]"`. Set `FUSION_CAD_CORPUS_DIR` to keep it elsewhere.

Search is substring bag-of-words with hand-tuned weights, not embeddings and not BM25. There is no stemming and no word-boundary matching, so `"arc"` matches inside `"search"`. Zero hits is a success with an empty list, not an error.

**Call `find_api` before writing an `execute` script against an unfamiliar method.** One search is cheaper than a failed round trip, and it reports Preview status, which is a release gate.

Indexes are cached for the process lifetime, so edits to the markdown need a server restart.

Errors: `corpus_not_built`, `patterns_not_found`, `gotchas_not_found`, `empty_query`.

---

# Local workspace V1

Workspace paths are absolute paths on the Fusion host. The workspace keeps a local ledger and hash-addressed F3D archives in `.fusion/`. The `branch` argument selects a workspace checkpoint label. Git tracks source files separately.

## `workspace_init(workspace_path)`

Create local workspace storage, a `design.json` starter and `assets/` directory. Initialize Git metadata if needed and ignore `.fusion/`. Runs offline.

## `workspace_bind(document_name=None)`

Bind the active document or a unique exact document name. Returns `result.target_handle` for this open instance in the current Fusion session. Rebinding the same instance reuses its handle and preserves ownership and pending-operation state. A bound user document can be inspected and captured as a checkpoint. Use an owned copy returned by checkpoint, restore or build for managed writes. Before measuring or editing, activate the bound design and leave it idle in its root context at the end of its parametric timeline.

## `workspace_inspect(target_handle, parameter_name, checks, scope=None, baseline=None)`

Read `result.parameter.expression` and `result.parameter.value_mm` for a numeric length user parameter, plus health and `result.measurements` keyed by check ID. Each check supplies a unique `id`, `metric`, `entities`, `frame: "root"`, `units` and `axis` where listed below. Use the measurements to fill the edit baseline.

For scope-only inspection without a parameter or scalar checks, pass `parameter_name=""` and `checks=[]`. This does not change the checkpoint requirement for a named parameter.

| Metric | `entities` | `units` | `axis` |
|---|---|---|---|
| `body_extent` | One body | `mm` | `x`, `y` or `z` |
| `body_volume` | One solid body | `mm^3` | Omit |
| `occurrence_translation` | One exact occurrence path | `mm` | `x`, `y` or `z` |
| `clearance` | Two distinct solid bodies | `mm` | Omit |
| `interference` | Two distinct solid bodies | `mm^3` | Omit |

The V1 `body_volume` check reads Fusion's `BRepBody.volume`. Its `exact` classification denotes a direct native measurement rather than a heuristic; the API does not specify an accuracy bound, and it is not a claim of mathematically exact volume.

Optional `scope` is a versioned exact inventory, for example:

```json
{"version":1,"path":"root","sections":[{"kind":"body","names":["Plate"],"fields":["bounds_mm","volume_mm3"],"tolerances":{"bounds_mm":0.01,"volume_mm3":0.1}}]}
```

Each section declares unique exact names, selected fields and a tolerance for every numeric field. Kinds/fields are `body`: `bounds_mm`, `volume_mm3`; `mesh`: `witness`; `feature` and `sketch`: `health`; `parameter`: `expression`, `value_mm`, `units`; `occurrence`: `transform_cm`. The mesh `witness` tolerance is `{"bounds_mm":0.01,"transform_cm":0.001}`; it records a native geometry digest, counts, component-local bounds and occurrence transform, with appearance unknown. Omit numeric tolerances for text fields. Sections cover the exact collection in `path` (`root` or a full occurrence path); missing, extra and duplicate names fail comparison. Supply a prior returned evidence snapshot as `baseline` to compare on the same binding. Inspection does not implicitly recompute. These observations are not guards applied atomically by `workspace_edit`.

Scoped `volume_mm3` uses Fusion physical properties at `LowCalculationAccuracy`, reported with the value; Autodesk documents a ±1% error margin at that setting. A tight equality tolerance does not improve that calculation accuracy.

Body selectors are `root / BodyName` or `<full occurrence path> / BodyName`. An occurrence path looks like `Parent:1+Mount:2`. `body_extent` measures the precise root-axis B-rep box. `clearance` combines minimum distance with an interference check. `interference` measures total overlap volume.

## `workspace_edit(workspace_path, contract, custom_script=None)`

`contract` requires `operation_id` (canonical UUID), `target_handle`, `parameter_name`, the requested unit-bearing `expression`, `expected_expression`, `expected_value_mm`, `value_tolerance_mm` and `requirements`. `protected` is optional. The lists together allow up to 32 uniquely identified checks. At least one requirement must be a `body_extent` check.

Each requirement adds `baseline`, `baseline_tolerance`, `expected`, `comparator` (`eq`, `ge` or `le`) and `tolerance` to an inspection check. A protected check uses the same fields except `expected` and `comparator`. Its post-edit value must match its measured pre-edit value within `tolerance`. Thresholds and tolerances use the declared units. The bridge rechecks the prior expression, numeric value, health and geometry guards in the bound copy before the edit.

Supply `custom_script` to implement the edit in trusted Python when scripting fits the task. Define one top-level `run(_ctx)` with one positional argument. The context contains `app`, `document`, `design` and `root`. Apply the requested expression and raise on failure. The recipe runs with Fusion's Python access. Independent expression readback, health and geometry measurements determine the result.

Read `result.execution` and `result.verification` separately. A completed edit can have a failed or incomplete verification. Rows include baseline, result, delta, threshold, units, tolerance, evidence classification and status. Unavailable required measurements prevent verified status. Reuse the original UUID to look up historical evidence. Use a fresh UUID and fresh guards for a new edit.

## `workspace_checkpoint(workspace_path, target_handle, branch, requirements, parameter_name, operation_id=None, expected_previous=None, accepted=False, evidence_scope=None)`

Capture a bound self-contained design into an immutable F3D and open a fresh workspace-owned copy. `requirements` use the edit requirement shape. Checkpoint thresholds apply to both the source and reopened archive. Set `accepted=True` to request accepted promotion. It requires passing checks and matching roundtrip evidence. A retained archive with failed or incomplete verification is eligible for the WIP pointer. Read `checkpoint_kind`, `roundtrip_passed` and `target_handle` in the result.

Optional `evidence_scope` uses the same shape as `workspace_inspect.scope` and compares the source and freshly reopened archive, including selected mesh witnesses. This is checkpoint verification, not an atomic edit guard or general geometry equivalence proof. Read `roundtrip_details.scoped_evidence` and its differences.

Before roundtrip readback, checkpoint and build explicitly recompute the freshly reopened workspace-owned copy as a separate receipt-backed phase. Build also recomputes its newly opened seed copy before baseline inspection. Source inspection is read-only; unavailable or uncertain recompute cannot produce verified evidence. Check phase receipts through `workspace_status`.

Use `expected_previous=None` for a new pointer. To replace one, pass its current SHA-256 digest from `workspace_status`.

## `workspace_restore(workspace_path, branch, kind="accepted", operation_id=None)`

Opens the retained archive as a fresh workspace-owned copy and explicitly recomputes that copy in a separate receipt-backed phase. The restore reports `verification: "not_run"`; inspect the new copy and its health before editing. An uncertain recompute keeps the writer barrier.

Verify the archive digest and open a fresh working document from the selected `accepted` or `wip` pointer. Recovery uses archive import and bypasses Fusion Undo. Inspect the returned handle for current evidence before the next edit.

## `workspace_build(workspace_path, manifest_path="design.json", branch="main", operation_id=None)`

Read a schema 1 JSON manifest with a relative `seed` F3D path, `edit` parameter and expression guards, `requirements`, optional `protected` and optional `script` Python path. Keep the manifest, seed and script inside the workspace. The bridge supplies the handle and operation UUID. The built-in parameter recipe supports cache reuse for matching seed and edit inputs in a compatible executor. Reopen the cached archive and run current verification requirements on each hit. Managed Python recipes use the same checks and produce a fresh build. Known failed or incomplete candidates can retain WIP archives. Accepted publication requires passing roundtrip evidence.

## `workspace_status(workspace_path, operation_id=None)`

Read workspace checkpoint pointers and recent jobs. Pass an operation UUID for its ledger and receipt evidence. Runs offline. The records describe historical results.

## `workspace_recover(workspace_path, operation_id)`

Recover a late guarded edit using its clean terminal receipt and a fresh probe of the same bound document. The probe can use reserved journal capacity. Preserve the original UUID and active target. A failed custom recipe with unresolved effects stays fenced. A replay returns historical evidence without dispatching the original mutation.

---

# `execute(script)`

A first-class custom Python path. Use it for missing capabilities or whenever scripting better fits the task than a premade tool, including coordinated edits and specialized inspection. It remains available even when a typed tool covers part or all of the operation; the agent can choose or combine either approach.

Default raw execution has no automatic target validation, operation receipts or general rollback guarantee. For custom receipt-backed execution pass `durable=True` and a caller-retained canonical UUID `operation_id`. The existing operation status and reconciliation tools apply. Captured stdout/stderr and `receipt_stage(name, data)` checkpoints are historical execution evidence, not verification. Failed or unknown writes retain the barrier. See `docs/durable-execute.md`. Check the intended target and starting state, bound the edit, then measure the actual geometry against selected requirements with explicit units and tolerances. Report the evidence and any unknowns. For a guarded V1 numeric length parameter edit on a workspace-owned copy, `workspace_edit` accepts a trusted Python recipe and evaluates the same declared checks as the built-in edit.

Direct raw execution bypasses receipt-backed writer coordination. Keep unresolved mutations fenced: do not use raw execution to resume writes or replay them before establishing completion and recovery evidence.

```python
import adsk.core, adsk.fusion

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    root = design.rootComponent
    # work here
    print("done")
```

Rules that are not optional:

1. The entry point must be exactly `def run(_ctx):`. No other name works.
2. **Preserve failures.** Let exceptions propagate. Use `try/finally` for cleanup or re-raise caught exceptions with their traceback; do not swallow errors or print success after a failed edit.
3. `print()` is the return channel. Stdout becomes the response `message`.
4. **A failure can leave changes.** Inspect the document and reported outcome before retrying; raw execution has no general rollback guarantee.

Internal units depend on the quantity: lengths use **cm**, angles use **radians**, and volumes use **cm³**. `Point3D.create` takes cm; numeric parameter values use their quantity's internal units. Prefer unit-bearing expressions such as `ValueInput.createByString("30 mm")`. Convert length to mm with ×10 and volume to mm³ with ×1000; do not apply a length conversion to other quantities.

Choose `execute` whenever custom scripting is the better fit. Check `find_api` before using unfamiliar APIs and `find_pattern` for reusable examples. There is no requirement to try or exhaust typed tools first.
