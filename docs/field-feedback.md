# Initial field feedback and follow-up

Received 2026-11-08. The observations below were supplied by an agent using the
local MCP installation. They are a report of that pass, not a benchmark score or
an independently repeated modeling test.

| Reported observation | What it establishes |
| --- | --- |
| Typed document, assembly and bounding-box inspection worked | Those read-only workflows were usable in the reported design. |
| A guessed body name failed without modifying anything | A lookup refusal was observed; this is not exhaustive ambiguous/stale-reference testing. |
| `find_api` reported no corpus; Autodesk's read tool supplied API help | The optional local corpus was absent; the separate Autodesk documentation route was usable. |
| `find_tool` returned outdated import prose | The bundled import reference needed correction. |
| A large assembly response repeated verbose metadata | Inspection output needed a duplication audit; the field response's exact size was not supplied. |
| Raw `execute` opened a local F3D in a separate document, aggregated bounds to JSON and saved a high-resolution viewport image | The escape hatch supported these reported workflows. It does not establish typed import, export or redesign reliability. |

Detailed redesign modeling and export reliability were **not exercised** in this
pass. They remain unscored by this report; see the separate, scoped
[earlier live fixtures](prebenchmark-validation.md) for prior evidence.

## Corrections

The import reference now includes `target_component`, active-design scope,
accepted selectors, direct target counts, identity/frame metadata and refusal
conditions. Opening an F3D as a separate document is distinguished from importing
into the active design. A search regression check verifies that the bounded
`find_tool` snippet contains that distinction, using packaged documentation rather
than a user's local override.

Inspection parsed its JSON stdout into `result` while retaining the same JSON in
`message`. Successful `inspect_assembly`, `inspect_feature` and `query_geometry`
now leave `message` empty and preserve the entire `result`, including handles and
unknown nested fields. **Clients that read inspection JSON from `message` must
read `result` instead.** Failed, malformed and missing replies retain their
diagnostics; raw `execute` stdout is unchanged.

Both MCP text and structured content remain available for compatibility, as
recommended by the [MCP structured-content specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#structured-content).
Per-occurrence paths, transforms and flags remain intact. Pagination and selected
`include` sections are still needed for large assemblies; the limit is per section.

## Verification and limits

- Full offline suite: **798 passed**. Ruff and `git diff --check` passed.
- Wheel build and installed-wheel knowledge searches passed.
- Registered-tool wire tests cover all three inspection tools, result preservation,
  text/structured equivalence, read-only dispatch, failure diagnostics and raw
  execution compatibility. These are serialization checks, not geometry tests.
- A synthetic 50-occurrence response measured **142,056 → 65,634 UTF-8 bytes**
  when serialized as a complete `CallToolResult` (53.8% less). The result data was
  identical. This measures the duplicate removal for one synthetic payload, not
  the reported assembly, model tokens, transport latency or CAD performance.
- Independent implementation and import-guidance reviews completed.
- A live read-only assembly probe returned `inspect_assembly_parse_failed` with
  no stdout. A separate direct Autodesk print probe also returned
  `success: true` with an empty message. This again reproduces the upstream
  missing-stdout symptom, but does not establish its cause. The response-size correction is
  **not a fix for missing stdout**; successful live validation of the new
  inspection path was blocked. Fusion was not restarted and no open document
  was modified during these follow-up probes.

No user design names, paths, entity tokens or account details are published here.
