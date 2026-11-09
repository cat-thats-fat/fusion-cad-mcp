"""Crash-safe local receipt contracts using in-process doubles and subprocesses."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import types
import uuid

import pytest

from fusion_cad_mcp import receipts as r
from fusion_cad_mcp.envelope import Envelope

SCRIPT = """import json
def run(_ctx):
    print(json.dumps({'ok': True, 'value': 42}))
"""
FAIL = """import json
def run(_ctx):
    print(json.dumps({'ok': False, 'error': 'feature_failed', 'model_may_have_changed': True}))
"""


def mock_send(monkeypatch, *, lose=False, doc="doc-1"):
    app = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    app.core = core
    core.Application = types.SimpleNamespace(
        get=lambda: types.SimpleNamespace(activeDocument=types.SimpleNamespace(creationId=doc))
    )
    monkeypatch.setitem(sys.modules, "adsk", app)
    monkeypatch.setitem(sys.modules, "adsk.core", core)
    calls = []

    def send(script, *, read_only=False):
        calls.append(read_only)
        ns = {}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(script, ns)
            ns["run"](None)
        return Envelope(ok=True, message="" if lose else out.getvalue())

    return send, calls


def test_missing_stdout_recovers_exact_declared_result_and_duplicate(monkeypatch, tmp_path):
    send, calls = mock_send(monkeypatch, lose=True)
    op = str(uuid.uuid4())
    env = r.run_typed(send, SCRIPT, "local-endpoint", 1, op, journal_dir=tmp_path)
    assert env.ok and json.loads(env.message) == {"ok": True, "value": 42}
    assert env.state["recovered_from_receipt"] is True
    assert env.state["document_creation_id"] == "doc-1"
    assert "current document state is not established" in env.state["receipt_evidence"]
    status = r.get_status(op, journal_dir=tmp_path).result
    assert status["status"] == "completed" and status["document_creation_id"] == "doc-1"
    assert status["declared_result"]["value"] == 42
    again = r.run_typed(send, SCRIPT, "local-endpoint", 1, op, journal_dir=tmp_path)
    assert again.ok and calls == [False]
    assert again.state["document_creation_id"] == "doc-1"
    assert again.state["recovered_from_receipt"] is True
    conflict = r.run_typed(
        send, SCRIPT + "\n# changed", "local-endpoint", 1, op, journal_dir=tmp_path
    )
    assert conflict.error == "receipt_conflict" and calls == [False]


def test_declared_failure_stays_blocked_until_explicit_matching_reconciliation(
    monkeypatch, tmp_path
):
    send, calls = mock_send(monkeypatch)
    op = str(uuid.uuid4())
    result = r.run_typed(send, FAIL, "endpoint", 1, op, journal_dir=tmp_path)
    assert result.ok is False and result.state["retry_safe"] is False
    assert (
        r.get_status(op, journal_dir=tmp_path).result["status"] == "declared_failure_unreconciled"
    )
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).error == "receipt_barrier"
    probe_id = str(uuid.uuid4())
    assert r.run_typed(
        send, SCRIPT, "endpoint", 1, probe_id, read_only=True, journal_dir=tmp_path
    ).ok
    refused = r.reconcile(
        op,
        endpoint="endpoint",
        expected_document="other",
        probe_operation_id=probe_id,
        acknowledgement=True,
        evidence="inspected",
        journal_dir=tmp_path,
    )
    assert not refused.ok
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).error == "receipt_barrier"
    accepted = r.reconcile(
        op,
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=probe_id,
        acknowledgement=True,
        evidence="inspected state",
        journal_dir=tmp_path,
    )
    assert accepted.ok and "not established" in accepted.result["meaning"]
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).ok
    assert calls == [False, True, False]


def test_readonly_receipt_works_during_unresolved_barrier(monkeypatch, tmp_path):
    send, calls = mock_send(monkeypatch)
    op = str(uuid.uuid4())
    r.run_typed(send, FAIL, "endpoint", 1, op, journal_dir=tmp_path)
    read_id = str(uuid.uuid4())
    got = r.run_typed(send, SCRIPT, "endpoint", 1, read_id, read_only=True, journal_dir=tmp_path)
    assert got.ok and calls == [False, True]
    assert r.get_status(read_id, journal_dir=tmp_path).result["document_creation_id"] == "doc-1"
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).error == "receipt_barrier"


def test_missing_terminal_never_claims_success_or_replays(tmp_path):
    calls = []

    def send(script, *, read_only=False):
        calls.append(1)
        return Envelope(ok=True, message='{"ok":true}')

    op = str(uuid.uuid4())
    out = r.run_typed(send, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path)
    assert not out.ok and out.error == "receipt_unconfirmed"
    assert out.state["operation_id"] == op and out.state["execution_outcome"] == "unknown"
    assert (
        r.run_typed(send, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path).error
        == "receipt_unresolved"
    )
    assert calls == [1]
    no_doc = r.reconcile(
        op,
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=str(uuid.uuid4()),
        acknowledgement=True,
        evidence="inspected",
        journal_dir=tmp_path,
    )
    assert not no_doc.ok


def test_crashed_process_leaves_durable_write_barrier(tmp_path):
    op = str(uuid.uuid4())
    code = """import os,sys
from fusion_cad_mcp.receipts import run_typed
s = "import json\\ndef run(_ctx):\\n print(json.dumps({'ok':True}))\\n"
def send(script,read_only=False): os._exit(27)
run_typed(send,s,'endpoint',1,sys.argv[1],journal_dir=sys.argv[2])
"""
    child = subprocess.run(
        [sys.executable, "-c", code, op, str(tmp_path)], env=os.environ.copy(), check=False
    )
    assert child.returncode == 27
    assert r.get_status(op, journal_dir=tmp_path).result["status"] == "unknown"
    assert (
        r.run_typed(
            lambda *a, **k: pytest.fail("must not dispatch"),
            SCRIPT,
            "endpoint",
            1,
            journal_dir=tmp_path,
        ).error
        == "receipt_barrier"
    )


def test_endpoint_lock_is_bounded_and_prevents_parallel_dispatch(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    send, calls = mock_send(monkeypatch)

    def slow(script, *, read_only=False):
        entered.set()
        assert release.wait(3)
        return send(script, read_only=read_only)

    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            r.run_typed(slow, SCRIPT, "endpoint", 1, journal_dir=tmp_path)
        )
    )
    worker.start()
    assert entered.wait(2)
    try:
        blocked = r.run_typed(
            lambda *a, **k: pytest.fail("must not dispatch"),
            SCRIPT,
            "endpoint",
            0.05,
            journal_dir=tmp_path,
        )
        assert (
            blocked.error == "receipt_busy" and blocked.state["execution_outcome"] == "not_started"
        )
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and results[0].ok and calls == [False]


@pytest.mark.parametrize(
    "script",
    ["def run(c): print(42)", "def run(c): pass", 'def run(c): print(json.dumps(1), end="")'],
)
def test_unsupported_output_refused_before_send(tmp_path, script):
    out = r.run_typed(
        lambda *a, **k: pytest.fail("must not dispatch"),
        script,
        "endpoint",
        1,
        journal_dir=tmp_path,
    )
    assert out.error == "receipt_invalid_input"
    assert list(tmp_path.iterdir()) == []


def test_corrupt_record_blocks_next_write(tmp_path):
    op = str(uuid.uuid4())
    (tmp_path / (op + ".json")).write_text("{broken", encoding="utf-8")
    out = r.run_typed(
        lambda *a, **k: pytest.fail("must not dispatch"),
        SCRIPT,
        "endpoint",
        1,
        journal_dir=tmp_path,
    )
    assert out.error == "receipt_unavailable"


def test_terminal_receipt_wins_over_timeout_and_recovers_failure(monkeypatch, tmp_path):
    send, calls = mock_send(monkeypatch)

    def timeout_after_completion(script, *, read_only=False):
        send(script, read_only=read_only)
        return Envelope(ok=False, error="transport_timeout", state={"execution_outcome": "unknown"})

    ok_id = str(uuid.uuid4())
    success = r.run_typed(
        timeout_after_completion, SCRIPT, "endpoint", 1, ok_id, journal_dir=tmp_path
    )
    assert success.ok and json.loads(success.message)["value"] == 42
    assert success.state["recovered_from_receipt"] is True
    fail_id = str(uuid.uuid4())
    failure = r.run_typed(
        timeout_after_completion, FAIL, "endpoint", 1, fail_id, journal_dir=tmp_path
    )
    assert not failure.ok and failure.error == "feature_failed"
    assert failure.result["model_may_have_changed"] is True
    assert failure.state["operation_id"] == fail_id and failure.state["retry_safe"] is False
    again = r.run_typed(
        timeout_after_completion, FAIL, "endpoint", 1, fail_id, journal_dir=tmp_path
    )
    assert again.error == "feature_failed" and len(calls) == 2
    blocked = r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path)
    assert blocked.error == "receipt_barrier" and blocked.state["barrier_operation_id"] == fail_id
    assert (
        blocked.state["execution_outcome"] == "not_started" and blocked.state["retry_safe"] is False
    )


def test_confirmed_transport_not_started_does_not_leave_barrier(monkeypatch, tmp_path):
    op = str(uuid.uuid4())

    def rejected(script, *, read_only=False):
        return Envelope(ok=False, error="invalid_input", state={"execution_outcome": "not_started"})

    out = r.run_typed(rejected, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path)
    assert out.error == "invalid_input" and out.state["operation_id"] == op
    assert r.get_status(op, journal_dir=tmp_path).result["status"] == "not_started"
    send, _ = mock_send(monkeypatch)
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).ok
    assert (
        r.run_typed(rejected, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path).error
        == "receipt_not_started"
    )


def test_exception_is_reraised_by_script_and_journal_stays_unknown(monkeypatch, tmp_path):
    send, calls = mock_send(monkeypatch)
    script = """import json
def run(_ctx):
    raise RuntimeError('private error')
    print(json.dumps({'ok': True}))
"""
    op = str(uuid.uuid4())
    out = r.run_typed(send, script, "endpoint", 1, op, journal_dir=tmp_path)
    assert not out.ok and out.error == "receipt_dispatch_unknown"
    assert out.state["operation_id"] == op and out.state["retry_safe"] is False
    assert r.get_status(op, journal_dir=tmp_path).result["status"] == "unknown"
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).error == "receipt_barrier"
    assert calls == [False]


def test_reconcile_needs_fresh_receipted_same_document_probe(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    op, probe = str(uuid.uuid4()), str(uuid.uuid4())
    r.run_typed(send, FAIL, "endpoint", 1, op, journal_dir=tmp_path)
    no_probe = r.reconcile(
        op,
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=probe,
        acknowledgement=True,
        evidence="checked",
        journal_dir=tmp_path,
    )
    assert not no_probe.ok
    r.run_typed(send, SCRIPT, "endpoint", 1, probe, read_only=True, journal_dir=tmp_path)
    no_ack = r.reconcile(
        op,
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=probe,
        acknowledgement=False,
        evidence="checked",
        journal_dir=tmp_path,
    )
    assert not no_ack.ok
    record = tmp_path / (probe + ".json")
    obj = json.loads(record.read_text())
    obj["created_at"] -= 1000
    record.write_text(json.dumps(obj), encoding="utf-8")
    expired = r.reconcile(
        op,
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=probe,
        acknowledgement=True,
        evidence="checked",
        journal_dir=tmp_path,
    )
    assert (
        not expired.ok
        and r.get_status(op, journal_dir=tmp_path).result["status"]
        == "declared_failure_unreconciled"
    )


def test_serialized_read_only_waits_for_active_call(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    send, calls = mock_send(monkeypatch)

    def slow(script, *, read_only=False):
        entered.set()
        release.wait(2)
        return send(script, read_only=read_only)

    worker = threading.Thread(
        target=lambda: r.run_typed(slow, SCRIPT, "endpoint", 1, journal_dir=tmp_path)
    )
    worker.start()
    assert entered.wait(2)
    try:
        read = r.run_typed(send, SCRIPT, "endpoint", 0.05, read_only=True, journal_dir=tmp_path)
        assert read.error == "receipt_busy" and calls == []
    finally:
        release.set()
        worker.join(3)
    assert calls == [False]


def test_terminal_persists_before_output_failure(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    op = str(uuid.uuid4())

    def broken_output(script, *, read_only=False):
        def fail(*args, **kwargs):
            raise OSError("output stream unavailable")

        ns = {"print": fail}
        exec(script, ns)
        ns["run"](None)
        pytest.fail("print should fail")

    out = r.run_typed(broken_output, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path)
    assert out.ok and out.state["recovered_from_receipt"] is True
    assert json.loads(out.message) == {"ok": True, "value": 42}
    assert r.get_status(op, journal_dir=tmp_path).result["status"] == "completed"
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).ok


def test_real_generated_typed_scripts_are_instrumentable(tmp_path):
    from fusion_cad_mcp.tools import io as io_tools
    from fusion_cad_mcp.tools import parameters, placement, validation

    scripts = [
        parameters.build_update("width", "20 mm"),
        parameters.build_add([{"name": "width", "expression": "20 mm"}]),
        placement.build_create_component("Part"),
        io_tools.build_export("step", None, "C:/fixture.step"),
        validation.build_validate_design(
            [{"type": "bounds", "body": "b", "min_mm": [0, 0, 0], "max_mm": [1, 1, 1]}]
        ),
    ]
    for script in scripts:
        output = r._instrument(script, tmp_path, str(uuid.uuid4()), "a" * 64, "b" * 64)
        compile(output, "<generated>", "exec")


def test_os_lock_excludes_other_process(tmp_path):
    ready = tmp_path / "ready"
    code = """import sys,time
from pathlib import Path
from fusion_cad_mcp.receipts import _endpoint_lock,_hash
with _endpoint_lock(Path(sys.argv[1]),_hash('endpoint'),1):
 Path(sys.argv[2]).write_text('ready')
 time.sleep(1.5)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), str(ready)], env=os.environ.copy()
    )
    try:
        import time

        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        out = r.run_typed(
            lambda *a, **k: pytest.fail("must not dispatch"),
            SCRIPT,
            "endpoint",
            0.1,
            journal_dir=tmp_path,
        )
        assert out.error == "receipt_busy"
    finally:
        process.wait(timeout=5)
    assert process.returncode == 0


def test_identical_script_dispatch_twice_has_one_effect(monkeypatch, tmp_path):
    mock_send(monkeypatch)
    calls = []
    script = """import json
values = []
def run(_ctx):
    values.append(1)
    print(json.dumps({'ok':True, 'count':len(values)}))
"""

    def duplicate(instrumented, *, read_only=False):
        ns = {}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(instrumented, ns)
            ns["run"](None)
            ns["run"](None)
        calls.append(list(ns["values"]))
        return Envelope(ok=True, message=output.getvalue())

    out = r.run_typed(duplicate, script, "endpoint", 1, journal_dir=tmp_path)
    assert out.ok and calls == [[1]]
    assert json.loads(out.message)["count"] == 1


def test_receipts_preserve_shadowed_print_and_single_status(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    script = """import json
def nested_capture(errors):
    def print(value): errors.append(json.loads(value))
    print(json.dumps({'ok': False, 'error':'inner'}))
def run(_ctx):
    errors = []
    nested_capture(errors)
    print(json.dumps({'ok': True, 'errors':errors}))
"""
    out = r.run_typed(send, script, "endpoint", 1, journal_dir=tmp_path)
    assert out.ok and json.loads(out.message)["errors"] == [{"ok": False, "error": "inner"}]


def test_invalid_scoped_placement_handle_produces_one_declared_error(monkeypatch, tmp_path):
    from fusion_cad_mcp.tools import placement
    from tests._assembly_fakes import Collection
    from tests.test_placement import Adapter

    adapter = Adapter(monkeypatch)
    adapter.app.documents = Collection([adapter.document])
    adapter.design.findEntityByToken = lambda token: []
    handle = "occurrence:Board:1|doc=646f63:missing"
    script = placement.build_place_component(handle, [0, 0, 0])
    out = r.run_typed(
        lambda script, read_only=False: adapter.execute_script(script),
        script,
        "endpoint",
        1,
        journal_dir=tmp_path,
    )
    assert not out.ok and out.error == "handle_invalid"
    assert out.result["detail"]["reason"] == "not_found_in_active_doc"
    assert out.state["receipt_status"] == "declared_failure_unreconciled"


def test_missing_original_identity_requires_explicit_weaker_override(monkeypatch, tmp_path):
    op = str(uuid.uuid4())

    def lost(script, *, read_only=False):
        return Envelope(ok=False, error="timeout")

    first = r.run_typed(lost, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path)
    assert not first.ok
    send, _ = mock_send(monkeypatch)
    probe = str(uuid.uuid4())
    assert r.run_typed(send, SCRIPT, "endpoint", 1, probe, read_only=True, journal_dir=tmp_path).ok
    args = dict(
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=probe,
        acknowledgement=True,
        evidence="confirmed prior run ended",
        journal_dir=tmp_path,
    )
    assert not r.reconcile(op, **args).ok
    result = r.reconcile(op, allow_missing_identity=True, **args)
    assert result.ok and result.result["original_document_identity_unavailable"] is True
    assert r.get_status(op, journal_dir=tmp_path).result["status"] == "reconciled"
    replay = r.run_typed(lost, SCRIPT, "endpoint", 1, op, journal_dir=tmp_path)
    assert replay.error == "receipt_reconciled" and replay.state["receipt_status"] == "reconciled"
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).ok


def test_explicit_declared_not_started_does_not_leave_barrier(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    script = """import json
def run(_ctx): print(json.dumps({'ok':False,'error':'preflight','mutation_state':'not_started'}))
"""
    op = str(uuid.uuid4())
    result = r.run_typed(send, script, "endpoint", 1, op, journal_dir=tmp_path)
    assert not result.ok and result.error == "preflight"
    assert r.get_status(op, journal_dir=tmp_path).result["status"] == "declared_not_started"
    assert r.run_typed(send, SCRIPT, "endpoint", 1, journal_dir=tmp_path).ok


def test_shadowed_print_argument_and_closure_are_preserved(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    script = """import json
def outer(errors):
    def print(value): errors.append(json.loads(value))
    def inner(): print(json.dumps({'ok': False, 'error': 'nested'}))
    inner()
def call_with(print): print(json.dumps({'ok':False,'error':'argument'}))
def run(_ctx):
    errors = []
    outer(errors)
    call_with(lambda value: errors.append(json.loads(value)))
    print(json.dumps({'ok':True,'errors':errors}))
"""
    out = r.run_typed(send, script, "endpoint", 1, journal_dir=tmp_path)
    assert out.ok and [e["error"] for e in json.loads(out.message)["errors"]] == [
        "nested",
        "argument",
    ]


def test_module_global_print_rebinding_refused_before_dispatch(tmp_path):
    script = """import json
print = lambda value: None
def run(_ctx): print(json.dumps({'ok':True}))
"""
    out = r.run_typed(
        lambda *a, **k: pytest.fail("must not dispatch"),
        script,
        "endpoint",
        1,
        journal_dir=tmp_path,
    )
    assert out.error == "receipt_invalid_input"


def test_claim_prevents_second_execution_after_exception(monkeypatch, tmp_path):
    mock_send(monkeypatch)
    script = """import json
values=[]
def run(_ctx):
    values.append(1)
    raise RuntimeError('interrupted')
    print(json.dumps({'ok': True}))
"""
    seen = []

    def twice(instrumented, *, read_only=False):
        ns = {}
        exec(instrumented, ns)
        with pytest.raises(RuntimeError, match="interrupted"):
            ns["run"](None)
        with pytest.raises(RuntimeError, match="already claimed"):
            ns["run"](None)
        seen.extend(ns["values"])
        return Envelope(ok=False, error="unknown")

    out = r.run_typed(twice, script, "endpoint", 1, journal_dir=tmp_path)
    assert not out.ok and seen == [1]
    assert out.state["execution_outcome"] == "unknown"


def test_top_level_output_refused_before_dispatch(tmp_path):
    script = (
        "import json\nprint(json.dumps({'ok':True}))\ndef run(c): print(json.dumps({'ok':True}))\n"
    )
    result = r.run_typed(
        lambda *a, **k: pytest.fail("must not dispatch"),
        script,
        "endpoint",
        1,
        journal_dir=tmp_path,
    )
    assert result.error == "receipt_invalid_input"
    assert list(tmp_path.iterdir()) == []


def test_reserved_capacity_for_internal_recovery_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "_MAX_ENTRIES", 3)
    monkeypatch.setattr(r, "_RECOVERY_RESERVE", 1)
    send, calls = mock_send(monkeypatch)
    failed_id = str(uuid.uuid4())
    assert not r.run_typed(send, FAIL, "endpoint", 1, failed_id, journal_dir=tmp_path).ok
    normal_read = r.run_typed(send, SCRIPT, "endpoint", 1, read_only=True, journal_dir=tmp_path)
    assert normal_read.ok
    full = r.run_typed(send, SCRIPT, "endpoint", 1, read_only=True, journal_dir=tmp_path)
    assert full.error == "receipt_capacity"
    invalid = r.run_typed(send, SCRIPT, "endpoint", 1, recovery_probe=True, journal_dir=tmp_path)
    assert invalid.error == "receipt_invalid_input"
    probe_id = str(uuid.uuid4())
    probe = r.run_typed(
        send,
        SCRIPT,
        "endpoint",
        1,
        probe_id,
        read_only=True,
        journal_dir=tmp_path,
        recovery_probe=True,
    )
    assert probe.ok and calls == [False, True, True]
    result = r.reconcile(
        failed_id,
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=probe_id,
        acknowledgement=True,
        evidence="inspected",
        journal_dir=tmp_path,
    )
    assert result.ok


def test_reconcile_refuses_read_and_resolved_and_does_not_replace_evidence(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    write_id, read_id, probe_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    r.run_typed(send, SCRIPT, "endpoint", 1, write_id, journal_dir=tmp_path)
    r.run_typed(send, SCRIPT, "endpoint", 1, read_id, read_only=True, journal_dir=tmp_path)
    args = dict(
        endpoint="endpoint",
        expected_document="doc-1",
        probe_operation_id=read_id,
        acknowledgement=True,
        evidence="first",
        journal_dir=tmp_path,
    )
    assert not r.reconcile(write_id, **args).ok
    assert not r.reconcile(read_id, **args).ok
    failed_id = str(uuid.uuid4())
    r.run_typed(send, FAIL, "endpoint", 1, failed_id, journal_dir=tmp_path)
    r.run_typed(
        send,
        SCRIPT,
        "endpoint",
        1,
        probe_id,
        read_only=True,
        journal_dir=tmp_path,
        recovery_probe=True,
    )
    args["probe_operation_id"] = probe_id
    assert r.reconcile(failed_id, **args).ok
    args["evidence"] = "replacement"
    assert r.reconcile(failed_id, **args).ok
    record = json.loads((tmp_path / (failed_id + ".reconciled.json")).read_text())
    assert record["evidence"] == "first"


def test_result_is_snapshotted_at_original_output_call(monkeypatch, tmp_path):
    send, _ = mock_send(monkeypatch)
    script = "import json\ndef run(c):\n value={'ok':True,'number':1}\n print(json.dumps(value))\n value['number']=2\n"
    out = r.run_typed(send, script, "endpoint", 1, journal_dir=tmp_path)
    assert out.ok and json.loads(out.message)["number"] == 1


def test_shadowed_json_refused_before_dispatch(tmp_path):
    script = "import json\ndef run(c):\n json=object()\n print(json.dumps({'ok':True}))\n"
    result = r.run_typed(
        lambda *a, **k: pytest.fail("must not dispatch"),
        script,
        "endpoint",
        1,
        journal_dir=tmp_path,
    )
    assert result.error == "receipt_invalid_input"
    assert list(tmp_path.iterdir()) == []
