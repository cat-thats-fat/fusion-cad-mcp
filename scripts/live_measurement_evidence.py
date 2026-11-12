"""Opt-in native measurement fixture; run only with exclusive Fusion access.

Run prepare, measure, then cleanup with the same output directory. The registry
is maintained inside Fusion, so do not restart or run another automation client.
Private evidence includes handles and full native results. A failed or uncertain
write must be investigated and reconciled before continuing or closing fixtures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from live_field_followup import adapter_for, checked, fixture, inspect, native, prepare

from fusion_cad_mcp.tools import handle_tools, inspection, validation


def _body_handle(adapter, component, name):
    data = checked(inspection.inspect_assembly(adapter, component, include=['bodies'], fields=['handles']))
    matches = [row for row in data['sections']['bodies']['items'] if row['name'] == name]
    if len(matches) != 1 or not matches[0].get('handle'):
        raise RuntimeError(f'Expected one current body handle for {name}')
    return matches[0]['handle']


def _native_fixtures(adapter):
    return native(adapter, '''
        doc, design = _fixture('primary'); root = design.rootComponent
        state = _state()
        assert not hasattr(state, 'measurement_fixture_started'), 'Fixture already attempted; inspect first'
        state.measurement_fixture_started = True
        # All additions are confined to the owned primary document.
        sk = root.sketches.add(root.xYConstructionPlane)
        state.measurement_cylinder_sketch = sk
        sk.name = 'Measurement cylinder profile'
        sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(4, 0, 0), 1)
        assert sk.profiles.count == 1
        feat = root.features.extrudeFeatures.addSimple(sk.profiles.item(0),
            adsk.core.ValueInput.createByString('20 mm'), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        state.measurement_cylinder_feature = feat
        feat.name = 'Measurement cylinder'; feat.bodies.item(0).name = 'MeasurementCylinder'
        occ = next(o for o in root.allOccurrences if o.fullPathName == 'Outer:1+Reference:1')
        comp = occ.component
        nested = comp.sketches.add(comp.xYConstructionPlane)
        state.measurement_nested_sketch = nested
        nested.name = 'Measurement nested profile'
        nested.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0,0,0), adsk.core.Point3D.create(1,1,0))
        assert nested.profiles.count == 1
        ext = comp.features.extrudeFeatures.addSimple(nested.profiles.item(0),
            adsk.core.ValueInput.createByString('10 mm'), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        state.measurement_nested_feature = ext
        ext.name = 'Measurement nested extrusion'; ext.bodies.item(0).name = 'MeasurementNested'
        assert design.computeAll()
        assert _originals_intact() and _signature(state.fixtures['protected']) == state.protected_signature
        print(json.dumps({'ok': True, 'cylinder_radius_mm': 10, 'cylinder_height_mm': 20,
                          'nested_occurrence': occ.fullPathName}))
    ''')


def _sample(name, body, point, expected, max_faces=64):
    return {'type': 'surface_distance', 'name': name, 'body': body, 'point_mm': point,
            'frame': 'root', 'max_mm': expected + 0.001, 'tolerance_mm': 0,
            'max_faces': max_faces}


def measure(adapter, folder):
    setup = _native_fixtures(adapter)
    box = _body_handle(adapter, None, 'Editable')
    cylinder = _body_handle(adapter, None, 'MeasurementCylinder')
    nested = _body_handle(adapter, 'Outer:1+Reference:1', 'MeasurementNested')
    samples = [
        (_sample('box_outside', box, [25, 15, 5], 5), 5),
        (_sample('box_inside', box, [10, 15, 5], 5), 5),
        (_sample('box_edge', box, [0, 0, 5], 0), 0),
        (_sample('cylinder_outside', cylinder, [55, 0, 10], 5), 5),
        (_sample('cylinder_inside', cylinder, [40, 0, 10], 10), 10),
        (_sample('nested_outside', nested, [105, 65, 15], 5), 5),
    ]
    checks = checked(validation.validate_design(adapter, [x[0] for x in samples]))
    cap = checked(validation.validate_design(adapter, [_sample('face_cap', box, [25, 15, 5], 5, max_faces=1)]))
    stale_handle = box.rsplit(':', 1)[0] + ':intentionally-missing-token'
    stale = checked(validation.validate_design(adapter, [_sample('stale', stale_handle, [25, 15, 5], 5)]))
    faces = []
    for normal in ([0, 0, 1], [0, 0, -1]):
        result = checked(inspection.query_geometry(adapter, 'Editable', 'face', {'normal': normal}, expected_count=1))
        faces.append(result['items'][0]['handle'])
    pair = checked(validation.validate_design(adapter, [{
        'type': 'face_distance', 'face_a': faces[0], 'face_b': faces[1],
        'min_mm': 10, 'max_mm': 10, 'tolerance_mm': 0.00001,
    }]))
    direct = handle_tools.measure(adapter, faces[0], faces[1], kind='distance')
    preservation = inspect(adapter)
    evidence = {'setup': setup, 'surface': checks, 'face_cap': cap, 'stale': stale,
                'face_distance': pair, 'measure': direct.to_dict(), 'preservation': preservation}
    (folder / 'measurement-private.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    assert preservation['originals_intact'] and preservation['protected_intact'] and preservation['owned_open'] == 2, preservation
    assert cap['checks'][0]['status'] == 'unavailable' and cap['checks'][0]['reason'] == 'face_budget_exceeded', cap
    assert stale['checks'][0]['status'] == 'unavailable', stale
    # Validate analytic minima only when every face was usable. A contradictory
    # native witness remains unavailable evidence rather than a false pass.
    summary = []
    for row, (spec, expected) in zip(checks['checks'], samples, strict=True):
        evidence_row = row.get('evidence', {})
        candidate = evidence_row.get('minimum_scalar_candidate')
        if row['status'] != 'unavailable':
            assert candidate and abs(candidate['minimum_distance_mm'] - expected) < 0.001, row
            assert row['status'] == 'pass', row
        summary.append({'name': spec['name'], 'status': row['status'],
                        'candidate_mm': candidate.get('minimum_distance_mm') if candidate else None,
                        'witness_status': candidate.get('witness_consistency', {}).get('status') if candidate else None})
    pair_row = pair['checks'][0]
    if pair_row['status'] != 'unavailable':
        assert pair_row['status'] == 'pass' and abs(pair_row['evidence']['minimum_distance_mm'] - 10) < 0.001, pair_row
    if direct.ok:
        assert abs(direct.result['value_mm'] - 10) < 0.001, direct.to_dict()
    return {'ok': True, 'surface_samples': summary, 'face_cap_refused': True, 'stale_refused': True,
            'face_distance_status': pair_row['status'], 'measure_ok': direct.ok,
            'originals_intact': True, 'protected_intact': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', required=True, help='Confirm exclusive Fusion automation')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('action', choices=('prepare', 'measure', 'inspect', 'cleanup'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with adapter_for(args.output) as adapter:
        if args.action == 'prepare':
            result = prepare(adapter, args.output)
            result['fixture'] = fixture(adapter)
        elif args.action == 'measure':
            result = measure(adapter, args.output)
        elif args.action == 'inspect':
            result = inspect(adapter)
        else:
            # Supplied by the shared live harness after integration on main.
            from live_round2 import cleanup
            result = cleanup(adapter)
    (args.output / (args.action + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
