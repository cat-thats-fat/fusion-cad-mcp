"""Opt-in durable execution test in disposable Fusion documents.

Requires exclusive Fusion automation. A failure leaves fixtures and receipts for
inspection; it never automatically retries or clears an unresolved mutation.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from live_field_followup import COMMON, adapter_for, checked, fixture, inspect, native, prepare
from live_round2 import cleanup

from fusion_cad_mcp.envelope import Envelope, parse_status_json
from fusion_cad_mcp.receipts import get_status, run_typed
from fusion_cad_mcp.tools import execute, operations


def readback(adapter, expected=None):
    data = native(adapter, '''
        doc, design = _fixture('primary'); body = design.rootComponent.bRepBodies.itemByName('Editable')
        print(json.dumps({'ok': True, 'value_mm': design.userParameters.itemByName('wall').value*10,
            'height_mm': (body.boundingBox.maxPoint.z-body.boundingBox.minPoint.z)*10,
            'volume_mm3': body.volume*1000, 'calls': getattr(_state(),'durable_calls',0)}))
    ''', read_only=True)
    if expected is None:
        expected = data['value_mm']
    assert abs(data['value_mm']-expected) < 1e-6 and abs(data['height_mm']-expected) < 1e-6, data
    assert abs(data['volume_mm3']-600*expected) < 1e-3, data
    return data


def script(expression, *, fail=False):
    return COMMON + '''
def run(_ctx):
    doc, design = _fixture('primary')
    assert adsk.core.Application.get().activeDocument == doc
    state = _state(); state.durable_calls = getattr(state,'durable_calls',0)+1
    design.userParameters.itemByName('wall').expression = EXPRESSION
    assert design.computeAll()
    receipt_stage('parameter_recomputed', {'parameter':'wall','expression':EXPRESSION})
    print('Unicode output: \u03bc / \U0001f9e9')
    import sys
    print('diagnostic stderr', file=sys.stderr)
    FAIL
'''.replace('EXPRESSION', repr(expression)).replace('    FAIL', "    raise RuntimeError('deliberate optional diagnostic failure')" if fail else '    return None')


def run_checks(adapter, folder):
    initial = readback(adapter, 10)
    stream = native(adapter, '''
        _state().durable_stdout = sys.stdout; _state().durable_stderr = sys.stderr
        print(json.dumps({'ok': True}))
    ''', read_only=True)
    assert stream['ok']
    raw = adapter.execute_script("def run(_ctx):\n    print('ROUND2_RAW_PROBE')\n", read_only=True)
    raw_observation = {'ok': raw.ok, 'probe_visible': 'ROUND2_RAW_PROBE' in (raw.message or ''),
                       'message_empty': not bool(raw.message)}
    operation = str(uuid.uuid4())
    good = script('11 mm')
    began = time.perf_counter()
    success = execute.run(adapter, good, durable=True, operation_id=operation)
    returned = checked(success)
    assert '\u03bc' in returned['stdout'] and '\U0001f9e9' in returned['stdout'], returned
    assert returned['stderr'] == 'diagnostic stderr\n', returned
    success_geometry = readback(adapter, 11)
    feedback_ms = (time.perf_counter()-began)*1000
    assert success_geometry['calls'] == 1
    replay = execute.run(adapter, good, durable=True, operation_id=operation)
    assert replay.ok and readback(adapter, 11)['calls'] == 1
    conflict = execute.run(adapter, good+'\n# changed input\n', durable=True, operation_id=operation)
    assert conflict.error == 'receipt_conflict', conflict.to_dict()

    failed_id = str(uuid.uuid4())
    failure = execute.run(adapter, script('12 mm', fail=True), durable=True, operation_id=failed_id)
    assert not failure.ok, failure.to_dict()
    status = get_status(failed_id, journal_dir=folder/'receipts')
    assert status.result['status'] == 'unknown', status.to_dict()
    assert status.result['stages'][0]['name'] == 'parameter_recomputed', status.to_dict()
    assert status.result['failure']['exception_message'] == 'deliberate optional diagnostic failure'
    failed_geometry = readback(adapter)
    assert failed_geometry['calls'] == 2
    blocked = execute.run(adapter, good, durable=True, operation_id=str(uuid.uuid4()))
    assert blocked.error == 'receipt_barrier', blocked.to_dict()
    assert readback(adapter, failed_geometry['value_mm'])['calls'] == 2
    restored_streams = native(adapter, '''
        print(json.dumps({'ok':True, 'stdout_restored':sys.stdout is _state().durable_stdout,
            'stderr_restored':sys.stderr is _state().durable_stderr}))
    ''', read_only=True)
    # Autodesk can replace capture writers between calls. Durable wrapper restoration
    # is separately tested in-process offline; this records observed live identities.
    reconciled = operations.reconcile_operation(adapter, failed_id, status.result['document_creation_id'], True,
        'Deliberate synchronous diagnostic exception is recorded. Independent owned-document readback: '
        +json.dumps(failed_geometry)+'. Subsequent mutation was blocked. This releases a barrier, not a rollback. Restore only wall next.')
    assert reconciled.ok, reconciled.to_dict()
    checked(execute.run(adapter, script('10 mm'), durable=True, operation_id=str(uuid.uuid4())))
    restored = readback(adapter, 10)

    dispatches = []
    def lose_reply(code, **kwargs):
        dispatches.append(1)
        response = adapter.execute_script(code, **kwargs)
        assert response.ok, response.to_dict()
        return Envelope(ok=False, error='injected_reply_loss', state={'failure_kind':'transport',
            'execution_outcome':'unknown','model_may_have_changed':True,'retry_safe':False})
    lost_id = str(uuid.uuid4())
    lost = run_typed(lose_reply, "def run(_ctx):\n    print('durable reply-loss probe')\n",
        adapter.url, adapter.timeout, operation_id=lost_id, custom=True, journal_dir=folder/'receipts')
    assert lost.ok and len(dispatches) == 1, lost.to_dict()
    assert parse_status_json(lost)['stdout'] == 'durable reply-loss probe\n'
    preservation = inspect(adapter)
    assert preservation['originals_intact'] and preservation['protected_intact'], preservation
    private = {'success': success.to_dict(), 'failure': failure.to_dict(), 'status':status.to_dict(),
               'blocked':blocked.to_dict(),'reconciliation':reconciled.to_dict(),'lost_reply':lost.to_dict()}
    (folder/'durable-private.json').write_text(json.dumps(private,indent=2),encoding='utf-8')
    return {'ok': True, 'raw_output_observation':raw_observation, 'initial':initial,
            'successful_geometry':success_geometry,'failed_script_geometry':failed_geometry,
            'restored_geometry':restored,'duplicate_not_executed':True,'changed_id_input_refused':True,
            'failed_stage_retained':True,'blocked_next_mutation':True,'reply_loss_injected':True,
            'injected_reply_loss_receipt_recovered':True,'feedback_ms':feedback_ms,
            'stream_identity_observation':restored_streams,**preservation}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with adapter_for(args.output) as adapter:
        prepare(adapter,args.output)
        fixture(adapter)
        result=run_checks(adapter,args.output)
        result['run_complete']=False
        result['cleanup']={'status':'pending'}
        (args.output/'durable.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        result['cleanup']=cleanup(adapter)
        result['run_complete']=True
        (args.output/'durable.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
