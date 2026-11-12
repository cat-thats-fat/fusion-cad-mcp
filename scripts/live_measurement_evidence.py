"""Opt-in native measurement fixture; run only with exclusive Fusion access.

Run prepare, measure, then cleanup with the same output directory. The registry
is maintained inside Fusion, so do not restart or run another automation client.
Private evidence includes handles and full native results. A failed or uncertain
write must be investigated and reconciled before continuing or closing fixtures.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from live_field_followup import adapter_for, checked, fixture, inspect, native, prepare

from fusion_cad_mcp.tools import handle_tools, inspection, io, validation


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


def measure(adapter, folder, *, existing=False):
    if existing:
        setup = native(adapter, '''
            doc, design = _fixture('primary')
            assert getattr(_state(), 'measurement_fixture_started', False)
            print(json.dumps({'ok': True, 'existing_fixture': True}))
        ''', read_only=True)
    else:
        setup = _native_fixtures(adapter)
    started = time.perf_counter()
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
    # A satisfying point bounds distance without establishing the global minimum.
    # Invalid smaller native candidates must not be promoted to valid minima.
    summary = []
    for row, (spec, expected) in zip(checks['checks'], samples, strict=True):
        evidence_row = row.get('evidence', {})
        candidate = evidence_row.get('minimum_scalar_candidate')
        witness = evidence_row.get('satisfying_witness')
        if row['status'] == 'pass':
            assert witness and evidence_row['proof'] == 'validated_witness_upper_bound', row
            assert witness['witness_consistency']['status'] == 'consistent', row
            assert witness['surface_membership']['status'] == 'on_face', row
            assert abs(witness['input_point_separation_mm'] - expected) < 0.001, row
            assert witness['witness_upper_bound_mm'] <= spec['max_mm'] + spec['tolerance_mm'], row
        else:
            assert row['status'] == 'unavailable', row
        if not spec['name'].startswith('cylinder_'):
            assert row['status'] == 'pass', row
        summary.append({'name': spec['name'], 'status': row['status'],
                        'candidate_mm': candidate.get('minimum_distance_mm') if candidate else None,
                        'proof': evidence_row.get('proof'),
                        'satisfying_distance_mm': witness.get('input_point_separation_mm') if witness else None,
                        'witness_upper_bound_mm': witness.get('witness_upper_bound_mm') if witness else None,
                        'witness_status': candidate.get('witness_consistency', {}).get('status') if candidate else None})
    pair_row = pair['checks'][0]
    assert pair_row['status'] == 'pass' and abs(pair_row['evidence']['minimum_distance_mm'] - 10) < 0.001, pair_row
    assert direct.ok and abs(direct.result['value_mm'] - 10) < 0.001, direct.to_dict()
    for ev in (pair_row['evidence'], direct.result['distance_evidence']):
        assert all(x['status'] == 'on_face' for x in ev['face_membership'].values()), ev
    return {'ok': True, 'surface_samples': summary, 'face_cap_refused': True, 'stale_refused': True,
            'face_distance_status': pair_row['status'], 'measure_ok': direct.ok,
            'face_witness_order': pair_row['evidence']['witness_order'],
            'feedback_with_inventory_ms': round((time.perf_counter()-started)*1000),
            'originals_intact': True, 'protected_intact': True}


def fidelity(adapter, folder):
    setup = native(adapter, '''
        doc, design = _fixture('primary'); root = design.rootComponent; state = _state()
        assert not hasattr(state,'fidelity_started'), 'Fixture already attempted; inspect first'
        state.fidelity_started = True
        owners = []
        for name in ('FidelitySource','FidelityImported'):
            occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
            owners.append(occ); occ.component.name = name
        state.fidelity_owners = owners
        comp = owners[0].component
        sk = comp.sketches.add(comp.xYConstructionPlane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0,0,0),adsk.core.Point3D.create(2,3,0))
        feat = comp.features.extrudeFeatures.addSimple(sk.profiles.item(0),adsk.core.ValueInput.createByString('10 mm'),adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        state.fidelity_source = feat.bodies.item(0); state.fidelity_source.name = 'FidelityBox'
        assert design.computeAll()
        print(json.dumps({'ok':True,'source':owners[0].fullPathName,'target':owners[1].fullPathName}))
    ''')
    path = folder/'synthetic-fidelity.step'
    exported = checked(io.export(adapter,'step',str(path),component=setup['source']))
    imported = checked(io.import_geometry(adapter,'step',str(path),target_component=setup['target']))
    target = native(adapter, '''
        doc,design=_fixture('primary'); state=_state(); root=design.rootComponent
        prefix=state.fidelity_owners[1].fullPathName
        candidates=[(o,b) for o in root.allOccurrences if o.fullPathName==prefix or o.fullPathName.startswith(prefix+'+') for b in o.bRepBodies]
        assert len(candidates)==1
        occ,proxy=candidates[0]; state.fidelity_imported=proxy.nativeObject or proxy
        state.fidelity_imported.name='ImportedFidelityBox'
        assert abs(state.fidelity_imported.volume*1000-6000)<1e-3
        print(json.dumps({'ok':True,'path':occ.fullPathName,'body_name':state.fidelity_imported.name}))
    ''')
    point = [10,15,10]
    def probe(component, name, maximum=0.05):
        handle=_body_handle(adapter,component,name)
        spec=_sample('local_surface',handle,point,0)
        spec['max_mm']=maximum
        return checked(validation.validate_design(adapter,[spec]))['checks'][0]
    source=probe(setup['source'],'FidelityBox')
    roundtrip=probe(target['path'],target['body_name'])
    (folder/'fidelity-private.json').write_text(json.dumps({'source':source,'step_roundtrip':roundtrip,
        'export':exported,'import':imported,'phase':'before_notch'},indent=2),encoding='utf-8')
    assert source['status']=='pass' and roundtrip['status']=='pass', (source,roundtrip)
    notch = native(adapter, '''
        doc,design=_fixture('primary'); state=_state(); body=state.fidelity_imported; comp=body.parentComponent
        before=body.volume*1000
        box=body.boundingBox
        bounds=[[getattr(p,a)*10 for a in ('x','y','z')] for p in (box.minPoint,box.maxPoint)]
        plane_in=comp.constructionPlanes.createInput()
        plane_in.setByOffset(comp.xYConstructionPlane,adsk.core.ValueInput.createByString('9 mm'))
        plane=comp.constructionPlanes.add(plane_in)
        sk=comp.sketches.add(plane)
        sk.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0.95,1.45,0),adsk.core.Point3D.create(1.05,1.55,0))
        inp=comp.features.extrudeFeatures.createInput(sk.profiles.item(0),adsk.fusion.FeatureOperations.CutFeatureOperation)
        inp.participantBodies=[body]
        inp.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByString('1 mm')),adsk.fusion.ExtentDirections.PositiveExtentDirection)
        cut=comp.features.extrudeFeatures.add(inp); assert cut and design.computeAll()
        assert cut.healthState==adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        after=body.volume*1000; box=body.boundingBox
        new_bounds=[[getattr(p,a)*10 for a in ('x','y','z')] for p in (box.minPoint,box.maxPoint)]
        assert abs(before-6000)<1e-3 and abs(after-5999)<1e-3
        assert bounds==new_bounds
        assert abs(state.fidelity_source.volume*1000-6000)<1e-3
        print(json.dumps({'ok':True,'before_mm3':before,'after_mm3':after,
            'volume_change_percent':abs(after-before)/before*100,'bounds_unchanged':True,'source_volume_mm3':state.fidelity_source.volume*1000}))
    ''')
    changed=probe(target['path'],target['body_name'])
    relaxed=probe(target['path'],target['body_name'],0.6)
    preservation=inspect(adapter)
    evidence={'source':source,'step_roundtrip':roundtrip,'changed_copy':changed,'relaxed_check':relaxed,'notch':notch,
              'export':exported,'import':imported,'preservation':preservation}
    (folder/'fidelity-private.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    assert changed['status'] in ('unavailable','violation'),changed
    candidate=changed['evidence']['minimum_scalar_candidate']
    if changed['status']=='violation':
        assert changed['evidence']['face_failure_count']==0,changed
        assert candidate and abs(candidate['minimum_distance_mm']-0.5)<0.001,changed
    assert relaxed['status']=='pass',relaxed
    witness=relaxed['evidence']['satisfying_witness']
    assert witness['surface_membership']['status']=='on_face',relaxed
    assert witness['witness_consistency']['status']=='consistent',relaxed
    assert abs(witness['input_point_separation_mm']-0.5)<0.001,relaxed
    assert witness['witness_upper_bound_mm']<=0.6,relaxed
    assert preservation['originals_intact'] and preservation['protected_intact'] and preservation['owned_open']==2
    return {'ok':True,'step_roundtrip_local_probe':'pass','deliberate_local_change':changed['status'],
            'native_minimum_candidate_mm':candidate['minimum_distance_mm'] if candidate else None,
            'candidate_membership':candidate['surface_membership']['status'] if candidate else None,
            'relaxed_probe_status':relaxed['status'],'relaxed_upper_bound_mm':witness['witness_upper_bound_mm'],
            'analytic_notch_distance_mm':0.5,'gross_volume_within_0_1_percent':notch['volume_change_percent']<0.1,
            **notch,'originals_intact':True,'protected_intact':True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', required=True, help='Confirm exclusive Fusion automation')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('action', choices=('prepare', 'measure', 'recheck', 'fidelity', 'inspect', 'cleanup'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with adapter_for(args.output) as adapter:
        if args.action == 'prepare':
            result = prepare(adapter, args.output)
            result['fixture'] = fixture(adapter)
        elif args.action == 'measure':
            result = measure(adapter, args.output)
        elif args.action == 'recheck':
            result = measure(adapter, args.output, existing=True)
        elif args.action == 'fidelity':
            result = fidelity(adapter,args.output)
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
