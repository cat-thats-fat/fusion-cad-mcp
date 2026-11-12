"""Opt-in synthetic field-feedback checks; one Fusion coordinator throughout.

Uses live_field_followup's owned-document registry and preservation witnesses.
Run prepare, selected checks, then cleanup with the same output directory.
Never run concurrently with another Fusion automation client.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from live_field_followup import (
    adapter_for,
    checked,
    fixture,
    inspect,
    native,
    prepare,
)

from fusion_cad_mcp.receipts import get_status
from fusion_cad_mcp.tools import features


def cleanup(adapter):
    return native(adapter, '''
        state = _state()
        assert _originals_intact(), 'Original document evidence changed'
        assert _signature(state.fixtures['protected']) == state.protected_signature
        owned = [d for d in state.owned if d.isValid]
        expected = {key: doc for key,doc in state.fixtures.items() if doc.isValid}
        assert len(owned) == len(expected), 'Unregistered fixture requires inspection'
        for doc in owned:
            keys = [key for key,value in expected.items() if value == doc]
            assert len(keys) == 1 and doc.name == 'Field verification ' + keys[0]
            assert not doc.isSaved, 'Saved fixture requires manual disposition'
            assert all(doc != original for original in state.originals)
        count = 0
        for doc in reversed(owned):
            assert not doc.isSaved
            assert doc.close(False)
            count += 1
        if state.previous and state.previous.isValid: assert state.previous.activate()
        assert _originals_intact()
        del sys.modules['_fusion_field_followup_v1']
        print(json.dumps({'ok': True, 'closed_owned_documents': count, 'originals_intact': True}))
    ''')


def preflight(adapter, folder):
    baseline = native(adapter, '''
        doc, design = _fixture('primary')
        root = design.rootComponent
        assert root.sketches.itemByName('Editable profile') is not None
        print(json.dumps({'ok': True, 'features': root.features.count,
            'bodies': root.bRepBodies.count}))
    ''', read_only=True)
    missing_id = str(uuid.uuid4())
    began = time.perf_counter()
    missing = features.extrude(adapter, 'Editable', expression='1 mm',
                               name='Preflight valid extrusion', operation_id=missing_id)
    assert not missing.ok and missing.error == 'sketch_not_found', missing.to_dict()
    assert missing.result['mutation_state'] == 'not_started', missing.to_dict()
    replay = features.extrude(adapter, 'Editable', expression='1 mm',
                              name='Preflight valid extrusion', operation_id=missing_id)
    assert replay.error == 'sketch_not_found', replay.to_dict()
    receipt = get_status(missing_id, journal_dir=folder / 'receipts')
    assert receipt.result['status'] == 'declared_not_started', receipt.to_dict()
    unchanged = native(adapter, '''
        doc, design = _fixture('primary'); root = design.rootComponent
        print(json.dumps({'ok': True, 'features': root.features.count,
            'bodies': root.bRepBodies.count}))
    ''', read_only=True)
    assert unchanged == baseline, (baseline, unchanged)

    # Register exact references before later operations; cleanup never guesses names.
    native(adapter, '''
        doc, design = _fixture('primary'); root = design.rootComponent
        state = _state(); state.preflight_sketches = []; state.preflight_owners = []
        for index in range(2):
            owner = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
            state.preflight_owners.append(owner)
            owner.component.name = 'Preflight owner ' + str(index)
            sketch = owner.component.sketches.add(owner.component.xYConstructionPlane)
            state.preflight_sketches.append(sketch)
            sketch.name = 'Ambiguous preflight profile'
            sketch.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0,0,0), 0.1)
        assert state.preflight_sketches[0].name == state.preflight_sketches[1].name
        print(json.dumps({'ok': True}))
    ''')
    ambiguous = features.extrude(adapter, 'Ambiguous preflight profile', expression='1 mm',
                                 operation_id=str(uuid.uuid4()))
    assert ambiguous.error == 'sketch_ambiguous', ambiguous.to_dict()
    assert ambiguous.result['mutation_state'] == 'not_started', ambiguous.to_dict()
    native(adapter, '''
        doc, design = _fixture('primary')
        for owner in reversed(_state().preflight_owners):
            assert owner.isValid and owner.deleteMe()
        print(json.dumps({'ok': True}))
    ''')
    success = features.extrude(adapter, 'Editable profile', expression='1 mm',
                               name='Preflight valid extrusion', operation_id=str(uuid.uuid4()))
    checked(success)
    geometry = native(adapter, '''
        doc, design = _fixture('primary'); root = design.rootComponent
        candidates = [f for f in root.features if f.name == 'Preflight valid extrusion']
        assert len(candidates) == 1
        feature = candidates[0]; assert feature.bodies.count == 1
        body = feature.bodies.item(0)
        dimensions = [(getattr(body.boundingBox.maxPoint, a)-getattr(body.boundingBox.minPoint, a))*10
                      for a in ('x','y','z')]
        assert all(abs(a-b) < 1e-6 for a,b in zip(dimensions,[20,30,1]))
        assert abs(body.volume*1000 - 600) < 1e-3
        assert feature.healthState == adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        print(json.dumps({'ok': True, 'dimensions_mm': dimensions,
            'volume_mm3': body.volume*1000, 'feature_healthy': True}))
    ''', read_only=True)
    (folder / 'preflight-private.json').write_text(json.dumps({
        'missing': missing.to_dict(), 'ambiguous': ambiguous.to_dict(),
        'success': success.to_dict()}, indent=2), encoding='utf-8')
    preservation = inspect(adapter)
    (folder / 'preflight-preservation.json').write_text(json.dumps(preservation, indent=2), encoding='utf-8')
    assert preservation['originals_intact'] and preservation['protected_intact'], preservation
    assert preservation['owned_open'] == 2, preservation
    return {'ok': True, 'missing_refusal': True, 'ambiguous_refusal': True,
            'failed_id_replay_without_mutation': True, 'refusal_preserved_counts': True,
            'subsequent_valid_extrusion': geometry, 'elapsed_ms': (time.perf_counter()-began)*1000,
            **preservation}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', required=True,
                        help='Confirm exclusive Fusion automation until cleanup')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('action', choices=('prepare', 'preflight', 'inspect', 'cleanup'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with adapter_for(args.output) as adapter:
        if args.action == 'prepare':
            result = prepare(adapter, args.output)
            result['fixture'] = fixture(adapter)
        elif args.action == 'preflight':
            result = preflight(adapter, args.output)
        else:
            result = {'inspect': inspect, 'cleanup': cleanup}[args.action](adapter)
    (args.output / (args.action + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
