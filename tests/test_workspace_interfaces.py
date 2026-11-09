"""Exercise the MCP and CLI boundaries without sending Fusion requests."""

import json
from contextlib import contextmanager
from datetime import timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from fusion_cad_mcp import server, workspace_cli
from fusion_cad_mcp.envelope import Envelope


def _unexpected_adapter(*args, **kwargs):
    pytest.fail("this request must be handled before obtaining a Fusion adapter")


@pytest.mark.asyncio
async def test_workspace_tools_register_and_initialize_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_get_adapter", _unexpected_adapter)
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert {name for name in tools if name.startswith("workspace_")} == {
            "workspace_init",
            "workspace_bind",
            "workspace_inspect",
            "workspace_edit",
            "workspace_checkpoint",
            "workspace_restore",
            "workspace_build",
            "workspace_status",
            "workspace_recover",
        }
        assert "execute" in tools
        assert tools["workspace_edit"].inputSchema["required"] == ["workspace_path", "contract"]
        assert tools["workspace_inspect"].inputSchema["properties"]["checks"]["type"] == "array"

        root = tmp_path / "work"
        reply = await client.call_tool("workspace_init", {"workspace_path": str(root)})
        assert reply.isError is False
        assert reply.structuredContent["ok"] is True
        assert json.loads(reply.content[0].text) == reply.structuredContent
        assert root.is_dir()


@pytest.mark.asyncio
async def test_workspace_edit_wire_preserves_contract_and_outcomes(monkeypatch):
    adapter = object()
    contract = {"target_handle": "opaque-handle"}
    script = "def run(_ctx):\n    pass\n"

    def edit(selected_adapter, workspace_path, request, custom_script=None):
        assert selected_adapter is adapter
        assert (workspace_path, request, custom_script) == ("workspace", contract, script)
        return Envelope(ok=False, error="workspace_check_failed",
                        result={"execution": "completed", "verification": "failed"})

    monkeypatch.setattr(server, "_get_adapter", lambda: adapter)
    monkeypatch.setattr(server.workspace, "edit", edit)
    async with create_connected_server_and_client_session(
        server.mcp, read_timeout_seconds=timedelta(seconds=5)
    ) as client:
        reply = await client.call_tool(
            "workspace_edit",
            {"workspace_path": "workspace", "contract": contract, "custom_script": script},
        )
    assert reply.isError is True
    assert reply.structuredContent["error"] == "workspace_check_failed"
    assert reply.structuredContent["result"] == {"execution": "completed", "verification": "failed"}


def test_cli_initializes_offline(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(workspace_cli, "FusionAdapter", _unexpected_adapter)
    root = tmp_path / "workspace with spaces"
    assert workspace_cli.main(["init", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert root.is_dir()


def test_cli_edit_request_passes_fields_and_closes_adapter(monkeypatch, tmp_path, capsys):
    adapter = object()
    events = []

    @contextmanager
    def connect(*, url):
        events.append(url)
        try:
            yield adapter
        finally:
            events.append("closed")

    def edit(selected_adapter, workspace_path, contract, custom_script=None):
        assert selected_adapter is adapter
        assert workspace_path == "workspace with spaces"
        assert contract == {"target_handle": "opaque-handle", "expected_value_mm": 60}
        assert custom_script == "def run(_ctx):\n    pass\n"
        return Envelope(ok=False, error="execution_unknown", result={"execution": "unknown"})

    monkeypatch.setenv("FUSION_MCP_URL", "http://localhost:31234/mcp")
    monkeypatch.setattr(workspace_cli, "FusionAdapter", connect)
    monkeypatch.setattr(workspace_cli.workspace, "edit", edit)
    request = tmp_path / "edit.json"
    request.write_text(
        json.dumps(
            {
                "workspace_path": "workspace with spaces",
                "contract": {"target_handle": "opaque-handle", "expected_value_mm": 60},
                "custom_script": "def run(_ctx):\n    pass\n",
            }
        ),
        encoding="utf-8",
    )

    assert workspace_cli.main(["edit", str(request)]) == 1
    assert events == ["http://localhost:31234/mcp", "closed"]
    assert json.loads(capsys.readouterr().out)["error"] == "execution_unknown"


@pytest.mark.parametrize(
    "source",
    [
        "{",
        "[]",
        "{}",
        '{"workspace_path":"one","workspace_path":"two","contract":{}}',
        '{"workspace_path":"one","contract":{"expected_value_mm":NaN}}',
        '{"workspace_path":"one","contract":{},"unknown_field":true}',
    ],
)
def test_cli_rejects_invalid_request_before_opening_adapter(monkeypatch, tmp_path, capsys, source):
    monkeypatch.setattr(workspace_cli, "FusionAdapter", _unexpected_adapter)
    request = tmp_path / "edit.json"
    request.write_text(source, encoding="utf-8")
    assert workspace_cli.main(["edit", str(request)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_input"


def test_cli_restore_and_build_options():
    parser = workspace_cli._parser()
    restore = parser.parse_args(["restore", "workspace", "experiment", "--kind", "wip"])
    assert (restore.workspace_path, restore.branch, restore.kind, restore.operation_id) == (
        "workspace",
        "experiment",
        "wip",
        None,
    )
    build = parser.parse_args(["build", "workspace"])
    assert (build.workspace_path, build.manifest_path, build.branch, build.operation_id) == (
        "workspace",
        "design.json",
        "main",
        None,
    )
