"""FastMCP server entry point. Exposes wrapped tools over stdio.

Tool registration is split per group for readability. Each tool is a thin
shim: it calls the group module's run() function with the shared adapter,
then returns the envelope as MCP text and structured content with an error flag.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

from . import workspace
from .adapter import DEFAULT_URL, FusionAdapter
from .envelope import Envelope
from .receipts import get_status
from .tools import assembly as asm
from .tools import construction as cons
from .tools import doc_state as doc_state_tool
from .tools import document as docs
from .tools import execute as execute_tool
from .tools import features as feat
from .tools import handle_tools as ht
from .tools import inspection as inspect_tools
from .tools import io as io_tools
from .tools import knowledge as kn
from .tools import local_files, operations, parametric_sketch
from .tools import native_features as native_tools
from .tools import parameters as params
from .tools import placement as placement_tools
from .tools import primitives as primitive_tools
from .tools import references as reference_tools
from .tools import sketch as sk
from .tools import validation as validation_tools
from .tools import verify as verify_tools
from .tools import visualize as viz_tools

mcp = FastMCP("fusion-cad-mcp")

_adapter: FusionAdapter | None = None


def _get_adapter() -> FusionAdapter:
    global _adapter
    if _adapter is None:
        url = os.environ.get("FUSION_MCP_URL", DEFAULT_URL)
        _adapter = FusionAdapter(url=url)
    return _adapter


def _image_result(env: Envelope, *, compare: bool = False) -> CallToolResult:
    """Keep image bytes in native MCP blocks, with compact structured metadata."""
    metadata = env.to_dict()
    images = metadata.pop("images", None) or []
    first = metadata.pop("image", None)
    if not images and first:
        images.append(first)
    if compare and isinstance(metadata.get("result"), dict):
        result = metadata["result"]
        for key in ("before_image", "after_image"):
            value = result.pop(key, None)
            if value:
                result[key] = {"content_index": len(images) + 1}
                images.append(value)
    content = [TextContent(type="text", text=json.dumps(metadata))]
    content.extend(
        ImageContent(type="image", data=i["data"], mimeType=i["mime_type"]) for i in images
    )
    return CallToolResult(content=content, structuredContent=metadata, isError=not env.ok)


def _tool_result(env: Envelope) -> CallToolResult:
    """Publish the same envelope as JSON text and structured MCP content."""
    if env.image or env.images:
        return _image_result(env)
    metadata = env.to_dict()
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(metadata))],
                          structuredContent=metadata, isError=not env.ok)


@mcp.tool()
def get_capabilities() -> CallToolResult:
    """Offline capability and setup report: corpus availability, supported local imports,
    receipt coverage and limits. No Fusion call or network access.
    """
    return _tool_result(kn.get_capabilities())


# ---------- Group 1: Document & State ----------


@mcp.tool()
def doc_state() -> CallToolResult:
    """Summary of the active Fusion document: design_type (parametric/direct), bodies/sketches/features count, units, dirty flag, components, parameters, timeline. A direct design returns parameters_count and timeline_count as None, listed in result.unavailable."""
    return _tool_result(doc_state_tool.run(_get_adapter()))


@mcp.tool()
def list_open_docs() -> CallToolResult:
    """List documents currently open in Fusion."""
    return _tool_result(docs.list_open_docs(_get_adapter()))


@mcp.tool()
def list_projects() -> CallToolResult:
    """List all projects (hubs / folders) the user can see."""
    return _tool_result(docs.list_projects(_get_adapter()))


@mcp.tool()
def search_docs(query: str, project: str | None = None) -> CallToolResult:
    """Fuzzy-search documents by name. Optional project scope."""
    return _tool_result(docs.search_docs(_get_adapter(), query, project))


@mcp.tool()
def open_doc(name: str, project: str | None = None, file_id: str | None = None) -> CallToolResult:
    """Open a unique exact document name, or an explicit file_id from search_docs."""
    return _tool_result(docs.open_doc(_get_adapter(), name, project, file_id))


@mcp.tool()
def save() -> CallToolResult:
    """Save the active document. Initial cloud save may require Fusion's Save dialog."""
    return _tool_result(docs.save(_get_adapter()))


@mcp.tool()
def save_as(path: str) -> CallToolResult:
    """Return save_as_unsupported; use export for local files or Fusion's Save dialog."""
    return _tool_result(docs.save_as(_get_adapter(), path))


@mcp.tool()
def close(confirm: str = "prompt") -> CallToolResult:
    """Close the active document. confirm must be 'save', 'discard', or 'prompt'.
    Never auto-pick save vs discard for dirty docs; surface the choice to the user.
    """
    return _tool_result(docs.close(_get_adapter(), confirm))


@mcp.tool()
def undo(count: int = 1) -> CallToolResult:
    """Undo the last `count` actions in the active document.
    Undo may affect unrelated work. Inspect the timeline and use only when whole-document
    recovery is explicitly intended; do not assume it restores every previous state.
    """
    return _tool_result(docs.undo(_get_adapter(), count))


@mcp.tool()
def redo(count: int = 1) -> CallToolResult:
    """Redo the last `count` undone actions."""
    return _tool_result(docs.redo(_get_adapter(), count))


# ---------- Group 2: Sketch (parameters subset) ----------


@mcp.tool()
def add_parameters(defs: list[dict]) -> CallToolResult:
    """Add user parameters idempotently. Existing names are skipped, not overwritten.

    Each def: {name, expression, units?, comment?}.
    Reserved math names (sin/cos/pi/e/sqrt/etc) are rejected at validation time
    because Fusion will silently bind expressions to the math function.
    """
    return _tool_result(params.add_parameters(_get_adapter(), defs))


@mcp.tool()
def update_parameter(name: str, expression: str, operation_id: str | None = None) -> CallToolResult:
    """Update one user parameter's expression. Returns before/after values."""
    return _tool_result(params.update_parameter(_get_adapter(), name, expression, operation_id))


@mcp.tool()
def list_parameters() -> CallToolResult:
    """List all user parameters with resolved values, expressions, units, comments."""
    return _tool_result(params.list_parameters(_get_adapter()))


# ---------- Group 2: Sketch geometry ----------


@mcp.tool()
def create_sketch(plane: str, name: str, component: str | None = None) -> CallToolResult:
    """Create a sketch on xy/xz/yz, a named plane, or a scoped planar face handle.
    component selects a unique component or occurrence path. Coordinates are sketch-local.
    Shared component definitions are refused to prevent editing every instance.
    """
    return _tool_result(sk.create_sketch(_get_adapter(), plane, name, component))


@mcp.tool()
def add_line(sketch: str, p1: list, p2: list) -> CallToolResult:
    """Add a single line to a sketch. p1, p2 are [x, y] in mm."""
    return _tool_result(sk.add_line(_get_adapter(), sketch, p1, p2))


@mcp.tool()
def add_rectangle(sketch: str, kind: str, p1: list, p2: list, p3: list | None = None) -> CallToolResult:
    """Add a rectangle to a sketch. kind in {center, corner, 3pt}. Returns line_indices for the 4 edges.
    center: p1=center point, p2=corner. corner: p1, p2 are opposite corners. 3pt: p1, p2 define one edge, p3 sets the other edge direction.
    """
    return _tool_result(sk.add_rectangle(_get_adapter(), sketch, kind, p1, p2, p3))


@mcp.tool()
def add_circle(
    sketch: str,
    kind: str,
    center: list | None = None,
    radius_mm: float | None = None,
    p1: list | None = None,
    p2: list | None = None,
    p3: list | None = None,
) -> CallToolResult:
    """Add a circle. kind=center_radius needs center+radius_mm. kind=3pt needs p1+p2+p3.
    Returns circle_index for later reference."""
    kw = {
        k: v
        for k, v in {"center": center, "radius_mm": radius_mm, "p1": p1, "p2": p2, "p3": p3}.items()
        if v is not None
    }
    return _tool_result(sk.add_circle(_get_adapter(), sketch, kind, **kw))


@mcp.tool()
def add_polygon(
    sketch: str, sides: int, center: list, vertex: list, inscribed: bool = True
) -> CallToolResult:
    """Add a regular polygon. sides >= 3. Inscribed (default) means vertices lie on the radius."""
    return _tool_result(sk.add_polygon(_get_adapter(), sketch, sides, center, vertex, inscribed))


@mcp.tool()
def add_geometric_constraint(sketch: str, kind: str, entities: list) -> CallToolResult:
    """Apply a geometric constraint. kind in {horizontal, vertical, parallel, perpendicular, coincident,
    tangent, equal, concentric, fix, midpoint, symmetric}. entities is a list of entity refs like
    'line:0', 'line:0:start', 'circle:1:center', or 'origin'."""
    return _tool_result(sk.add_geometric_constraint(_get_adapter(), sketch, kind, entities))


@mcp.tool()
def add_dimension(
    sketch: str, kind: str, entities: list, expression: str, text_pos: list | None = None
) -> CallToolResult:
    """Add a dimension with an expression. kind in {distance_h, distance_v, distance, angle, radial, diameter}.
    Parameter-name expressions ('body_width', 'length / 2') work AND propagate when the param changes
    (gotchas.md G9, verified 2026-05-31). Use literal mm only when you want a baked value.

    entities depends on kind:
      distance_h / distance_v / distance: 2 point refs (e.g. ['line:0:start', 'line:0:end'])
      angle: 2 line refs
      radial / diameter: 1 circle or arc ref
    """
    return _tool_result(sk.add_dimension(_get_adapter(), sketch, kind, entities, expression, text_pos))


@mcp.tool()
def assert_profiles(sketch: str, expected: int) -> CallToolResult:
    """Check that the sketch has the expected number of closed profiles. Returns ok=False if not.
    Use after building a sketch to catch self-intersecting polygons (which return profiles=2 silently)."""
    return _tool_result(sk.assert_profiles(_get_adapter(), sketch, expected))


@mcp.tool()
def probe_sketch_dimensions(sketch: str, component_name: str | None = None) -> CallToolResult:
    """Dump every dimension in a sketch with parameter name, expression, value, and
    entity-attachment coordinates.

    Use this to identify which literal-named dim (e.g. `d278`) controls which
    geometric feature when sketches use baked-in numbers. Walks root + every
    sub-component to find the sketch; pass component_name for disambiguation
    when the same sketch name exists in multiple components.

    Per-dim payload: dim_class, param_name, expression, value_mm, unit,
    entity_one, entity_two. Entity descriptors include point/start/end/center
    coords in mm so you can match dims to specific lines/circles/ellipses.
    """
    return _tool_result(sk.probe_sketch_dimensions(_get_adapter(), sketch, component_name))


@mcp.tool()
def edit_sketch_dimension(
    sketch: str,
    dim_name: str,
    new_expression: str,
    component_name: str | None = None,
) -> CallToolResult:
    """Change a sketch dimension's expression by parameter name (e.g. `d278`).

    Triggers design.computeAll() before returning. After calling this, run
    `audit_feature_health` to check whether the edit broke any downstream
    features (very common for fillets when their target edges move; see G11).

    On dim_not_found, the response includes the available dimensions in the
    sketch so the agent can correct course without another tool call.

    Args:
        sketch: Sketch name (will search root + every sub-component).
        dim_name: Parameter name of the dimension (use `probe_sketch_dimensions`
            to find these).
        new_expression: New expression as a string. Can be a numeric literal
            ("20 mm"), a user param reference ("oval_x"), or an expression
            ("oval_x / 2 + 1 mm"). Units required if not a bare expression.
        component_name: Optional disambiguation when the sketch name exists in
            multiple components.
    """
    return _tool_result(sk.edit_sketch_dimension(
        _get_adapter(), sketch, dim_name, new_expression, component_name
    ))


@mcp.tool()
def add_ellipse(sketch: str, center: list, major_axis_end: list, minor_axis_end: list) -> CallToolResult:
    """Add an ellipse defined by center + major-axis endpoint + minor-axis endpoint. All in mm."""
    return _tool_result(sk.add_ellipse(_get_adapter(), sketch, center, major_axis_end, minor_axis_end))


@mcp.tool()
def add_arc(
    sketch: str,
    kind: str,
    p1: list | None = None,
    p2: list | None = None,
    p3: list | None = None,
    center: list | None = None,
    start: list | None = None,
    end: list | None = None,
    sweep_radians: float | None = None,
) -> CallToolResult:
    """Add an arc. kind in {3pt, center_start_end, center_start_sweep}.
    3pt: p1, p2, p3.
    center_start_end: center, start, end.
    center_start_sweep: center, start, sweep_radians (positive = CCW).
    """
    kw = {
        k: v
        for k, v in {
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "center": center,
            "start": start,
            "end": end,
            "sweep_radians": sweep_radians,
        }.items()
        if v is not None
    }
    return _tool_result(sk.add_arc(_get_adapter(), sketch, kind, **kw))


@mcp.tool()
def add_spline(sketch: str, points: list, closed: bool = False) -> CallToolResult:
    """Add a fitted spline through a list of [x, y] points (mm). Optionally closed."""
    return _tool_result(sk.add_spline(_get_adapter(), sketch, points, closed))


# ---------- Group 3: Features ----------


@mcp.tool()
def extrude(
    sketch: str,
    profile_index: int = 0,
    operation: str = "new_body",
    extent_kind: str = "distance",
    expression: str | None = None,
    direction: str = "positive",
    is_full_length: bool = True,
    participants: list[str] | None = None,
    name: str | None = None,
    operation_id: str | None = None,
) -> CallToolResult:
    """Extrude a sketch profile into a feature.
    operation: new_body | join | cut | intersect | new_component
    extent_kind: distance (needs expression) | symmetric (needs expression) | all_positive | all_negative
    direction: positive | negative (for distance kind)
    participants: body names to operate on (for cut/join/intersect)
    operation_id: optional UUID for receipt-backed replay and recovery.
    Returns feature_name + bodies_added.
    """
    return _tool_result(feat.extrude(
        _get_adapter(),
        sketch,
        profile_index,
        operation,
        extent_kind,
        expression,
        direction,
        is_full_length,
        participants,
        name,
        operation_id,
    ))


@mcp.tool()
def fillet_edges_by_geometry(
    body: str,
    radius: str,
    parallel_to: str = "z",
    min_length_mm: float | None = None,
    is_tangent_chain: bool = True,
    name: str | None = None,
) -> CallToolResult:
    """Fillet edges of a body by geometric filter (no UI selection needed).
    parallel_to: x | y | z | any — only edges parallel to that axis (or all).
    radius: expression like 'corner_r' or '8 mm'.
    min_length_mm: optional minimum edge length filter (skips tiny rounding edges).
    """
    return _tool_result(feat.fillet_edges_by_geometry(
        _get_adapter(),
        body,
        radius,
        parallel_to,
        min_length_mm,
        is_tangent_chain,
        name,
    ))


@mcp.tool()
def chamfer_edges_by_geometry(
    body: str,
    distance: str,
    parallel_to: str = "z",
    kind: str = "equal",
    distance2: str | None = None,
    angle: str | None = None,
    min_length_mm: float | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Chamfer edges of a body by geometric filter.
    kind: equal (one distance) | two_dist (needs distance2) | dist_angle (needs angle).
    """
    return _tool_result(feat.chamfer_edges_by_geometry(
        _get_adapter(),
        body,
        distance,
        parallel_to,
        kind,
        distance2,
        angle,
        min_length_mm,
        name,
    ))


@mcp.tool()
def mirror_feature(feature_or_body: str, plane: str, name: str | None = None) -> CallToolResult:
    """Mirror a feature or body across a plane. plane: xy | xz | yz | <construction_plane_name>."""
    return _tool_result(feat.mirror_feature(_get_adapter(), feature_or_body, plane, name))


@mcp.tool()
def pattern_rectangular(
    feature_or_body: str,
    x_axis: str = "x",
    x_count: int = 2,
    x_distance: str | None = None,
    y_axis: str | None = None,
    y_count: int = 1,
    y_distance: str | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Rectangular pattern. x_axis / y_axis: x | y | z | <construction_axis_name>.
    x_distance / y_distance: total-extent expressions like '40 mm' or '4 * pitch'.
    """
    return _tool_result(feat.pattern_rectangular(
        _get_adapter(),
        feature_or_body,
        x_axis,
        x_count,
        x_distance,
        y_axis,
        y_count,
        y_distance,
        name,
    ))


@mcp.tool()
def pattern_circular(
    feature_or_body: str,
    axis: str = "z",
    count: int = 6,
    total_angle: str = "360 deg",
    name: str | None = None,
) -> CallToolResult:
    """Circular pattern around an axis. axis: x | y | z | <construction_axis_name>."""
    return _tool_result(feat.pattern_circular(
        _get_adapter(),
        feature_or_body,
        axis,
        count,
        total_angle,
        name,
    ))


@mcp.tool()
def combine(
    target_body: str,
    tool_bodies: list[str],
    operation: str = "join",
    keep_tools: bool = False,
    name: str | None = None,
) -> CallToolResult:
    """Boolean combine. operation: join | cut | intersect. keep_tools: preserve tool bodies after op."""
    return _tool_result(feat.combine(
        _get_adapter(),
        target_body,
        tool_bodies,
        operation,
        keep_tools,
        name,
    ))


@mcp.tool()
def revolve(
    sketch: str,
    profile_index: int = 0,
    axis: str = "z",
    operation: str = "new_body",
    extent_kind: str = "full",
    angle: str | None = None,
    is_symmetric: bool = False,
    participants: list[str] | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Revolve a sketch profile around an axis.
    axis: x | y | z | <construction_axis_name>
    extent_kind: full (360deg) | angle (needs angle expr)
    operation: new_body | join | cut | intersect | new_component
    """
    return _tool_result(feat.revolve(
        _get_adapter(),
        sketch,
        profile_index,
        axis,
        operation,
        extent_kind,
        angle,
        is_symmetric,
        participants,
        name,
    ))


@mcp.tool()
def shell(
    body: str,
    thickness: str,
    face_normals_to_remove: list | None = None,
    direction: str = "inside",
    name: str | None = None,
) -> CallToolResult:
    """Hollow out a body. face_normals_to_remove is a list of [nx, ny, nz] for faces to remove
    (e.g. [[0, 0, 1]] removes the top face). Empty / omitted = closed shell.
    direction: inside | outside | both.
    """
    return _tool_result(feat.shell(
        _get_adapter(),
        body,
        thickness,
        face_normals_to_remove,
        direction,
        name,
    ))


@mcp.tool()
def add_hole(
    body: str,
    position_mm: list,
    diameter: str,
    kind: str = "simple",
    cbore_diameter: str | None = None,
    cbore_depth: str | None = None,
    csink_diameter: str | None = None,
    csink_angle: str | None = None,
    extent_kind: str = "all",
    depth_expression: str | None = None,
    name: str | None = None,
    face_handle: str | None = None,
) -> CallToolResult:
    """Add a hole to one root-component body, optionally on an explicit planar face.
    kind: simple | counterbore (needs cbore_diameter + cbore_depth) | countersink (needs csink_diameter + csink_angle).
    position_mm: model-space [x, y, z]. Without face_handle, select the nearest
    trimmed +Z face at that XY. Only the selected body participates in the cut.
    extent_kind: all | distance (needs depth_expression).
    """
    return _tool_result(feat.add_hole(
        _get_adapter(),
        body,
        position_mm,
        diameter,
        kind,
        cbore_diameter,
        cbore_depth,
        csink_diameter,
        csink_angle,
        extent_kind,
        depth_expression,
        name,
        face_handle,
    ))


@mcp.tool()
def rebuild_feature(feature_name: str, component_name: str | None = None) -> CallToolResult:
    """Return safe_rebuild_unavailable without changing the design.
    Automatic delete-and-recreate cannot preserve downstream references or
    reliably identify replacement profiles. Inspect audit_feature_health and
    correct the original feature inputs instead.
    """
    return _tool_result(feat.rebuild_feature(_get_adapter(), feature_name, component_name))


@mcp.tool()
def move_body(
    body: str,
    translation_mm: list | None = None,
    rotation_axis: list | None = None,
    rotation_angle_deg: float | None = None,
    rotation_origin_mm: list | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Move a body via a Move feature (parametric in the timeline).
    Provide translation_mm (mm) and/or rotation (axis + angle_deg).
    """
    return _tool_result(feat.move_body(
        _get_adapter(),
        body,
        translation_mm,
        rotation_axis,
        rotation_angle_deg,
        rotation_origin_mm,
        name,
    ))


@mcp.tool()
def rib(
    sketch: str,
    thickness: str,
    side: str = "symmetric",
    extend_profile: bool = True,
    name: str | None = None,
    target_body: str | None = None,
    is_flipped: bool = False,
) -> CallToolResult:
    """Create a symmetric to-next rib on builds exposing RibFeatures.createInput.
    target_body is required. Older builds return rib_not_scriptable.
    is_flipped reverses the in-plane extrusion direction. A warning/error
    returns rib_feature_unhealthy and reports that the model may have changed.
    """
    return _tool_result(feat.rib(
        _get_adapter(), sketch, thickness, side, extend_profile, name, target_body, is_flipped
    ))


# ---------- Group 5: Assembly & Motion (first slice, no joints) ----------


@mcp.tool()
def bodies_to_components(mapping: dict) -> CallToolResult:
    """Convert root-level bodies into named components, preserving world position.
    mapping is {body_name: new_component_name}. Multi-body designs need this before
    joint/motion work — Fusion's joints operate on components, not bodies.
    """
    return _tool_result(asm.bodies_to_components(_get_adapter(), mapping))


@mcp.tool()
def move_component(
    name: str,
    translation_mm: list | None = None,
    rotation_axis: list | None = None,
    rotation_angle_deg: float | None = None,
    rotation_origin_mm: list | None = None,
) -> CallToolResult:
    """Move an occurrence by translation (mm) and/or rotation (axis + angle deg).
    rotation_origin_mm defaults to [0, 0, 0] (world origin) if omitted.
    Use a full occurrence path when a name is ambiguous. Grounded occurrences
    and designs with pending position changes return an error. The response
    reports whether the move was applied and includes the resulting transform.
    """
    return _tool_result(asm.move_component(
        _get_adapter(),
        name,
        translation_mm,
        rotation_axis,
        rotation_angle_deg,
        rotation_origin_mm,
    ))


@mcp.tool()
def ground_component(name: str) -> CallToolResult:
    """Pin an occurrence at its current world pose. Release Ground To Parent before adding a pin."""
    return _tool_result(asm.ground_component(_get_adapter(), name))


@mcp.tool()
def unground_component(name: str) -> CallToolResult:
    """Clear Pin and Ground To Parent on an occurrence."""
    return _tool_result(asm.unground_component(_get_adapter(), name))


@mcp.tool()
def create_rigid_group(component_names: list, name: str | None = None,
                       include_children: bool = True, preview: bool = False,
                       max_occurrences: int | None = None, operation_id: str | None = None) -> CallToolResult:
    """Lock 2+ occurrences together. Preview selected/expanded counts before large groups.
    include_children defaults to legacy True; max_occurrences refuses oversized groups before mutation.
    Use a unique name and reuse the same operation_id only for the identical request.
    """
    return _tool_result(asm.create_rigid_group(_get_adapter(), component_names, name,
                                              include_children, preview, max_occurrences, operation_id))


@mcp.tool()
def inspect_rigid_group(name: str, limit: int = 50, offset: int = 0) -> CallToolResult:
    """Read a uniquely named root rigid group's actual membership and suppression state.
    Unknown health stays null. Membership is paginated; limit is 1..200.
    """
    return _tool_result(asm.inspect_rigid_group(_get_adapter(), name, limit, offset))


@mcp.tool()
def create_contact_set(body_names: list) -> CallToolResult:
    """Create a contact set for physics / motion analysis between 2+ bodies."""
    return _tool_result(asm.create_contact_set(_get_adapter(), body_names))


@mcp.tool()
def interference_check(entity_names: list) -> CallToolResult:
    """Run an interference analysis on 2+ bodies or occurrences.
    Returns per-pair interference volume in mm^3. Empty pair list = no interference.
    """
    return _tool_result(asm.interference_check(_get_adapter(), entity_names))


@mcp.tool()
def set_joint_limits(
    joint_name: str,
    min_value: str | None = None,
    max_value: str | None = None,
    rest_value: str | None = None,
) -> CallToolResult:
    """Set min/max/rest values on an existing joint by name.
    Values are expressions: angles for revolute (e.g. '45 deg'), distances for slider.
    rest_value is the NEW May 2026 snap-back position. Pass None to leave any field unset.
    Limits bind to rotationLimits when the motion has one, else slideLimits
    (cylindrical joints get rotation limits). The response echoes back what
    Fusion actually stored under `applied` (internal units: radians / cm).
    """
    return _tool_result(asm.set_joint_limits(
        _get_adapter(), joint_name, min_value, max_value, rest_value
    ))


@mcp.tool()
def drive_joint(joint_name: str, value: str) -> CallToolResult:
    """Drive an existing joint to a target value.
    value is an expression: angle for revolute, distance for slider, etc.
    Tries jointMotion attributes in order: rotation, slide, roll, pitch, yaw.
    Fusion silently ignores drives beyond the joint's limits; check `applied`
    in the response (false = the joint stayed where it was).
    """
    return _tool_result(asm.drive_joint(_get_adapter(), joint_name, value))


@mcp.tool()
def create_joint(
    geometry_one: str,
    geometry_two: str,
    motion_type: str = "rigid",
    axis: str = "z",
    offset_mm: str | None = None,
    angle_deg: str | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Create a Joint between two entity-handle origins.

    Joint origins are specified as entity handles (kind:path:token). Supported
    kinds: face (planar OR cylindrical/conical), edge (uses MiddleKeyPoint),
    vertex (uses Point), point (sketch point). Get handles from
    list_body_entities for face/edge/vertex, or use the sketch tools' returned
    handles for sketch points.

    motion_type: one of rigid, revolute, slider, cylindrical, ball.
    - rigid: locks the two origins; no DOF
    - revolute: 1 DOF rotation about `axis` (use for hinges)
    - slider: 1 DOF translation along `axis`
    - cylindrical: rotation + translation along `axis`
    - ball: 3 DOF rotation (axis arg ignored; Fusion only accepts pitch=Z,
      yaw=X, which is what gets used)

    axis: x | y | z, relative to the first joint geometry's local frame.
    For typical face-normal rotation (hinge pin coming out of the face) use z.

    offset_mm: optional ValueInput expression for lateral offset between the
    two geometries. A snap hinge typically needs a few mm here to hold the gap
    between mating flanges.
    Preserved across user-param changes (unlike Move-based positioning).

    angle_deg: optional initial angle (revolute / cylindrical).

    name: optional joint name in the timeline.

    Returns {ok, joint_name, joint_token, motion_type, axis, offset_mm, angle_deg}.
    Use joint_name for downstream drive_joint / set_joint_limits calls.

    Errors:
    - handle_invalid: one of the geometry handles could not be resolved.
    - joint_geometry_failed: face/edge/vertex/point handle resolved but Fusion
      could not build a JointGeometry from it (e.g. degenerate face).
    - joints_add_failed: joint inputs were valid but Fusion refused the joint
      (usually means the two geometries are mateable only with a different
      motion type, or one geometry is in a frozen component).
    """
    return _tool_result(asm.create_joint(
        _get_adapter(),
        geometry_one,
        geometry_two,
        motion_type,
        axis,
        offset_mm,
        angle_deg,
        name,
    ))


# ---------- Group 4: Construction Geometry ----------


@mcp.tool()
def create_construction_plane(
    kind: str,
    name: str | None = None,
    base_plane: str | None = None,
    offset: str | None = None,
    plane_a: str | None = None,
    plane_b: str | None = None,
    axis: str | None = None,
    angle: str | None = None,
    p1: list | None = None,
    p2: list | None = None,
    p3: list | None = None,
) -> CallToolResult:
    """Create a construction plane. kind in {offset, midplane, at_angle, 3_points}.
    offset: base_plane + offset expr (e.g. 'thickness')
    midplane: plane_a + plane_b (between two existing planes)
    at_angle: axis + base_plane + angle expr (e.g. '30 deg')
    3_points: p1, p2, p3 each as [x, y, z] in mm
    Plane / axis names default to principals: 'xy', 'xz', 'yz' / 'x', 'y', 'z'.
    """
    kw = {
        k: v
        for k, v in {
            "base_plane": base_plane,
            "offset": offset,
            "plane_a": plane_a,
            "plane_b": plane_b,
            "axis": axis,
            "angle": angle,
            "p1": p1,
            "p2": p2,
            "p3": p3,
        }.items()
        if v is not None
    }
    return _tool_result(cons.create_construction_plane(_get_adapter(), kind, name, **kw))


@mcp.tool()
def create_construction_axis(
    kind: str,
    name: str | None = None,
    p1: list | None = None,
    p2: list | None = None,
    body: str | None = None,
    face_normal: list | None = None,
) -> CallToolResult:
    """Create a construction axis. kind in {2_points, normal_to_face_by_geometry}.
    2_points: p1, p2 each as [x, y, z] in mm
    normal_to_face_by_geometry: body name + face_normal [nx, ny, nz] (finds the face whose normal matches)
    """
    kw = {
        k: v
        for k, v in {
            "p1": p1,
            "p2": p2,
            "body": body,
            "face_normal": face_normal,
        }.items()
        if v is not None
    }
    return _tool_result(cons.create_construction_axis(_get_adapter(), kind, name, **kw))


@mcp.tool()
def create_construction_point(coords: list, name: str | None = None) -> CallToolResult:
    """Create a construction point at explicit [x, y, z] coordinates (mm)."""
    return _tool_result(cons.create_construction_point(_get_adapter(), coords, name))


@mcp.tool()
def delete_construction(name: str) -> CallToolResult:
    """Delete a construction plane / axis / point by name."""
    return _tool_result(cons.delete_construction(_get_adapter(), name))


# ---------- Group 5 / 6 / 2 / 3: Handle-consuming tools ----------


@mcp.tool()
def list_body_entities(
    body: str,
    kinds: list | None = None,
    face_normal_filter: list | None = None,
    edge_parallel_to: str | None = None,
    min_edge_length_mm: float | None = None,
) -> CallToolResult:
    """Enumerate faces / edges / vertices of a body and return their handles.
    This is the bridge between name-addressing (bodies) and handle-addressing (sub-entities).

    kinds: subset of [face, edge, vertex]. Defaults to all three.
    face_normal_filter: [nx, ny, nz] — only faces matching that normal (e.g. [0, 0, 1] for top).
    edge_parallel_to: x | y | z — only axis-parallel edges.
    min_edge_length_mm: filter out short edges (typical use: skip fillet runouts).

    Returns face/edge/vertex lists, each with handle, index, and geometric info.
    Use the returned handles in measure, fillet_edges, chamfer_edges, project_to_sketch, etc.
    """
    return _tool_result(ht.list_body_entities(
        _get_adapter(),
        body,
        kinds,
        face_normal_filter,
        edge_parallel_to,
        min_edge_length_mm,
    ))


@mcp.tool()
def measure(entity_a: str, entity_b: str, kind: str = "distance") -> CallToolResult:
    """Measure between two entities using their handles (from list_body_entities or tool returns).
    kind: distance (min distance + nearest points) | min_distance (value only) | angle.
    """
    return _tool_result(ht.measure(_get_adapter(), entity_a, entity_b, kind))


@mcp.tool()
def find_mesh_using_ray(
    origin_mm: list,
    direction: list,
    component_name: str | None = None,
) -> CallToolResult:
    """Cast a ray in root coordinates and return intersected mesh handles.
    origin_mm is [x, y, z] in mm and direction is [dx, dy, dz].
    Pass a unique component name or full occurrence path to limit hits to that
    occurrence and its descendants. Omit component_name to search the whole assembly.
    """
    return _tool_result(ht.find_mesh_using_ray(_get_adapter(), origin_mm, direction, component_name))


@mcp.tool()
def ray_collision_with_mesh(
    mesh_handle: str,
    origin_mm: list[float] | None = None,
    direction: list[float] | None = None,
    queries: list[dict] | None = None,
    frame: str = "root",
    tolerance_mm: float = 1e-6,
    replay: dict | None = None,
) -> CallToolResult:
    """Intersect one mesh with a legacy ray or bounded batch of reproducible probes.
    Supply either origin_mm/direction, queries, or replay. Batch queries specify
    origin_mm, direction, start_mm, end_mm and selection ('all', 'nearest', 'farthest').
    frame is 'root' or 'local'; travel and tolerance are mm. No-hit means missing
    coverage, not free space. Returned witnesses can be replayed only against
    matching source geometry and pose. Mesh handles come from inspect_assembly
    include=['meshes'], import_geometry or find_mesh_using_ray.
    """
    return _tool_result(ht.ray_collision_with_mesh(
        _get_adapter(), mesh_handle, origin_mm, direction, queries=queries,
        frame=frame, tolerance_mm=tolerance_mm, replay=replay,
    ))


@mcp.tool()
def fillet_edges(
    edge_handles: list, radius: str, is_tangent_chain: bool = True, name: str | None = None
) -> CallToolResult:
    """Fillet specific edges by handle (UI-selection style).
    edge_handles: list from list_body_entities (kinds=['edge']).
    radius: expression like 'corner_r' or '8 mm'.
    """
    return _tool_result(ht.fillet_edges(_get_adapter(), edge_handles, radius, is_tangent_chain, name))


@mcp.tool()
def chamfer_edges(
    edge_handles: list,
    distance: str,
    kind: str = "equal",
    distance2: str | None = None,
    angle: str | None = None,
    name: str | None = None,
) -> CallToolResult:
    """Chamfer specific edges by handle.
    kind: equal | two_dist (needs distance2) | dist_angle (needs angle).
    """
    return _tool_result(ht.chamfer_edges(
        _get_adapter(), edge_handles, distance, kind, distance2, angle, name
    ))


@mcp.tool()
def project_to_sketch(sketch: str, entity_handles: list) -> CallToolResult:
    """Project entities (by handle) onto a named sketch. Useful for cut-extrudes that reference body geometry."""
    return _tool_result(ht.project_to_sketch(_get_adapter(), sketch, entity_handles))


# ---------- Group 6: Verify & Visualize ----------


@mcp.tool()
def bounding_box(body_name: str | None = None) -> CallToolResult:
    """Return mm bbox + extents for a body (by name) or all bodies if name omitted.
    Walks root + every occurrence.
    """
    return _tool_result(verify_tools.bounding_box(_get_adapter(), body_name))


@mcp.tool()
def volume(body_name: str | None = None) -> CallToolResult:
    """Return volume in cm^3 and mm^3 for a body (by name) or all bodies if name omitted."""
    return _tool_result(verify_tools.volume(_get_adapter(), body_name))


@mcp.tool()
def mass(body_name: str | None = None) -> CallToolResult:
    """Return mass (kg), material name, density, and volume for a body or all bodies.
    Material is whatever the body has been assigned; uses Fusion's physical-properties calc.
    """
    return _tool_result(verify_tools.mass(_get_adapter(), body_name))


@mcp.tool()
def center_of_mass(body_name: str | None = None) -> CallToolResult:
    """Return center of mass in mm world coordinates for a body or all bodies."""
    return _tool_result(verify_tools.center_of_mass(_get_adapter(), body_name))


@mcp.tool()
def audit_feature_health(
    component_name: str | None = None,
    include_healthy: bool = False,
) -> CallToolResult:
    """Sweep the feature tree (root + every sub-component) and report features
    whose healthState != 0.

    Call this after ANY tool that edits sketch geometry, user parameters, or
    features. If summary.failed > 0 (state 2) or summary.warning > 0
    (state 1), the BREP may look healthy via the API while Fusion's UI shows
    a stale cached display. See gotchas G10/G11.

    Args:
        component_name: Restrict the audit to a unique component or occurrence
            path. Default: root + all.
        include_healthy: Include healthy features (state 0) in the features
            list. Default False — non-healthy entities are returned.

    Returns envelope.result = {
        ok, summary: {total, healthy, warning, failed, suppressed, rolled_back, unknown},
        features: [{path, component, name, classType, category, healthState, healthLabel,
                    isSuppressed, errorOrWarningMessage}, ...]
    }
    States 3, 4, and 5 mean suppressed, rolled back, and unknown, respectively.
    """
    return _tool_result(verify_tools.audit_feature_health(
        _get_adapter(), component_name, include_healthy
    ))


@mcp.tool()
def screenshot(
    direction: str = "iso-top-right",
    width: int | None = None,
    height: int | None = None,
    transparent: bool = True,
    anti_aliasing: bool = True,
) -> CallToolResult:
    """Capture a PNG screenshot of the active viewport as a native MCP image.
    direction: one of current, front, back, bottom, top, left, right, iso-* (4 variants).
    Fusion handles fit-view internally for named directions.
    """
    return _image_result(
        viz_tools.screenshot(_get_adapter(), direction, width, height, transparent, anti_aliasing)
    )


@mcp.tool()
def set_view(direction: str = "iso-top-right", fit: bool = True) -> CallToolResult:
    """Orient the viewport to a named direction. Optionally fit to view.
    Use when you want to change camera without capturing a screenshot.
    """
    return _tool_result(viz_tools.set_view(_get_adapter(), direction, fit))


@mcp.tool()
def screenshot_compare_with_marker(
    before_marker_position: int,
    direction: str = "iso-top-right",
    width: int | None = None,
    height: int | None = None,
    transparent: bool = True,
    anti_aliasing: bool = True,
) -> CallToolResult:
    """Capture before/after screenshots with the same view direction by rolling the
    timeline marker to `before_marker_position`, screenshotting, restoring
    the original marker position, and screenshotting again.

    If rollback or the before capture fails, this tool still attempts to
    restore the marker. The restore_marker_failed error means the original
    marker position could not be verified.

    Args:
        before_marker_position: Timeline marker position to capture as the
            "before" state. Must be in [0, timeline.count]. Use 0 for the
            initial empty state, or the index just before a specific feature.
        direction: One of: current, front, back, bottom, top, left, right,
            iso-bottom-left/right, iso-top-left/right. Defaults to iso-top-right.
        width, height: Optional image dimensions in pixels.
        transparent: PNG transparency. Default True.
        anti_aliasing: Smooth edges. Default True.

    Returns two native image blocks in before/after order. Structured metadata
    identifies their content indices, the marker positions, and the direction.
    """
    return _image_result(
        viz_tools.screenshot_compare_with_marker(
            _get_adapter(),
            before_marker_position,
            direction,
            width,
            height,
            transparent,
            anti_aliasing,
        ),
        compare=True,
    )


# ---------- Group 7: IO & Knowledge ----------


@mcp.tool()
def find_api(
    query: str,
    kind: str | None = None,
    namespace: str | None = None,
    limit: int = 5,
) -> CallToolResult:
    """Search the local Fusion API help corpus.

    Returns top-N matches as {slug, title, namespace, kind, url, is_preview, introduced, snippet}.
    Use this BEFORE writing a script that touches an unfamiliar method or class.

    Filters:
      kind: 'object' | 'member' | 'manual' | etc — restrict to one page type
      namespace: substring match on namespace (e.g. 'Fusion', 'CAM', 'core')
    """
    return _tool_result(kn.find_api(query, kind=kind, namespace=namespace, limit=limit))


@mcp.tool()
def find_pattern(query: str, limit: int = 5) -> CallToolResult:
    """Search historical Fusion modeling recipes by intent.

    Entries may describe older builds or retired APIs. Check current Autodesk
    documentation and verify resulting geometry when adapting a recipe.
    """
    return _tool_result(kn.find_pattern(query, limit))


@mcp.tool()
def find_tool(query: str, limit: int = 5) -> CallToolResult:
    """Search this server's own tool reference (tools.md) by intent or symptom.

    Returns the matching sections: signatures, exact enum values, return keys,
    and error codes. Use this to look up how to call a tool instead of guessing
    at arguments, and to check what an error code means.

    Works with Fusion closed.
    """
    return _tool_result(kn.find_tool(query, limit))


@mcp.tool()
def find_gotcha(query: str, limit: int = 5) -> CallToolResult:
    """Search gotchas.md (known failure modes) by symptom or keyword.

    Use when something behaves unexpectedly: check here for the documented cause
    + fix before debugging from scratch.
    """
    return _tool_result(kn.find_gotcha(query, limit))


@mcp.tool()
def export(
    format: str,
    path: str,
    body: str | None = None,
    refinement: str = "medium",
    units: str = "mm",
    component: str | None = None,
    operation_id: str | None = None,
) -> CallToolResult:
    """Export geometry to a file. format in {stl, 3mf, step, iges, obj, f3d, sat, smt}.
    path: absolute path (relative + '..' + Windows reserved names rejected).
    body: optional unique body selector for STL/3MF/OBJ.
    component: optional occurrence path selecting a component for CAD formats
    or an occurrence for mesh formats. Body scope is rejected for CAD formats.
    refinement: low | medium | high — applies to STL and OBJ mesh quality.
    units: mm | cm | m | inch — STL only.
    """
    return _tool_result(io_tools.export(
        _get_adapter(), format, path, body, refinement, units, component, operation_id
    ))


@mcp.tool()
def import_geometry(format: str, path: str, target_component: str | None = None,
                    operation_id: str | None = None, units: str | None = None,
                    dependency_roots: list[str] | None = None,
                    appearance_policy: str = "require_resources") -> CallToolResult:
    """Import geometry from a file into the active design.
    format in {step, iges, sat, smt, f3d, obj}.
    path: absolute path to an existing file.
    Returns imported object identities, source hash, units, and target frame.
    target_component accepts a unique component or occurrence path.
    OBJ requires units in {mm, cm, m, in, inch}. Dependencies must remain within
    its resolved directory or explicitly supplied dependency_roots. OBJ appearance
    policy is require_resources or geometry_only; file presence does not verify
    texture display. Retain operation_id before dispatch for safe status lookup.
    """
    return _tool_result(io_tools.import_geometry(
        _get_adapter(), format, path, target_component, operation_id=operation_id,
        units=units, dependency_roots=dependency_roots, appearance_policy=appearance_policy,
    ))


# ---------- Escape hatch ----------


@mcp.tool()
def open_local_archive(path: str, operation_id: str | None = None) -> CallToolResult:
    """Open a local F3D in a separate unsaved document; preserve existing documents.
    Returns source hash, document creation ID, bounded counts and health. Does not save to cloud.
    """
    return _tool_result(local_files.open_local_archive(_get_adapter(), path, operation_id=operation_id))


@mcp.tool()
def save_viewport(path: str, width: int = 1600, height: int = 1000,
                  direction: str = "current", fit: bool = False,
                  operation_id: str | None = None) -> CallToolResult:
    """Publish a local PNG atomically; return dimensions, byte count and SHA-256, no base64.
    direction: current, top, bottom, front, back, left, right, or iso-top-right,
    iso-top-left, iso-bottom-right, iso-bottom-left. Temporary camera changes
    are restored and checked before publication. Image validity does not establish CAD correctness.
    """
    return _tool_result(local_files.save_viewport(_get_adapter(), path, width, height,
                                                 direction, fit, operation_id=operation_id))


@mcp.tool()
def create_parametric_rectangle(name: str, width: str, height: str, x: str = "0 mm",
                                y: str = "0 mm", z: str = "0 mm", plane: str = "xy",
                                centered: bool = False, component: str | None = None) -> CallToolResult:
    """Create a fully constrained named rectangle sketch and linked datum anchor.
    Width/height and component-frame x/y/z are Fusion length expressions. Returns one profile,
    world-independent component coordinates and verification. Requires a unique editable definition.
    plane is xy/xz/yz; positive sketch directions on xz/yz differ from component axes.
    """
    return _tool_result(parametric_sketch.create_parametric_rectangle(
        _get_adapter(), name, width, height, x, y, z, plane, centered, component))


@mcp.tool()
def get_operation_status(operation_id: str) -> CallToolResult:
    """Read a durable local receipt without contacting Fusion. Results may arrive after a timeout.
    A recorded declaration is historical evidence, not proof of current geometry or rollback.
    """
    return _tool_result(get_status(operation_id))


@mcp.tool()
def reconcile_operation(operation_id: str, expected_document: str, acknowledgement: bool,
                         evidence: str, allow_missing_identity: bool = False) -> CallToolResult:
    """Release an unresolved write barrier after independent inspection and a fresh document probe.
    acknowledgement=True asserts that prior execution has ended. expected_document is the receipt's
    creation ID; evidence describes inspection/recovery. This establishes neither success nor rollback.
    allow_missing_identity explicitly accepts weaker evidence only when no original identity exists;
    it never permits a known mismatch or re-executes the operation.
    """
    return _tool_result(operations.reconcile_operation(_get_adapter(), operation_id,
                                                       expected_document, acknowledgement, evidence, allow_missing_identity))


@mcp.tool()
def inspect_assembly(component: str | None = None, include: list[str] | None = None,
                     limit: int = 50, offset: int = 0, detail: str = "full",
                     fields: list[str] | None = None, health_filter: str | None = None) -> CallToolResult:
    """Read bounded assembly structure, occurrence transforms, B-reps, meshes, sketches, features and joints.
    component is root (default) or a unique occurrence path. include selects occurrences, bodies,
    meshes, sketches, features, joints or as_built_joints. Occurrences lists the selected subtree's descendants;
    limit (1..200) and offset bound each section. Frames and units accompany measurements.
    Reads shared definitions without editing them. Does not recompute or move the timeline.
    detail=summary returns counts; full returns paginated items. fields selects handles,
    transforms, health and bounds (None=these defaults, []=none); opt-in mesh_geometry adds
    a bounded native node/index digest, not appearance verification. health_filter=non_healthy includes
    warning/error/unknown sketches and features only. Pagination includes next_offset.
    """
    return _tool_result(inspect_tools.inspect_assembly(_get_adapter(), component, include, limit,
                                                       offset, detail, fields, health_filter))


@mcp.tool()
def inspect_feature(feature: str, limit: int = 50) -> CallToolResult:
    """Read a unique feature name or 'Occurrence:1 / Feature': health, parameters, current faces,
    affected bodies and linked features. Surviving faces are not a complete history or dependency graph.
    limit bounds each collection. Does not move the timeline or alter the design.
    """
    return _tool_result(inspect_tools.inspect_feature(_get_adapter(), feature, limit))


@mcp.tool()
def query_geometry(body: str, kind: str, filters: dict | None = None, limit: int = 50,
                   offset: int = 0, expected_count: int | None = None) -> CallToolResult:
    """Find current face/edge/vertex handles on a unique body or occurrence-qualified body.
    Filters: type (Fusion geometry class), normal/parallel [x,y,z], radius/length in mm,
    near [x,y,z] (distance to sample point), tolerance (mm for lengths; dimensionless for directions),
    feature (current surviving faces).
    Geometry is reported in root coordinates. expected_count refuses ambiguous/unexpected selection.
    Re-query after topology edits. limit 1..200; offset paginates matching entities.
    """
    return _tool_result(inspect_tools.query_geometry(_get_adapter(), body, kind, filters, limit, offset, expected_count))


@mcp.tool()
def validate_design(checks: list[dict]) -> CallToolResult:
    """Read-only mechanical checks (1..50). Each check is one of:
    {type:'clearance', body_a, body_b, min_mm}; {type:'interference', body_a, body_b, max_overlap_mm3?:0};
    {type:'bounds', body, min_mm:[x,y,z], max_mm:[x,y,z], tolerance_mm?:0};
    {type:'face_distance', face_a, face_b, min_mm?, max_mm?, tolerance_mm};
    {type:'surface_distance', name, body, point_mm:[x,y,z], frame:'root', max_mm, tolerance_mm, max_faces}.
    Face distance requires current distinct face handles and at least one bound. It returns native
    minimum distance and witness points in root mm, not global wall thickness. Reacquire faces after edits.
    Surface distance requires a current document-scoped body handle, unique probe name and max_faces
    in 1..512 (sum <=4096). It measures the point against every body face; excess faces are unavailable.
    It is a sampled directional surface check, not Hausdorff distance or full export equivalence.
    Native scalar/witness contradictions retain scalar evidence and cannot pass distance validation.
    Face/surface witnesses must also pass native trimmed-face membership; false or unavailable
    isPointOnFace results retain raw scalar candidates and make validation unavailable.
    Body selectors accept names, occurrence-qualified names or body handles. Clearance uses B-rep
    measurement plus interference. Bounds are conservative root-axis boxes, not cavity containment.
    Read result.passed and each status: pass, violation or unavailable. No unchecked result passes.
    """
    return _tool_result(validation_tools.validate_design(_get_adapter(), checks))


@mcp.tool()
def set_reference_metadata(component: str, metadata: dict) -> CallToolResult:
    """Attach portable v1 mechanical metadata to a local component definition. Geometry is unchanged.
    Required metadata: schema_version:1, units:'mm', reference_id. Optional mounting_points
    [{id,position_mm,axis,hole_diameter_mm?}], connectors [{id,origin_mm,x_axis,y_axis}],
    keepouts [{id,min_mm,max_mm}], source_sha256. Directions must be unit vectors.
    All coordinates are component-local. Shared/referenced definitions refuse edits.
    Import STEP with import_geometry first; this tool does not import or fetch models.
    """
    return _tool_result(reference_tools.set_reference_metadata(_get_adapter(), component, metadata))


@mcp.tool()
def get_reference_metadata(component: str) -> CallToolResult:
    """Read portable mechanical metadata, root-frame body bounds, and component-to-root transform.
    Mounts, connectors and keep-outs remain explicitly component-local; metadata is user supplied,
    not inferred or certified. Use an occurrence path for repeated components. Read-only.
    """
    return _tool_result(reference_tools.get_reference_metadata(_get_adapter(), component))


@mcp.tool()
def create_component(name: str, parent: str | None = None) -> CallToolResult:
    """Create a uniquely named empty local component under root or a unique parent occurrence.
    Requires a parametric design with the timeline at its end and no pending transforms.
    Refuses duplicate names and edits to shared parents. Returns scoped occurrence identity and pose.
    """
    return _tool_result(placement_tools.create_component(_get_adapter(), name, parent))


@mcp.tool()
def insert_component(source: str, operation_id: str, parent: str | None = None,
                     independent: bool = False) -> CallToolResult:
    """Insert another local occurrence of source, or an independent definition when independent=True.
    source and parent are occurrence selectors/handles. operation_id prevents duplicate retries in
    the same parent; an independent copy also uses it as its component name. Shared insertion never
    renames the source definition. No cloud links; placement starts at the parent origin.
    """
    return _tool_result(placement_tools.insert_component(_get_adapter(), source, operation_id, parent, independent))


@mcp.tool()
def place_component(component: str, origin_mm: list[float], x_axis: list[float] | None = None,
                    y_axis: list[float] | None = None, relative_to: str | None = None) -> CallToolResult:
    """Set an occurrence's absolute coordinate frame: origin in mm plus perpendicular unit X/Y axes.
    Default axes are root X/Y. relative_to expresses the frame in another occurrence's coordinates.
    Verifies the full resulting transform and captures its own position snapshot. Refuses pinned,
    parent-grounded, shared-parent or pending-transform edits. Repeating the same pose is a no-op.
    Use move_component for relative translations/rotations; this tool sets an absolute pose.
    """
    return _tool_result(placement_tools.place_component(_get_adapter(), component, origin_mm, x_axis, y_axis, relative_to))


@mcp.tool()
def create_parametric_enclosure(name: str, width: str, depth: str, height: str, wall: str,
                               floor: str, component: str = "root") -> CallToolResult:
    """Create an open rectangular enclosure centered on component XY, with floor at local Z=0.
    Dimensions are Fusion length expressions (e.g. case_width, '2 mm'). Creates constrained native
    sketches and editable extrudes, verifies dimensions/volume/health, and returns feature selectors.
    Name must be unused in the component; repeated names refuse rather than duplicate geometry.
    Shared or externally linked component edits refuse. Does not create a lid or plan an assembly.
    """
    return _tool_result(primitive_tools.create_parametric_enclosure(_get_adapter(), name, width, depth, height, wall, floor, component))


@mcp.tool()
def create_mounting_boss(name: str, x: str, y: str, z: str, outer_diameter: str,
                         inner_diameter: str, height: str, component: str = "root",
                         target_body: str | None = None) -> CallToolResult:
    """Create an editable cylindrical/annular boss along component +Z at local x,y,z expressions.
    All dimensions accept Fusion length expressions. inner_diameter='0 mm' makes a solid boss.
    Creates a new body unless target_body explicitly selects a solid to join in the same component.
    Checks material added, sibling identity/volume/bounds and feature health. Returns feature selectors.
    """
    return _tool_result(primitive_tools.create_mounting_boss(_get_adapter(), name, x, y, z, outer_diameter, inner_diameter,
                                               height, component, target_body))


@mcp.tool()
def create_cutout(name: str, body: str, x: str, y: str, z: str, width: str, height: str,
                 depth: str, plane: str = "xy", direction: str = "positive",
                 component: str = "root") -> CallToolResult:
    """Cut a parametric rectangle from one explicit solid. Local x,y,z anchor its lower-left corner;
    width/height follow the selected xy/xz/yz sketch axes (Fusion XZ sketch Y is -Z).
    All dimensions/positions are length expressions. depth follows plane normal or negative normal.
    Verifies material removal and sibling preservation; a missed body is an error with targeted cleanup.
    """
    return _tool_result(primitive_tools.create_cutout(_get_adapter(), name, body, x, y, z, width, height, depth,
                                        plane, direction, component))


@mcp.tool()
def sweep(sketch: str, path_sketch: str, path_entities: list[str], name: str,
          profile_index: int = 0) -> CallToolResult:
    """Create a native new-body sweep from a closed sketch profile along explicitly listed curves.
    path_entities are line:N, arc:N or circle:N in path_sketch; no implicit chained selection.
    Both sketches must belong to the same uniquely editable component. Use a unique name to refuse
    duplicate retries. Verifies positive solid volume and health; returns feature/body handles.
    Cuts, guide rails, twist and multi-section lofts remain available through execute.
    """
    return _tool_result(native_tools.sweep(_get_adapter(), sketch, path_sketch, path_entities, profile_index, name))


# ---------- Local workspaces ----------


@mcp.tool()
def workspace_init(workspace_path: str) -> CallToolResult:
    """Initialize local workspace storage at an absolute host path. Works with Fusion closed."""
    return _tool_result(workspace.initialize(workspace_path))


@mcp.tool()
def workspace_bind(document_name: str | None = None) -> CallToolResult:
    """Probe the active document or a unique exact document name for a target handle.

    The handle identifies this open Fusion document instance. Use it while the document
    and Fusion session remain open. Later operations check the bound active target.
    """
    return _tool_result(workspace.bind(_get_adapter(), document_name=document_name))


@mcp.tool()
def workspace_inspect(
    target_handle: str, parameter_name: str, checks: list[dict[str, Any]],
    scope: dict[str, Any] | None = None, baseline: dict[str, Any] | None = None,
) -> CallToolResult:
    """Read a length parameter and declared geometry measurements on a bound target.

    Each check supplies id, metric, entities, frame='root', units and any axis. Use
    'root / Body' or '<full occurrence path> / Body' for bodies and exact full paths
    for occurrences. Metrics include body_extent, body_volume, occurrence_translation,
    clearance and interference. Units are 'mm' for length and 'mm^3' for volume.
    Use the measured expression, value and scoped geometry to declare an edit baseline.
    Optional scope version 1 declares path and exact named section inventories
    (kind, names, fields, tolerances). Supply a prior evidence snapshot as baseline
    for strict comparison without recomputing or changing geometry.
    """
    return _tool_result(
        workspace.inspect_target(_get_adapter(), target_handle, parameter_name, checks,
                                 scope=scope, baseline=baseline)
    )


@mcp.tool()
def workspace_edit(
    workspace_path: str, contract: dict[str, Any], custom_script: str | None = None
) -> CallToolResult:
    """Guard, apply and check one numeric length user parameter edit on a bound target.

    Contract fields are operation_id (UUID), target_handle, parameter_name, expression
    (explicit length units), expected_expression, expected_value_mm, value_tolerance_mm,
    requirements and optional protected. Start with workspace_inspect check selectors.
    Add baseline, baseline_tolerance, tolerance, comparator ('eq', 'ge' or 'le') and
    expected for each requirement, including a body_extent check. Protected checks use
    the measured baseline as the post-edit expectation and omit comparator and expected.
    Read execution and verification independently. Verified evidence covers the declared checks.

    Optional custom_script defines run(_ctx) and implements the edit in trusted Python.
    Its context contains app, document, design and root. The script must apply the
    requested parameter expression.
    It runs with Fusion's Python access. Independent readback verifies the declared
    checks. The V1 build cache covers the declared parameter-edit recipe.
    """
    return _tool_result(
        workspace.edit(_get_adapter(), workspace_path, contract, custom_script=custom_script)
    )


@mcp.tool()
def workspace_checkpoint(
    workspace_path: str,
    target_handle: str,
    branch: str,
    requirements: list[dict[str, Any]],
    parameter_name: str,
    operation_id: str | None = None,
    expected_previous: str | None = None,
    accepted: bool = False,
    evidence_scope: dict[str, Any] | None = None,
) -> CallToolResult:
    """Archive a bound self-contained design as an immutable local F3D checkpoint.

    By default record a WIP checkpoint. Accepted promotion requires the declared
    checks to pass on the bound target and on a freshly reopened archive. Use
    expected_previous to guard the branch reference when publishing.
    evidence_scope requests the same strict named evidence inventory used by
    workspace_inspect and compares it after archive reopen, including mesh witnesses.
    """
    return _tool_result(
        workspace.checkpoint(
            _get_adapter(), workspace_path, target_handle, branch, requirements, parameter_name,
            operation_id=operation_id, expected_previous=expected_previous, accepted=accepted,
            evidence_scope=evidence_scope,
        )
    )


@mcp.tool()
def workspace_restore(
    workspace_path: str, branch: str, kind: str = "accepted", operation_id: str | None = None
) -> CallToolResult:
    """Verify a branch checkpoint's archive hash and open a new working document.

    kind selects 'accepted' or 'wip'. Use the returned handle for later scoped operations.
    """
    return _tool_result(
        workspace.restore(_get_adapter(), workspace_path, branch, kind=kind, operation_id=operation_id)
    )


@mcp.tool()
def workspace_build(
    workspace_path: str,
    manifest_path: str = "design.json",
    branch: str = "main",
    operation_id: str | None = None,
) -> CallToolResult:
    """Run a V1 workspace manifest or reuse a compatible verified artifact.

    Cached archives are opened as working documents and checked against the manifest.
    The V1 build cache covers the declared parameter-edit recipe.
    """
    return _tool_result(
        workspace.build(
            _get_adapter(), workspace_path, manifest_path=manifest_path,
            branch=branch, operation_id=operation_id,
        )
    )


@mcp.tool()
def workspace_status(workspace_path: str, operation_id: str | None = None) -> CallToolResult:
    """Read recorded workspace state and an optional operation receipt without a Fusion call."""
    return _tool_result(workspace.status(_get_adapter(), workspace_path, operation_id=operation_id))


@mcp.tool()
def workspace_recover(workspace_path: str, operation_id: str) -> CallToolResult:
    """Recover a late guarded edit using its completed receipt and fresh bound checks.

    An unresolved operation keeps the workspace write barrier. Recovery rechecks the
    original operation's declared requirements on its open target.
    """
    return _tool_result(workspace.recover(_get_adapter(), workspace_path, operation_id))


@mcp.tool()
def execute(script: str, durable: bool = False, operation_id: str | None = None) -> CallToolResult:
    """Custom Python whenever it fits better than a typed tool, or for missing capabilities.
    Typed tools need not be tried first. Scripts must define `def run(_ctx):`.
    Use print() for any data to return; stdout becomes the response message.
    With durable=True provide a canonical UUID operation_id; stdout and stderr are
    captured in a local receipt and receipt_stage(name, data) records progress.
    Execution and stage records do not verify geometry. Inspect before retrying.
    """
    return _tool_result(execute_tool.run(_get_adapter(), script, durable=durable, operation_id=operation_id))


_USAGE = """\
fusion-cad-mcp - MCP server for driving Autodesk Fusion

Usage:
  fusion-cad-mcp                    Run the MCP server on stdio (default)
  fusion-cad-mcp corpus build       Build the local Fusion API help corpus
                                    that find_api searches. Needs
                                    --i-accept-autodesk-terms; see
                                    `corpus build --help` for options.

The corpus is Autodesk content and is never redistributed, so each user builds
their own local cache. Everything else works without it.
"""


def main() -> None:
    """Entry point installed by pyproject as `fusion-cad-mcp`.

    Bare invocation runs the MCP server, which is what an MCP client expects.
    Subcommands are handled before that, since an MCP client never passes any.
    """
    argv = sys.argv[1:]

    if argv and argv[0] == "corpus":
        from .corpus import build

        if len(argv) < 2 or argv[1] != "build":
            print("Usage: fusion-cad-mcp corpus build [options]", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(build(argv[2:]))

    if argv and argv[0] in {"-h", "--help", "help"}:
        print(_USAGE)
        raise SystemExit(0)

    if argv:
        print(f"Unknown argument: {argv[0]}\n", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)

    global _adapter
    try:
        mcp.run()
    finally:
        if _adapter is not None:
            _adapter.close()
            _adapter = None


if __name__ == "__main__":
    main()
