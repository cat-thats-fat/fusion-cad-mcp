"""Command-line entry point for local Fusion workspaces."""

from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path
from typing import Any

from . import workspace
from .adapter import DEFAULT_URL, FusionAdapter
from .envelope import Envelope

_MAX_REQUEST_BYTES = 1024 * 1024
_OPERATIONS = {"init": "initialize", "inspect": "inspect_target"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-cad-workspace",
        description="Manage local Fusion workspace builds, evidence and F3D checkpoints.",
        epilog="Use an absolute host path for WORKSPACE and request workspace_path fields.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="initialize local workspace storage")
    initialize.add_argument("workspace_path", metavar="WORKSPACE")

    bind = commands.add_parser("bind", help="get a handle to an open Fusion document")
    bind.add_argument("--document-name", help="unique exact name, defaults to the active document")

    requests = {
        "inspect": (
            "measure a bound document",
            "target_handle, parameter_name and checks",
        ),
        "edit": (
            "apply and check a guarded edit",
            "workspace_path, contract and optional custom_script",
        ),
        "checkpoint": (
            "record a local F3D checkpoint",
            "workspace_path, target_handle, branch, requirements, parameter_name, and optional "
            "operation_id, expected_previous and accepted",
        ),
    }
    for name, (help_text, fields) in requests.items():
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "request_file", metavar="REQUEST.json", help=f"JSON object with {fields}"
        )

    restore = commands.add_parser("restore", help="open a fresh copy of a branch checkpoint")
    restore.add_argument("workspace_path", metavar="WORKSPACE")
    restore.add_argument("branch", metavar="BRANCH")
    restore.add_argument("--kind", choices=("accepted", "wip"), default="accepted")
    restore.add_argument("--operation-id")

    build = commands.add_parser(
        "build", help="build a workspace manifest or reuse a verified artifact"
    )
    build.add_argument("workspace_path", metavar="WORKSPACE")
    build.add_argument("--manifest-path", default="design.json")
    build.add_argument("--branch", default="main")
    build.add_argument("--operation-id")

    status = commands.add_parser("status", help="read recorded workspace and receipt state")
    status.add_argument("workspace_path", metavar="WORKSPACE")
    status.add_argument("--operation-id")

    recover = commands.add_parser("recover", help="check a confirmed late operation result")
    recover.add_argument("workspace_path", metavar="WORKSPACE")
    recover.add_argument("operation_id", metavar="OPERATION_ID")
    return parser


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate request field {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"invalid JSON number {value}")


def _read_request(path: str) -> dict[str, Any]:
    with Path(path).open("rb") as source:
        data = source.read(_MAX_REQUEST_BYTES + 1)
    if len(data) > _MAX_REQUEST_BYTES:
        raise ValueError("request JSON exceeds 1 MiB")
    request = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(request, dict):
        raise ValueError("request JSON must be an object")
    return request


def main(argv: list[str] | None = None) -> int:
    """Run one workspace command and print its result envelope as JSON."""
    arguments = vars(_parser().parse_args(argv))
    command = arguments.pop("command")
    request_file = arguments.pop("request_file", None)
    operation = getattr(workspace, _OPERATIONS.get(command, command))
    try:
        request = _read_request(request_file) if request_file is not None else arguments
        positional = () if command == "init" else (None,)
        inspect.signature(operation).bind(*positional, **request)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        envelope = Envelope(ok=False, error="invalid_input", message=str(exc)[:240])
    else:
        if command == "init":
            envelope = operation(**request)
        else:
            with FusionAdapter(url=os.environ.get("FUSION_MCP_URL", DEFAULT_URL)) as adapter:
                envelope = operation(adapter, **request)
    print(json.dumps(envelope.to_dict(), allow_nan=False))
    return 0 if envelope.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
