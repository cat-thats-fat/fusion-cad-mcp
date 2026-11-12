import json
import sys
import uuid

import pytest

from fusion_cad_mcp import receipts as r
from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import execute as execute_tool


def sender(script, *, read_only=False):
    ns = {}
    exec(script, ns)
    ns['run']('context')
    return Envelope(ok=True, message='')


def call(tmp_path, script, op=None, send=sender):
    return r.run_typed(send, script, 'endpoint', 1, op or str(uuid.uuid4()), journal_dir=tmp_path, custom=True)


def test_custom_recovery_identity_stages_and_utf8(tmp_path):
    op = str(uuid.uuid4())
    script = '''def run(ctx):
    print("héllo", ctx)
    receipt_stage("created", {"body": "café"})
'''
    result = call(tmp_path, script, op)
    assert result.ok and result.state['recovered_from_receipt']
    status = r.get_status(op, journal_dir=tmp_path).result
    assert status['declared_result']['stdout'] == 'héllo context\n'
    assert status['stages'] == [{'name': 'created', 'data': {'body': 'café'}}]
    assert call(tmp_path, script, op).ok
    assert call(tmp_path, script + '# changed', op).error == 'receipt_conflict'


def test_partial_failure_restores_streams_and_blocks(tmp_path):
    op = str(uuid.uuid4())
    before = sys.stdout, sys.stderr
    script = '''import sys
def run(ctx):
    receipt_stage("created", {"body": "A"})
    print("diagnostic", file=sys.stderr)
    raise RuntimeError("optional failure")
'''
    result = call(tmp_path, script, op)
    assert not result.ok and result.state['execution_outcome'] == 'unknown'
    assert (sys.stdout, sys.stderr) == before
    status = r.get_status(op, journal_dir=tmp_path).result
    assert status['stages'][0]['name'] == 'created'
    assert status['failure']['exception_type'] == 'RuntimeError'
    assert status['failure']['stderr'] == 'diagnostic\n'
    assert call(tmp_path, 'def run(ctx): pass').error == 'receipt_barrier'


def test_bounds_and_preflight(tmp_path):
    result = call(tmp_path, 'def run(ctx):\n print("é" * 50000)')
    assert result.ok
    out = r.get_status(result.state['operation_id'], journal_dir=tmp_path).result['declared_result']
    assert len(out['stdout'].encode()) <= 32768 and out['stdout_truncated']
    bad = r.run_typed(sender, 'def run(:', 'endpoint', 1, str(uuid.uuid4()), journal_dir=tmp_path, custom=True)
    assert bad.error == 'receipt_invalid_input'
    missing = r.run_typed(sender, 'def run(ctx): pass', 'endpoint', 1, journal_dir=tmp_path, custom=True)
    assert missing.error == 'receipt_invalid_input'


def test_late_completion_is_recovered_without_dispatch(tmp_path):
    op = str(uuid.uuid4())
    captured = []
    def lost(script, *, read_only=False):
        captured.append(script)
        raise TimeoutError()
    script = 'def run(ctx): print("done")'
    assert not call(tmp_path, script, op, lost).ok
    ns = {}
    exec(captured[0], ns)
    ns['run'](None)
    result = call(tmp_path, script, op, lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    assert result.ok and result.state['recovered_from_receipt']


def test_failed_stage_retains_previous_and_import_failure_is_recorded(tmp_path):
    op = str(uuid.uuid4())
    script = """def run(ctx):
    receipt_stage('first', {'value': 1})
    receipt_stage('oversized', 'x' * 5000)
"""
    assert not call(tmp_path, script, op).ok
    status = r.get_status(op, journal_dir=tmp_path).result
    assert status['stages'] == [{'name': 'first', 'data': {'value': 1}}]
    assert status['failure']['exception_type'] == 'ValueError'
    separate = tmp_path / 'other'
    result = call(separate, 'import no_such_fusion_execute_module\ndef run(ctx): pass')
    assert not result.ok
    assert r.get_status(result.state['operation_id'], journal_dir=separate).result['failure']['exception_type'] == 'ModuleNotFoundError'


@pytest.mark.parametrize('fails', [False, True])
def test_escaped_output_budget_persists_terminal_or_failure(tmp_path, fails):
    script = '''import sys
def run(ctx):
    print("\\x00" * 40000 + "é" * 40000, file=sys.stdout)
    print("\\x01" * 40000 + "界" * 40000, file=sys.stderr)
'''
    if fails:
        script += '    raise ValueError("broken")\n'
    result = call(tmp_path, script)
    status = r.get_status(result.state['operation_id'], journal_dir=tmp_path).result
    record = status['failure'] if fails else status['declared_result']
    assert status['script_execution'] == ('raised' if fails else 'returned')
    assert record['stdout_truncated'] and record['stderr_truncated']
    assert len(json.dumps(record, ensure_ascii=True).encode()) < r._MAX_RECORD
    assert result.ok is not fails
    if fails:
        assert record['exception_message'] == 'broken'
        assert result.state['script_execution'] == 'raised'
        assert call(tmp_path, 'def run(ctx): pass').error == 'receipt_barrier'


def test_str_failure_in_exception_is_preserved(tmp_path):
    script = '''class Bad(Exception):
    def __str__(self): raise RuntimeError("cannot stringify")
def run(ctx): raise Bad()
'''
    result = call(tmp_path, script)
    status = r.get_status(result.state['operation_id'], journal_dir=tmp_path).result
    assert status['failure']['exception_type'] == 'Bad'
    assert status['failure']['exception_message'] == '<exception message unavailable>'


def test_timeout_blocks_duplicate_and_writes_until_late_receipt(tmp_path):
    op = str(uuid.uuid4())
    dispatched = []
    def lost(script, *, read_only=False):
        dispatched.append(script)
        raise TimeoutError()
    script = 'def run(ctx): print("eventually")'
    assert call(tmp_path, script, op, lost).state['execution_outcome'] == 'unknown'
    assert call(tmp_path, script, op, lost).error == 'receipt_unresolved'
    assert call(tmp_path, 'def run(ctx): pass').error == 'receipt_barrier'
    assert len(dispatched) == 1


def test_id_mode_conflict_and_input_validation(tmp_path):
    op = str(uuid.uuid4())
    script = '''import json
def run(ctx): print(json.dumps({'ok': True}))
'''
    assert call(tmp_path, script, op).ok
    typed = r.run_typed(sender, script, 'endpoint', 1, op, journal_dir=tmp_path)
    assert typed.error == 'receipt_conflict'
    oversized = r.run_typed(sender, '#' * (1024 * 1024 + 1), 'endpoint', 1,
                            str(uuid.uuid4()), journal_dir=tmp_path, custom=True)
    assert oversized.error == 'receipt_invalid_input'
    assert execute_tool.run(object(), script, durable=1).error == 'invalid_input'
    invalid = r.run_typed(sender, script, 'endpoint', 1, op, journal_dir=tmp_path, custom=1)
    assert invalid.error == 'receipt_invalid_input'


def test_missing_entry_and_adapter_journal_routing(tmp_path):
    from types import SimpleNamespace
    adapter = SimpleNamespace(execute_script=sender, url='endpoint', timeout=1,
                              receipt_journal_dir=tmp_path)
    op = str(uuid.uuid4())
    result = FusionAdapter.execute_durable_script(adapter, 'x = 1', operation_id=op)
    assert not result.ok and result.state['script_execution'] == 'raised'
    assert r.get_status(op, journal_dir=tmp_path).result['failure']['exception_type'] == 'TypeError'


def test_duplicate_success_never_calls_sender_again(tmp_path):
    op = str(uuid.uuid4())
    calls = []
    def counting(script, *, read_only=False):
        calls.append(script)
        return sender(script, read_only=read_only)
    script = "def run(ctx): print('one')"
    assert call(tmp_path, script, op, counting).ok
    assert call(tmp_path, script, op, counting).ok
    assert len(calls) == 1


def test_failed_receipt_is_surfaced_when_transport_returns_error(tmp_path):
    def transport(script, *, read_only=False):
        try:
            sender(script)
        except ValueError:
            return Envelope(ok=False, error='remote_failure', message='failed')
    result = call(tmp_path, "def run(ctx): raise ValueError('bad')", send=transport)
    assert result.state['script_execution'] == 'raised'
    assert result.state['execution_outcome'] == 'unknown'


@pytest.mark.parametrize('script', [
    "async def run(ctx):\n print('never')",
    "def run(ctx):\n print('never')\n yield 1",
    "async def run(ctx):\n print('never')\n yield 1",
    "async def work():\n print('never')\ndef run(ctx): return work()",
    "def work():\n print('never')\n yield 1\ndef run(ctx): return work()",
    "run = None",
])
def test_async_generator_and_noncallable_entry_keep_barrier(tmp_path, script):
    op = str(uuid.uuid4())
    result = call(tmp_path, script, op)
    assert not result.ok and result.state['script_execution'] == 'raised'
    status = r.get_status(op, journal_dir=tmp_path).result
    assert status['status'] == 'unknown'
    assert status['declared_result'] is None
    assert status['failure']['exception_type'] == 'TypeError'
    assert status['failure']['stdout'] == ''
    assert call(tmp_path, 'def run(ctx): pass').error == 'receipt_barrier'

def test_async_entry_after_top_level_stage_keeps_evidence_and_barrier(tmp_path):
    script = '''receipt_stage("top_level_effect", {"changed": True})
async def run(ctx): pass
'''
    result = call(tmp_path, script)
    status = r.get_status(result.state['operation_id'], journal_dir=tmp_path).result
    assert status['stages'] == [{'name': 'top_level_effect', 'data': {'changed': True}}]
    assert status['script_execution'] == 'raised'
    assert call(tmp_path, 'def run(ctx): pass').error == 'receipt_barrier'
