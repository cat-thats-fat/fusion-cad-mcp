"""Opt-in sampled-pose Fusion fixture; requires exclusive automation until cleanup.

Run prepare, check, inspect and cleanup with the same output directory. Only
registered fixture documents may be closed. Outputs and receipts are private.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from live_field_followup import adapter_for, checked, fixture, inspect, native, prepare
from live_round2 import cleanup

from fusion_cad_mcp.tools import validation


def setup_geometry(adapter):
    return native(adapter, '''
        doc, design = _fixture('primary'); root = design.rootComponent
        assert _originals_intact()
        def box(component, name, x0, y0, x1, y1, z, height):
            plane = component.xYConstructionPlane
            if z:
                inp = component.constructionPlanes.createInput()
                assert inp.setByOffset(plane, adsk.core.ValueInput.createByString(str(z) + ' mm'))
                plane = component.constructionPlanes.add(inp)
            sketch = component.sketches.add(plane); sketch.name = name + ' profile'
            sketch.sketchCurves.sketchLines.addTwoPointRectangle(
                adsk.core.Point3D.create(x0/10, y0/10, 0),
                adsk.core.Point3D.create(x1/10, y1/10, 0))
            assert sketch.profiles.count == 1
            feat = component.features.extrudeFeatures.addSimple(sketch.profiles.item(0),
                adsk.core.ValueInput.createByString(str(height) + ' mm'),
                adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            assert feat and feat.bodies.count == 1
            feat.name = name + ' extrusion'; feat.bodies.item(0).name = name
        assert not any(b.name.startswith('Pose fixture ') for b in root.bRepBodies)
        box(root, 'Pose fixture A', 0, 0, 10, 20, 0, 10)
        box(root, 'Pose fixture B', 20, 0, 30, 10, 0, 10)
        box(root, 'Pose fixture enclosure', -50, -50, 50, 50, -10, 40)
        box(root, 'Pose fixture nested obstacle', 85, 65, 95, 75, 10, 10)
        refs = [o for o in root.allOccurrences if o.component.name == 'Reference']
        assert len(refs) == 1
        ref = refs[0]
        box(ref.component, 'Pose fixture nested A', 0, 0, 10, 20, 0, 10)
        assert design.computeAll()
        refs = [o for o in root.allOccurrences if o.component.name == 'Reference']
        assert len(refs) == 1
        ref = refs[0]
        bodies = [b for b in ref.bRepBodies if b.name == 'Pose fixture nested A']
        assert len(bodies) == 1
        bb = bodies[0].boundingBox
        bounds = [[getattr(p, a)*10 for a in ('x','y','z')] for p in (bb.minPoint,bb.maxPoint)]
        expected = [[80,60,10], [100,70,20]]
        assert all(abs(bounds[i][j] - expected[i][j]) < 1e-4 for i in range(2) for j in range(3)), bounds
        _state().pose_signature = _signature(doc)
        print(json.dumps({'ok': True, 'nested_selector': ref.fullPathName + ' / Pose fixture nested A',
            'nested_bounds_mm': bounds, 'originals_intact': _originals_intact()}))
    ''')


def pose_checks(adapter, folder):
    before = inspect(adapter)
    assert before['originals_intact'] and before['protected_intact'] and before['owned_open'] == 2, before
    geometry = setup_geometry(adapter)
    base = {'type': 'pose_interference', 'frame': 'root', 'max_overlap_mm3': 0}
    spec = [
        {**base, 'body_a': 'Pose fixture A', 'body_b': 'Pose fixture B', 'poses': [
            {'id': 'separate', 'translation_mm': [0, 0, 0]},
            {'id': 'overlap', 'translation_mm': [15, 0, 0]},
            {'id': 'rotate_90', 'translation_mm': [30, 0, 0], 'rotation_deg': 90},
            {'id': 'contact', 'translation_mm': [10, 0, 0]},
        ]},
        {**base, 'body_a': 'Pose fixture A', 'body_b': 'Pose fixture enclosure', 'poses': [
            {'id': 'rotate_45_contained', 'translation_mm': [0, 0, 0], 'rotation_deg': 45},
        ]},
        {**base, 'body_a': geometry['nested_selector'], 'body_b': 'Pose fixture nested obstacle', 'poses': [
            {'id': 'nested_overlap', 'translation_mm': [0, 0, 0]},
            {'id': 'nested_separate', 'translation_mm': [30, 0, 0]},
        ]},
    ]
    started = time.perf_counter()
    response = validation.validate_design(adapter, spec)
    # Persist the full envelope before asserting so an unavailable native Boolean
    # remains inspectable and is never mislabeled as geometric success.
    (folder / 'pose-private.json').write_text(json.dumps(response.to_dict(), indent=2), encoding='utf-8')
    result = checked(response)
    after_geometry = native(adapter, '''
        doc, design = _fixture('primary')
        print(json.dumps({'ok': True, 'primary_unchanged': _signature(doc) == _state().pose_signature,
            'originals_intact': _originals_intact(),
            'protected_intact': _signature(_state().fixtures['protected']) == _state().protected_signature}))
    ''', read_only=True)
    after = inspect(adapter)
    assert after_geometry['primary_unchanged'] and after_geometry['originals_intact']
    assert after_geometry['protected_intact'] and after['originals_intact'] and after['protected_intact']
    assert after['owned_open'] == 2
    groups = result['checks']
    rows = {row['id']: row for group in groups for row in group['poses']}
    assert rows['separate']['status'] == 'pass', rows
    assert rows['nested_separate']['status'] == 'pass', rows
    expected = {'overlap': 500, 'rotate_90': 1000, 'rotate_45_contained': 2000, 'nested_overlap': 500}
    for name, volume in expected.items():
        row = rows[name]
        assert row['status'] in ('violation', 'unavailable'), row
        if row['status'] == 'violation':
            assert abs(row['overlap_mm3'] - volume) < 0.01, row
    assert rows['contact']['status'] in ('pass', 'violation', 'unavailable'), rows['contact']
    return {'ok': True, 'originals_and_protected_unchanged': True, 'primary_unchanged': True,
            'nested_bounds_mm': geometry['nested_bounds_mm'], 'expected_overlap_mm3': expected,
            'samples': rows, 'native_unavailable_samples': [name for name in expected if rows[name]['status'] == 'unavailable'],
            'scope': 'sampled poses only; contact is not a clearance claim',
            'elapsed_ms': (time.perf_counter()-started)*1000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', required=True,
                        help='Confirm exclusive Fusion automation until cleanup')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('action', choices=('prepare', 'check', 'inspect', 'cleanup'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with adapter_for(args.output, timeout=120) as adapter:
        if args.action == 'prepare':
            result = prepare(adapter, args.output)
            assert result['originals_intact'], result
            result['fixture'] = fixture(adapter)
        elif args.action == 'check':
            result = pose_checks(adapter, args.output)
        elif args.action == 'inspect':
            result = inspect(adapter)
        else:
            status = inspect(adapter)
            assert status['owned_open'] == 2 and status['originals_intact'] and status['protected_intact'], status
            result = cleanup(adapter)
            assert result['closed_owned_documents'] == 2 and result['originals_intact'], result
    (args.output / ('pose-' + args.action + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
