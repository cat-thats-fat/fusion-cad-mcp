# Local reference-component workflow

Use a local STEP file with the existing `import_geometry` tool. This workflow
adds portable mechanical meaning to imported geometry without a catalog service
or another CAD engine.

1. `create_component("Module")` creates a local component container. Retain its
   returned occurrence path.
2. `import_geometry("step", absolute_local_path, target_component=path)` imports
   geometry into it. Inspect the returned objects: some STEP files contain child
   components rather than a single body. The importer returns a source SHA-256.
3. Inspect bounds and geometry; identify the correct local frame. Supply known
   mounting points, connector frames and keep-outs to `set_reference_metadata`.
   These are user-provided mechanical requirements, not inferred facts.
4. Use `place_component` for an absolute occurrence frame, or `move_component`
   for a relative movement. `get_reference_metadata` returns the component-local
   data alongside the occurrence-to-root matrix and actual root-frame bounds.
5. Use `insert_component` to repeat an existing definition. Give each insertion
   a stable `operation_id`; duplicate retries refuse without adding another copy.
   Set `independent=True` to create an independently editable definition.
6. Keep the original model and metadata JSON together locally. In another design,
   import the same model and apply the JSON again. Metadata does not depend on
   native Fusion entity tokens or private cloud IDs.

[reference-component.json](../examples/reference-component.json) is a synthetic
format example, not a model of a particular commercial board. All positions and
keep-out boxes are in component-local millimetres. Connector X/Y axes must be
perpendicular unit vectors; their cross product defines Z. Mount axes are unit
vectors. IDs are unique across the metadata object. Each list is limited to 128
entries. An optional `source_sha256` records the import digest, but attaching it
does not authenticate a model or automatically re-check the file.

Metadata belongs to a **component definition**. Repeated occurrences share it;
their transforms remain independent. Attach metadata before creating repeated
occurrences. Writes to shared or externally referenced definitions are refused.
Reading through a particular occurrence returns that occurrence's pose and bounds.

Keep-outs are metadata boxes, not hidden solids or constraints. Construct an
explicit local clearance solid when exact B-rep interference is required, then
use `validate_design`. Its bounds check does not establish containment inside an
enclosure cavity, and this workflow does not automatically align holes or route
wiring. Raw Python remains available for specialized reference processing.
