# Modernization validation

This is the historical PR #1 report for baseline `7fed4e6`. The subsequent
capability upgrade has its own [verification report](prebenchmark-validation.md)
and [test review](prebenchmark-test-review.md); counts and transport limits below
describe the earlier revision.

Validated on Windows with Python 3.12 and Autodesk Fusion 2706.1.17. Earlier
checks also exercised 2705.1.30 before updating Fusion. No user design was saved
or changed; the live harness creates and closes its own unsaved fixture.

## Evidence

| Check | Result |
| --- | --- |
| Offline suite | 584 passed, no skips |
| Test audit | All 388 baseline and 371 current test functions accounted for; [individual decisions](../tests/audit/README.md) |
| Static checks | Ruff and `git diff --check` passed |
| Installed package | Built wheel installed into a temporary target; all three bundled knowledge searches passed with checkout fallback disabled |
| Live Fusion | 30 passed, 4 blocked; [sanitized report](live-regression-2706.json) |
| Session recovery | A terminated local MCP session returned HTTP 404; the production adapter renegotiated and rediscovered the same four tools |
| Independent review | Separate geometry/assembly and transport/IO reviews; follow-up review of fixes completed before push |

The live checks measure actual B-rep dimensions and volumes, occurrence frames,
proxy-handle distances, body-specific holes, rotations, interference, nested
sketch authoring, linked projection, mesh ray intersections, joint limits,
parameter propagation, rib geometry, file contents, and timeline restoration
after an injected screenshot timeout. All 36 inspected features/sketches were
healthy in the completed fixture. Test reports contain no account information,
file-system paths, entity tokens, or session identifiers.

The rib check illustrates why API-call success alone is insufficient. A 2 mm rib
on an L bracket must add 80 mm³ to its 1,520 mm³ base. The verified flipped
direction produced 1,600 mm³ and a healthy feature. The opposite direction
returned a warning and unexpected geometry; the wrapper now reports that state
as an error even when Fusion supplies an empty warning message.

A local HTTP server exercises stalled SSE heartbeats, incomplete SSE data,
silent reads, JSON data drips, and resumed SSE streams. It checks timeout,
cancellation for the original request, no replay, lock release, adapter reuse,
and session cleanup. Four of the five cases fail against the original
event-only deadline check; the silent-read case verifies the existing HTTP
timeout. These are transport tests, separate from the Fusion geometry checks.

A separate live probe exposed a grounding regression: pinning a component at
20 mm preserved its position, but subsequently grounding it to its parent
reset it to 0 mm while both flags remained true. `ground_component` now uses
Pin alone and verifies the resulting pose. The live fixture pins a board away
from its insertion position and checks both its transform and body bounds.

## Limits

- Response deadlines are checked before and after raw body chunks, before SSE
  parsing or JSON buffering can absorb them. A socket read already in progress
  can overrun the deadline by up to its configured HTTP inactivity timeout;
  this is not a hard wall-clock timeout for HTTP headers or framing.
- The installed Fusion capability refused IGES and SAT export. Their paired
  import checks lacked a fixture and are blocked, not passed. Option construction,
  returned-object handling, and failure paths are covered by API doubles.
- The 2705.1.30 build lacked writable rib APIs; its capability refusal was
  checked live. The 2706.1.17 build supports the tested rib path.
- Child-component hole authoring and automatic delete-and-recreate repair are
  explicitly refused. Shared component edits require a separate supported
  workflow rather than silently changing every instance.
- Pinning an occurrence already grounded to its parent is refused, including
  legacy states with both flags set. Explicitly unground it before pinning its
  current position.
- Cloud document opening and saving were not exercised against personal cloud
  files. Their dispatch, exact-match resolution, and refusal paths have offline
  contract tests. Local `save_as(path)` is explicitly unsupported.
- Legacy unscoped handles remain accepted for compatibility. Scoped handles
  reject known document/occurrence mismatches, but document creation IDs alone
  are not a universal identity guarantee across copied documents and sessions.
- Historical recipes are not all revalidated against the current build. This
  work improves modeling and verification foundations; a declarative multi-PCB
  enclosure planner, connector keep-outs, cable routing, and manufacturing-rule
  validation remain separate modernization work.

## Reproduce

```text
python -m pip install -e ".[dev,corpus]" build hatchling
python -m pytest
python -m ruff check src tests scripts
python -m build --wheel --no-isolation
python tests/wheel_knowledge_smoke.py dist/fusion_cad_mcp-0.2.2-py3-none-any.whl
python scripts/live_regression.py --run --report live-regression.json
```

The final command requires a running Fusion instance with its local MCP server.
Use `--keep-open` only when the disposable fixture should remain for inspection.

## Primary API references

- [Assembly contexts and proxies](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/ComponentsProxies_UM.htm)
- [Hole position and natural direction](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/HoleFeatureInput_setPositionByPoint.htm)
- [Rib input and direction](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_RibFeatureInput.htm)
- [Feature health states](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_FeatureHealthStates.htm)
- [Joint point geometry](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_JointGeometry_createByPoint.htm)
- [Occurrence Pin API](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Occurrence_isGrounded.htm)
- [Ground To Parent and Pin behavior](https://help.autodesk.com/cloudhelp/ENU/Fusion-Assemble/files/ASM-GROUND-TO-PARENT.htm)
- [Mesh ray return values](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/fusion_Component_findMeshUsingRay.htm)
- [Imported object identities](https://help.autodesk.com/cloudhelp/ENU/Fusion-360-API/files/core_ImportManager_importToTarget2.htm)
- [MCP Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [HTTPX per-chunk timeout semantics](https://www.python-httpx.org/advanced/timeouts/)
- [httpx-sse comment and partial-line decoding](https://github.com/florimondmanca/httpx-sse/blob/master/src/httpx_sse/_decoders.py)
