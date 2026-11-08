# Local Codex setup verification

Verified on 2026-11-08 against source revision
`b508782efc5d34a40d35576af0039a6a252d6947`, package 0.2.2. Environment: Windows,
Python 3.12.14, MCP SDK 1.30.0, Pydantic 2.14.3, Fusion 2706.1.17 Personal.

The repository was cloned into a separate directory and installed as a
non-editable package in a dedicated virtual environment. Codex's `fusion_cad`
registration used the absolute environment interpreter with
`-m fusion_cad_mcp.server`, the default loopback Fusion endpoint, and 30/120-second
startup/tool timeouts. The existing client settings were preserved. The companion
skill and its three references were installed and compared with the checkout.

| Check | Observed result |
| --- | --- |
| Codex registration readback | Enabled stdio server, intended interpreter/arguments/environment/timeouts |
| MCP handshake using the registered command and environment | Completed; 88 tools discovered |
| `find_tool` and `find_gotcha` | Each returned a nonempty hit using bundled documentation |
| Live `doc_state` | Active parametric design, mm units, unmodified; zero bodies, components, sketches and features |
| Live `inspect_assembly` | Occurrences, bodies and features sections returned counts of zero without unavailable statuses |
| Response contract | JSON text matched structured content for all four tool calls; no MCP error flags |
| Full automated suite with development and corpus extras | 790 passed, no skips |
| Static/dependency checks | Ruff and `pip check` passed; source checkout clean |
| Independent setup review | No blockers found |

The handshake and tool calls used an MCP client launching the exact Codex
registration. They verify the installed server and its connection to Fusion;
they are not a completed modeling field run by a new Codex agent. No design
changes were made in this setup check. Earlier live modeling evidence is in the
[capability verification report](prebenchmark-validation.md).

The optional API corpus was not built, so `find_api` was not verified with a
corpus. Its builder dependencies were installed to run the full offline suite;
that does not download Autodesk content or accept its terms.

The intermittent empty-output issue remains unresolved. Successful read-only
calls in this session do not establish a fix or a failure rate. Inspect uncertain
writes before retrying, and avoid concurrent modeling by separate agents against
the same Fusion instance. No machine-specific paths, document identities, entity
tokens or account information are included in this report.
