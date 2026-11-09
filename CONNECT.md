# Connecting fusion-cad-mcp to an MCP client

Three steps: turn on Fusion's API server, install this package, point your client at it.

If you would rather have an AI do it with you, [AI-SETUP-PROMPT.md](AI-SETUP-PROMPT.md) is a paste-one-prompt version of this page.

## 1. Enable Fusion's MCP server

In Fusion: **Preferences > General > API > Fusion MCP Server**, enable it.

This is what actually talks to Fusion. `fusion-cad-mcp` connects to it on `127.0.0.1:27182`, so it must be on or nothing works.

Check the route on macOS or Linux:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:27182/mcp
```

On Windows PowerShell, use `curl.exe` explicitly to avoid the Windows PowerShell
5.1 `curl` alias:

```powershell
curl.exe -s -o NUL -w "%{http_code}\n" http://127.0.0.1:27182/mcp
```

`405` indicates that the route rejected this GET request; it does not prove that
MCP initialization or Python execution works. `404` means the expected route was
not found. Connection refused means nothing accepted the connection at that
address. Confirm Fusion is running and its MCP server is enabled, then perform
the tool checks below.

## 2. Install

```bash
pip install "fusion-cad-mcp @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
```

Check it:

```bash
fusion-cad-mcp --help
```

Note the path it installed to, since some clients need an absolute one:

```bash
# macOS / Linux
which fusion-cad-mcp
# Windows PowerShell
(Get-Command fusion-cad-mcp).Source
```

## 3. Point your client at it

### Codex

For a fresh Windows checkout, use the following instead of the global install in
step 2. Run it from the directory where you want to keep the repository. Python
3.11+ and Git must already be installed; `py -3` must select a supported Python.

```powershell
git clone https://github.com/cat-thats-fat/fusion-cad-mcp.git
Set-Location fusion-cad-mcp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$fusionPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
codex mcp add fusion_cad --env FUSION_MCP_URL=http://127.0.0.1:27182/mcp --env PYTHONUNBUFFERED=1 -- $fusionPython -m fusion_cad_mcp.server
codex mcp get fusion_cad
```

This installs a fixed snapshot of the checkout. After deliberately updating the
checkout, run the same `pip install .` command again and reconnect the MCP server.
The absolute interpreter path avoids dependence on shell activation or PATH.
On macOS, create the environment with `python3 -m venv .venv` and use the absolute
path to `.venv/bin/python` instead; the live setup report below covers Windows.

Codex stores this registration in `~/.codex/config.toml`. Edit the existing
`fusion_cad` table to set the timeouts below; do not add a second table with the
same name. Replace the example command path with your actual interpreter path.

```toml
[mcp_servers.fusion_cad]
command = "C:/path/to/fusion-cad-mcp/.venv/Scripts/python.exe"
args = ["-m", "fusion_cad_mcp.server"]
startup_timeout_sec = 30
tool_timeout_sec = 120

[mcp_servers.fusion_cad.env]
FUSION_MCP_URL = "http://127.0.0.1:27182/mcp"
PYTHONUNBUFFERED = "1"
```

The adapter's default transport deadline is 60 seconds per request. The longer
client timeout leaves room for initialization, error reporting and cleanup; it
does not increase Fusion's execution budget or prove cancellation. See OpenAI's
[MCP configuration documentation](https://developers.openai.com/codex/mcp) for
the current configuration options.

Install the companion skill from the same checkout:

```powershell
.\.venv\Scripts\python.exe install_skill.py --skills-dir (Join-Path $env:USERPROFILE '.codex/skills')
```

If you use a custom `CODEX_HOME`, select its `skills` directory instead. This
copies `SKILL.md` and all three reference files; copying `SKILL.md` alone leaves
its references missing. Refresh the skill with the same command after updating.

Start a new Codex chat and select the `fusion-cad` skill. If the server is not
listed, reconnect or restart the client. `codex mcp get fusion_cad` confirms the
saved configuration, not a live connection. Verify through the tools:

1. Discover the server's tools (95 in version 0.3.0); call `get_capabilities` for the offline capability summary.
2. Call `find_tool` for `create_parametric_enclosure`; confirm a nonempty hit.
3. Call `doc_state`; confirm `result.active_design` is true and inspect the
   document's units, dirty flag and entity counts.
4. Call `inspect_assembly` with bounded results; check each returned section for
   counts or an explicit unavailable status.

These are read-only connection checks, not a modeling benchmark. The
[verification report](docs/codex-setup-validation.md) records the tested version
and results. If Autodesk's own MCP is also registered, both servers control the
same Fusion instance; avoid concurrent model edits from separate agents.

### Claude Code

```bash
claude mcp add fusion -- fusion-cad-mcp
```

Or edit the config directly. User scope, `~/.claude.json`, loads in every session:

```json
{
  "mcpServers": {
    "fusion": {
      "type": "stdio",
      "command": "fusion-cad-mcp"
    }
  }
}
```

Project scope, `.mcp.json` in a repo root, loads only in that repo. Use that if you only do CAD work in one place.

Verify with `/mcp` inside Claude Code. You should see `fusion` connected with its named tools.

### Claude Desktop

Edit the config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "fusion": {
      "command": "fusion-cad-mcp"
    }
  }
}
```

Restart Claude Desktop. Desktop does not always resolve commands from PATH, so if it fails to start, use the absolute path from step 2.

### Cursor, Cline, and other MCP clients

Any client speaking stdio MCP works. The server is `fusion-cad-mcp` with no arguments.

### Running from a checkout instead of a wheel

```json
{
  "mcpServers": {
    "fusion": {
      "command": "python",
      "args": ["-m", "fusion_cad_mcp.server"],
      "env": { "PYTHONPATH": "/absolute/path/to/server/src" }
    }
  }
}
```

## Optional: the API documentation corpus

`find_api` searches Autodesk's Fusion API help. That is Autodesk's content and is not redistributed, so you build a local copy once:

```bash
pip install "fusion-cad-mcp[corpus] @ git+https://github.com/cat-thats-fat/fusion-cad-mcp.git"
fusion-cad-mcp corpus build --i-accept-autodesk-terms
```

It crawls at 1 request/second and takes a while. Output goes to `~/.fusion-cad/corpus/`, which is the first place the server looks. Override with `FUSION_CAD_CORPUS_DIR`.

The other tools work without it. Only `find_api` returns `corpus_not_built` until you do this. Installing the `corpus` extra alone does not build the corpus.

## Troubleshooting

**Every call fails with a connection error.** Fusion is not running, or the API server is off. Check step 1.

**Everything worked, then calls started failing after restarting Fusion.** The adapter re-handshakes on a rejected session. If errors persist, confirm the listener and inspect the error; reconnect the MCP server in your client if needed. Repeated failures alone do not identify which layer is at fault.

**Expected script output is empty, or a call times out.** Intermittent empty successful replies were observed from Autodesk's MCP on Fusion 2706.1.17, including read-only print probes. The cause is unresolved. A write may have completed even if its result was lost: retain its operation ID, query `get_operation_status`, and inspect the design before deciding what happened. Instrumented writes can be blocked until `reconcile_operation` records explicit acknowledgement and evidence; reconciliation does not prove success, rollback or that timed-out execution stopped. Raw `execute` has no receipt. Restarting Fusion restored output in recorded runs, but is not a proven fix; protect unsaved work before restarting. An intentionally silent raw `execute` script can legitimately return empty output.

**`find_api` returns `corpus_not_built`.** Expected until you build the corpus. See above.

**`find_tool` or `find_gotcha` returns `*_not_found`.** The packaged markdown is missing, which should not happen from a wheel. Reinstall.

**A tool returns `ok: true` but nothing changed in Fusion.** Not always a bug. Fusion declines some operations silently and the tools surface it: check `applied` on `drive_joint`, `moved` on `move_component`, `bytes_written` on `export`. If a pattern or mirror looks wrong, count bodies rather than trusting positions, which can be correct while the count is not.

**A tool errors with something Fusion-specific.** Try `find_gotcha` with the symptom. The catalog covers the failure modes found during live testing, including several that report success while doing the wrong thing.
