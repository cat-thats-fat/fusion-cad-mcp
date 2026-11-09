"""Opt-in local Fusion fixtures for the field-feedback improvements.

Existing documents are witnessed, never edited, recomputed or closed. Only
native references registered by this harness may be closed during cleanup.
Private state and receipts stay in the caller's output directory.
Run only with exclusive Fusion automation from prepare through cleanup. The
workspace registry snapshot tracks newly opened owned documents; another agent
must not run workspace operations in that interval. This is an operating
requirement, not an application-wide lock.
"""
from __future__ import annotations

import argparse
import copy
import json
import struct
import textwrap
import time
import uuid
import zlib
from pathlib import Path

from fusion_cad_mcp import workspace
from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.envelope import parse_status_json
from fusion_cad_mcp.receipts import run_typed
from fusion_cad_mcp.tools import handle_tools, inspection, io, validation

COMMON = '''import adsk.core, adsk.fusion, json, sys, types, hashlib, math

def _state():
    return sys.modules['_fusion_field_followup_v1']

def _signature(doc):
    design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    row = {'modified': doc.isModified, 'saved': doc.isSaved, 'name': doc.name}
    if design is None: return row
    def box(obj):
        bb = obj.boundingBox
        return [[round(getattr(p, a), 9) for a in ('x', 'y', 'z')] for p in (bb.minPoint, bb.maxPoint)]
    components = []
    for component in design.allComponents:
        components.append({'name': component.name, 'sketches': component.sketches.count,
            'features': component.features.count,
            'bodies': [[b.name, round(b.volume, 9), box(b)] for b in component.bRepBodies],
            'meshes': [[b.name, b.mesh.nodeCount, b.mesh.triangleCount, box(b)] for b in component.meshBodies]})
    row['components'] = components
    row['occurrences'] = [[o.fullPathName, list(o.transform2.asArray())] for o in design.rootComponent.allOccurrences]
    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        row['timeline'] = [design.timeline.count, design.timeline.markerPosition]
        row['parameters'] = [[p.name, p.expression, p.value, p.unit] for p in design.userParameters]
    return row

def _originals_intact():
    state = _state()
    return all(d.isValid and _signature(d) == state.signatures[i] for i,d in enumerate(state.originals))

def _fixture(name):
    state = _state()
    doc = state.fixtures[name]
    assert doc.isValid and any(doc == d for d in state.owned)
    assert doc.activate()
    assert adsk.core.Application.get().activeDocument == doc
    return doc, adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
'''


def adapter_for(folder: Path, timeout=120):
    class IsolatedAdapter(FusionAdapter):
        receipt_journal_dir = str(folder / 'receipts')

        def execute_typed_script(self, script, **kwargs):
            return run_typed(self.execute_script, script, self.url, self.timeout,
                             journal_dir=self.receipt_journal_dir, **kwargs)

    return IsolatedAdapter(timeout=timeout)


def checked(env):
    result = parse_status_json(env) or env.result
    if not env.ok or not result or result.get('ok') is False:
        # No raw stdout: Autodesk may include unrelated historical output.
        raise RuntimeError(json.dumps({'ok': env.ok, 'error': env.error,
                                       'state': env.state, 'result': result}))
    return result


def native(adapter, body: str, read_only=False):
    script = COMMON + '\ndef run(_ctx):\n' + textwrap.indent(textwrap.dedent(body), '    ')
    return checked(adapter.execute_typed_script(script, read_only=read_only))


def prepare(adapter, folder):
    return native(adapter, '''
        app = adsk.core.Application.get()
        assert '_fusion_field_followup_v1' not in sys.modules, 'Existing test session requires inspection'
        state = types.ModuleType('_fusion_field_followup_v1')
        state.originals = list(app.documents)
        state.previous = app.activeDocument
        state.signatures = [_signature(d) for d in state.originals]
        state.owned = []
        state.fixtures = {}
        sys.modules['_fusion_field_followup_v1'] = state
        print(json.dumps({'ok': True, 'original_document_count': len(state.originals),
                          'fusion_version': app.version, 'originals_intact': _originals_intact()}))
    ''', read_only=True)


def fixture(adapter):
    return native(adapter, '''
        app = adsk.core.Application.get(); state = _state()
        assert not state.owned, 'Fixtures already created'
        assert app.userInterface.activeCommand == 'SelectCommand'
        for key in ('primary', 'protected'):
            doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            state.owned.append(doc); state.fixtures[key] = doc
            doc.name = 'Field verification ' + key
            design = adsk.fusion.Design.cast(app.activeProduct); root = design.rootComponent
            design.userParameters.add('wall', adsk.core.ValueInput.createByString('10 mm'), 'mm', '')
            for name, x, length in (('Editable', 0, 'wall'), ('Protected', 5, '10 mm')):
                sk = root.sketches.add(root.xYConstructionPlane); sk.name = name + ' profile'
                sk.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(x,0,0), adsk.core.Point3D.create(x+2,3,0))
                assert sk.profiles.count == 1
                feature = root.features.extrudeFeatures.addSimple(sk.profiles.item(0), adsk.core.ValueInput.createByString(length), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
                feature.name = name + ' extrusion'; feature.bodies.item(0).name = name
            if key == 'primary':
                matrix = adsk.core.Matrix3D.create()
                matrix.translation = adsk.core.Vector3D.create(10,2,0)
                outer = root.occurrences.addNewComponent(matrix); outer.component.name = 'Outer'
                matrix = adsk.core.Matrix3D.create()
                matrix.setToRotation(math.pi/2, adsk.core.Vector3D.create(0,0,1), adsk.core.Point3D.create(0,0,0))
                matrix.translation = adsk.core.Vector3D.create(0,4,1)
                inner = outer.component.occurrences.addNewComponent(matrix); inner.component.name = 'Reference'
            assert design.computeAll()
        _fixture('primary')
        state.protected_signature = _signature(state.fixtures['protected'])
        assert _originals_intact()
        print(json.dumps({'ok': True, 'fixtures_created': 2, 'originals_intact': True}))
    ''')


def inspect(adapter):
    return native(adapter, '''
        state = _state()
        print(json.dumps({'ok': True, 'originals_intact': _originals_intact(),
            'protected_intact': _signature(state.fixtures['protected']) == state.protected_signature,
            'owned_open': sum(d.isValid for d in state.owned)}))
    ''', read_only=True)


def cleanup(adapter):
    return native(adapter, '''
        state = _state()
        assert _originals_intact(), 'Original document evidence changed; inspect before cleanup'
        assert _signature(state.fixtures['protected']) == state.protected_signature
        count = 0
        for doc in reversed(state.owned):
            if doc.isValid:
                assert all(doc != d for d in state.originals)
                assert doc.close(False)
                count += 1
        if state.previous and state.previous.isValid: assert state.previous.activate()
        assert _originals_intact()
        del sys.modules['_fusion_field_followup_v1']
        print(json.dumps({'ok': True, 'closed_owned_documents': count, 'originals_intact': True}))
    ''')


def core_checks(adapter, folder):
    native(adapter, "_fixture('primary')\nprint(json.dumps({'ok': True}))", read_only=True)
    missing = folder / 'must-stay-absent' / 'missing.stl'
    operation = str(uuid.uuid4())
    failure = io.export(adapter, 'stl', str(missing), body='Does not exist', operation_id=operation)
    assert not failure.ok and failure.error == 'body_not_found', failure.to_dict()
    assert failure.state['execution_outcome'] == 'not_started', failure.to_dict()
    assert not missing.parent.exists()
    good = checked(io.export(adapter, 'stl', str(folder / 'plate.stl'), body='Editable',
                             operation_id=str(uuid.uuid4())))
    assert good['bytes_written'] > 84
    replay = io.export(adapter, 'stl', str(missing), body='Does not exist', operation_id=operation)
    assert not replay.ok and not missing.parent.exists()

    def faces():
        a = checked(inspection.query_geometry(adapter, 'Editable', 'face',
            {'normal': [0, 0, 1]}, expected_count=1))['items'][0]['handle']
        b = checked(inspection.query_geometry(adapter, 'Editable', 'face',
            {'normal': [0, 0, -1]}, expected_count=1))['items'][0]['handle']
        return a, b

    a, b = faces()
    spec = {'type': 'face_distance', 'face_a': a, 'face_b': b,
            'min_mm': 10, 'max_mm': 10, 'tolerance_mm': 1e-6}
    positive = checked(validation.validate_design(adapter, [spec]))
    assert positive['passed'], positive
    negative = checked(validation.validate_design(adapter, [{**spec, 'max_mm': 9, 'min_mm': 0}]))
    assert not negative['passed'] and negative['checks'][0]['status'] == 'violation', negative
    same = checked(validation.validate_design(adapter, [{**spec, 'face_b': a}]))
    assert not same['passed'] and same['checks'][0]['status'] == 'unavailable', same
    stale = checked(validation.validate_design(adapter, [{**spec, 'face_b': b.rsplit(':',1)[0]+':missing-token'}]))
    assert not stale['passed'], stale
    witness = positive['checks'][0]['evidence']
    return {'ok': True, 'export_preflight_not_started': True, 'subsequent_export_bytes': good['bytes_written'],
            'face_distance_mm': witness['minimum_distance_mm'], 'failed_requirement_reported': True,
            'same_face_refused': True, 'stale_face_refused': True, **inspect(adapter)}


def mesh_fixture(folder):
    root = folder / 'source-bundle'
    root.mkdir(exist_ok=True)
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    pixels = b''.join(b'\0' + b''.join(
        (b'\xff\x20\x20' if (x // 8 + y // 8) % 2 else b'\x20\xff\x20')
        for x in range(32)) for y in range(32))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B',32,32,8,2,0,0,0))
    png += chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')
    (root / 'checker.png').write_bytes(png)
    (root / 'reference.mtl').write_text('newmtl Checker\nKd 1 1 1\nmap_Kd checker.png\n', encoding='utf-8')
    path = root / 'reference.obj'
    path.write_text('''mtllib reference.mtl
o DoubleSurface
v 0 0 0
v 20 0 0
v 20 20 0
v 0 20 0
v 0 0 10
v 20 0 10
v 20 20 10
v 0 20 10
vt 0 0
vt 1 0
vt 1 1
vt 0 1
usemtl Checker
f 1/1 2/2 3/3
f 1/1 3/3 4/4
f 5/1 6/2 7/3
f 5/1 7/3 8/4
''', encoding='utf-8')
    return path


def appearance_probe(adapter, fixture_name):
    """Diagnostic only: API UV/appearance evidence does not certify rendering."""
    return native(adapter, f'''
        doc,design=_fixture({fixture_name!r})
        occ=next(o for o in design.rootComponent.allOccurrences if o.fullPathName=='Outer:1+Reference:1')
        body=occ.component.meshBodies.item(0)
        row={{'ok':True,'appearance_verification':'unknown','display_verification':'unknown'}}
        for key,read in (
            ('appearance_name',lambda:body.appearance.name),
            ('appearance_source_type',lambda:int(body.appearanceSourceType)),
            ('texture_map_control_present',lambda:body.textureMapControl is not None),
            ('uv_coordinates',lambda:list(body.displayMesh.textureCoordinatesAsDouble)),
        ):
            try: row[key]=read()
            except Exception: row[key]={{'status':'unavailable'}}
        print(json.dumps(row))
    ''', read_only=True)


def mesh_checks(adapter, folder):
    native(adapter, "_fixture('primary')\nprint(json.dumps({'ok': True}))", read_only=True)
    source = mesh_fixture(folder)
    operation = str(uuid.uuid4())
    target = 'Outer:1+Reference:1'
    result = checked(io.import_geometry(adapter, 'obj', str(source), target_component=target,
                                       units='mm', operation_id=operation))
    (folder / 'private-import.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    assert result['mesh_bodies_added'] == 1, result
    mesh = result['imported_objects'][0]
    assert mesh['node_count'] == 8 and mesh['triangle_count'] == 4, mesh
    assert mesh['local_bounds']['max'] == [20, 20, 10], mesh
    repeated = checked(io.import_geometry(adapter, 'obj', str(source), target_component=target,
                                         units='mm', operation_id=operation))
    assert repeated['imported_objects'] == result['imported_objects']
    changed = io.import_geometry(adapter, 'obj', str(source), target_component=target,
                                 units='cm', operation_id=operation)
    assert not changed.ok and changed.error == 'receipt_conflict', changed.to_dict()
    (folder / 'appearance-before.json').write_text(json.dumps(appearance_probe(adapter,'primary'),indent=2),encoding='utf-8')
    inventory = checked(inspection.inspect_assembly(adapter, target, include=['meshes'],
                       fields=['handles', 'bounds', 'transforms', 'mesh_geometry']))
    section = inventory['sections']['meshes']
    assert section['count'] == 1, section
    row = section['items'][0]
    assert 'geometry_witness' in row, row
    queries = [dict(origin_mm=[10, 7, -5], direction=[0,0,1], start_mm=0,end_mm=30),
               dict(origin_mm=[40, 7, -5], direction=[0,0,1], start_mm=0,end_mm=30)]
    rays = checked(handle_tools.ray_collision_with_mesh(adapter, mesh['handle'], queries=queries, frame='local'))
    assert rays['coverage']['hits'] == 1 and rays['coverage']['no_hit'] == 1, rays
    distances = [hit['travel_mm'] for hit in rays['queries'][0]['evidence']['hits']]
    assert len(distances) == 2 and all(abs(a-b)<1e-5 for a,b in zip(distances,[5,15],strict=True)), distances
    replay = checked(handle_tools.ray_collision_with_mesh(adapter, mesh['handle'], replay=rays['queries'][0]))
    assert replay['replay']['status'] == 'remeasured', replay
    native(adapter, '''
        doc,design=_fixture('primary')
        occ=next(o for o in design.rootComponent.allOccurrences if o.fullPathName=='Outer:1+Reference:1')
        _state().ray_pose=occ.transform2.copy()
        pose=occ.transform2
        pose.translation=adsk.core.Vector3D.create(pose.translation.x+1,pose.translation.y,pose.translation.z)
        occ.transform2=pose
        print(json.dumps({'ok':True}))
    ''')
    try:
        stale = checked(handle_tools.ray_collision_with_mesh(adapter, mesh['handle'], replay=rays['queries'][0]))
        assert stale['replay']['status'] == 'stale', stale
    finally:
        native(adapter, '''
            doc,design=_fixture('primary')
            occ=next(o for o in design.rootComponent.allOccurrences if o.fullPathName=='Outer:1+Reference:1')
            occ.transform2=_state().ray_pose
            assert design.computeAll()
            print(json.dumps({'ok':True}))
        ''')
    root_rays = checked(handle_tools.ray_collision_with_mesh(adapter, mesh['handle'], queries=[
        dict(origin_mm=[93,70,5],direction=[0,0,1],start_mm=0,end_mm=30)],frame='root'))
    assert root_rays['coverage']['hits'] == 1, root_rays
    root_points = [hit['point_mm'] for hit in root_rays['queries'][0]['evidence']['hits']]
    assert all(abs(p[0]-93)<1e-5 and abs(p[1]-70)<1e-5 for p in root_points),root_points
    assert all(abs(p[2]-z)<1e-5 for p,z in zip(root_points,[10,20],strict=True)),root_points
    ranged=checked(handle_tools.ray_collision_with_mesh(adapter,mesh['handle'],queries=[
        {**queries[0],'start_mm':6,'end_mm':20,'selection':'farthest'}],frame='local'))
    assert len(ranged['queries'][0]['evidence']['hits'])==1,ranged
    (folder / 'private-mesh.json').write_text(json.dumps({'import':result, 'inventory':inventory, 'rays':rays},indent=2),encoding='utf-8')
    return {'ok': True, 'imported_meshes': section['count'], 'source_nodes': mesh['node_count'],
            'source_triangles': mesh['triangle_count'], 'local_bounds_mm': mesh['local_bounds'],
            'root_bounds_mm': mesh['root_bounds'], 'ray_travel_mm':distances,
            'root_ray_points_mm':root_points,'replay_remeasured':True,'changed_pose_replay_stale':True,'finite_range_filtered':True,
            'operation_id_replay_deduplicated':True,'operation_id_changed_inputs_refused':True,
            **inspect(adapter)}


def nested_face_checks(adapter):
    native(adapter, '''
        doc,design=_fixture('primary')
        occ=next(o for o in design.rootComponent.allOccurrences if o.fullPathName=='Outer:1+Reference:1')
        comp=occ.component
        assert comp.bRepBodies.count==0
        sk=comp.sketches.add(comp.xYConstructionPlane);sk.name='Nested profile'
        sk.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(3,0,0),adsk.core.Point3D.create(4,1,0))
        f=comp.features.extrudeFeatures.addSimple(sk.profiles.item(0),adsk.core.ValueInput.createByString('5 mm'),adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        f.bodies.item(0).name='NestedWall'
        assert design.computeAll()
        print(json.dumps({'ok':True}))
    ''')
    selector='Outer:1+Reference:1 / NestedWall'
    pair=[checked(inspection.query_geometry(adapter,selector,'face',{'normal':[0,0,d]},expected_count=1))['items'][0]['handle'] for d in (1,-1)]
    result=checked(validation.validate_design(adapter,[dict(type='face_distance',face_a=pair[0],face_b=pair[1],min_mm=5,max_mm=5,tolerance_mm=1e-6)]))
    assert result['passed'],result
    evidence=result['checks'][0]['evidence']
    points=[evidence['closest_point_a_mm'],evidence['closest_point_b_mm']]
    assert all(90-1e-6<=p[0]<=100+1e-6 and 90-1e-6<=p[1]<=100+1e-6 for p in points),points
    assert abs(abs(points[0][2]-points[1][2])-5)<1e-6,points
    return {'ok':True,'nested_face_distance_mm':evidence['minimum_distance_mm'],
            'root_witness_points_mm':points,**inspect(adapter)}


def mesh_guard_checks(adapter, folder):
    native(adapter, '''
        state=_state(); app=adsk.core.Application.get()
        assert 'mesh_guards' not in state.fixtures
        doc=app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        state.owned.append(doc); state.fixtures['mesh_guards']=doc
        doc.name='Field import guards'
        print(json.dumps({'ok':True}))
    ''')
    source=folder/'source-bundle'/'reference.obj'
    first=checked(io.import_geometry(adapter,'obj',str(source),units='mm'))
    assert first['mesh_bodies_added']==1,first
    snapshot=checked(inspection.inspect_assembly(adapter,include=['meshes'],fields=['handles','mesh_geometry']))
    original=snapshot['sections']['meshes']['items'][0]['geometry_witness']
    second=source.with_name('second.obj')
    second.write_bytes(source.read_bytes())
    imported=checked(io.import_geometry(adapter,'obj',str(second),units='cm'))
    assert imported['mesh_bodies_added']==1,imported
    snapshot=checked(inspection.inspect_assembly(adapter,include=['meshes'],fields=['handles','mesh_geometry']))
    rows=snapshot['sections']['meshes']['items']
    assert snapshot['sections']['meshes']['count']==2,snapshot
    assert next(r for r in rows if r['name']=='reference.obj')['geometry_witness']==original,rows
    assert imported['imported_objects'][0]['local_bounds']['max']==[200,200,100],imported
    missing=source.with_name('missing.obj')
    missing.write_text('mtllib nonexistent.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n',encoding='utf-8')
    for bad in (io.import_geometry(adapter,'obj',str(source)),
                io.import_geometry(adapter,'obj',str(missing),units='mm')):
        assert not bad.ok and bad.error=='invalid_input',bad.to_dict()
    assert checked(inspection.inspect_assembly(adapter,include=['meshes'],detail='summary'))['sections']['meshes']['count']==2
    return {'ok':True,'two_meshes_preserved':True,'centimetre_units_verified':True,
            'missing_units_and_resources_refused':True,**inspect(adapter)}


def collect_workspace_documents(adapter):
    return native(adapter, '''
        state=_state(); reg=sys.modules['_fusion_cad_workspace_v1']
        for token,entry in reg.entries.items():
            if token in state.workspace_before or not entry['owned']: continue
            doc=entry['document']
            assert all(doc != d for d in state.originals)
            if doc.isValid and all(doc != d for d in state.owned):
                state.owned.append(doc); state.fixtures['experiment']=doc
        print(json.dumps({'ok':True}))
    ''', read_only=True)


def workspace_checks(adapter, folder):
    native(adapter, "_fixture('primary')\nprint(json.dumps({'ok': True}))", read_only=True)
    bound = checked(workspace.bind(adapter))['target_handle']
    scope = {'version': 1, 'path': 'root', 'sections': [
        {'kind': 'body', 'names': ['Editable', 'Protected'], 'fields': ['bounds_mm','volume_mm3'],
         'tolerances': {'bounds_mm': 1e-6, 'volume_mm3': 1e-3}},
        {'kind': 'parameter', 'names': ['wall'], 'fields': ['expression','value_mm','units'],
         'tolerances': {'value_mm': 1e-6}},
    ]}
    mesh_scope = {'version': 1, 'path': 'Outer:1+Reference:1', 'sections': [
        {'kind': 'mesh', 'names': ['reference.obj'], 'fields': ['witness'],
         'tolerances': {'witness': {'bounds_mm': 1e-6, 'transform_cm': 1e-8}}},
    ]}
    def requirement(before, expected):
        return {'id': 'thickness', 'metric': 'body_extent', 'entities': ['root / Editable'],
                'axis': 'z', 'frame': 'root', 'units': 'mm', 'baseline': before,
                'baseline_tolerance': 1e-6, 'comparator': 'eq', 'expected': expected, 'tolerance': 1e-6}
    checks = [requirement(10, 10)]
    initial = checked(workspace.inspect_target(adapter, bound, 'wall', checks, scope=scope))
    assert initial['evidence_status']['status'] == 'passed', initial
    reordered = copy.deepcopy(initial['evidence'])
    reordered['records'].reverse()
    compared = checked(workspace.inspect_target(adapter, bound, 'wall', checks, scope=scope, baseline=reordered))
    assert compared['evidence_comparison']['status'] == 'passed', compared
    missing = copy.deepcopy(reordered)
    missing['records'].pop()
    compared = checked(workspace.inspect_target(adapter, bound, 'wall', checks, scope=scope, baseline=missing))
    assert compared['evidence_comparison']['status'] == 'failed', compared
    project = folder / 'workspace'
    checked(workspace.initialize(str(project)))
    native(adapter, '''
        _state().workspace_before=set(sys.modules['_fusion_cad_workspace_v1'].entries)
        print(json.dumps({'ok':True}))
    ''', read_only=True)
    try:
        checkpoint_env = workspace.checkpoint(adapter, str(project), bound, 'mesh-seed', checks,
            'wall', accepted=True, evidence_scope=mesh_scope)
    finally:
        collect_workspace_documents(adapter)
    checkpoint = checked(checkpoint_env)
    assert checkpoint['checkpoint_kind'] == 'accepted' and checkpoint['roundtrip_passed'], checkpoint
    handle = checkpoint['target_handle']
    (folder/'appearance-reopened.json').write_text(json.dumps(appearance_probe(adapter,'experiment'),indent=2),encoding='utf-8')
    before = checked(workspace.inspect_target(adapter, handle, 'wall', checks, scope=scope))
    protected = [{'id':'protected','metric':'body_volume','entities':['root / Protected'],
                  'frame':'root','units':'mm^3','baseline':6000,'baseline_tolerance':1e-3,'tolerance':1e-3}]
    def contract(start, end, expected=None):
        return dict(operation_id=str(uuid.uuid4()), target_handle=handle, parameter_name='wall',
                    expression=f'{end} mm', expected_expression=f'{start} mm', expected_value_mm=start,
                    value_tolerance_mm=1e-6, requirements=[requirement(start, end if expected is None else expected)],
                    protected=protected)
    timings=[]
    for start,end in [(10,12),(12,10)]:
        began=time.perf_counter()
        result=checked(workspace.edit(adapter,str(project),contract(start,end)))
        assert result['verification']=='verified',result
        a=checked(inspection.query_geometry(adapter,'Editable','face',{'normal':[0,0,1]},expected_count=1))['items'][0]['handle']
        b=checked(inspection.query_geometry(adapter,'Editable','face',{'normal':[0,0,-1]},expected_count=1))['items'][0]['handle']
        measured=checked(validation.validate_design(adapter,[{'type':'face_distance','face_a':a,'face_b':b,
            'min_mm':end,'max_mm':end,'tolerance_mm':1e-6}]))
        assert measured['passed'],measured
        timings.append((time.perf_counter()-began)*1000)
    restored=checked(workspace.inspect_target(adapter,handle,'wall',checks,scope=scope,baseline=before['evidence']))
    assert restored['evidence_comparison']['status']=='passed',restored
    stale=workspace.edit(adapter,str(project),contract(12,13))
    assert not stale.ok and stale.result['execution']=='refused',stale.to_dict()
    failed=workspace.edit(adapter,str(project),contract(10,11,12))
    assert not failed.ok and failed.result['verification']=='failed',failed.to_dict()
    checked(workspace.edit(adapter,str(project),contract(11,10)))
    current=checked(workspace.inspect_target(adapter,handle,'wall',checks,scope=scope,baseline=before['evidence']))
    assert current['evidence_comparison']['status']=='passed',current
    try:
        restored_copy=checked(workspace.restore(adapter,str(project),'mesh-seed'))
        assert restored_copy['verification']=='not_run',restored_copy
        restored_state=checked(workspace.inspect_target(adapter,restored_copy['target_handle'],'wall',checks,scope=mesh_scope))
        assert restored_state['health']['unknown']==0 and restored_state['evidence_status']['status']=='passed',restored_state
        native(adapter, '''
            state=_state(); app=adsk.core.Application.get()
            assert all(app.activeDocument != d for d in state.originals)
            print(json.dumps({'ok':True}))
        ''',read_only=True)
        seed=project/'assets'/'mesh-seed.f3d'
        seed.write_bytes((project/'.fusion'/'blobs'/checkpoint['artifact_hash']).read_bytes())
        edit=contract(10,12)
        manifest={'schema':1,'seed':'assets/mesh-seed.f3d',
                  'edit':{k:edit[k] for k in ('parameter_name','expression','expected_expression','expected_value_mm','value_tolerance_mm')},
                  'requirements':edit['requirements'],'protected':protected}
        (project/'design.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        builds=[]
        for branch in ('mesh-build','mesh-cache'):
            builds.append(checked(workspace.build(adapter,str(project),branch=branch)))
        assert builds[0]['verification']=='verified' and not builds[0]['cache_hit'],builds
        assert builds[1]['verification']=='verified' and builds[1]['cache_hit'] and builds[1]['construction_calls']==0,builds
        final_mesh=checked(workspace.inspect_target(adapter,builds[-1]['target_handle'],'wall',[],scope=mesh_scope))
        assert final_mesh['evidence_status']['status']=='passed',final_mesh
        assert restored_state['evidence']['records'][0]['fields']['witness']['value']['geometry_sha256']==final_mesh['evidence']['records'][0]['fields']['witness']['value']['geometry_sha256']
    finally:
        collect_workspace_documents(adapter)
    (folder/'private-workspace.json').write_text(json.dumps({'checkpoint':checkpoint,'initial':initial,
        'restored':current,'stale':stale.to_dict(),'failed':failed.to_dict(),
        'restored_copy':restored_copy,'mesh_builds':builds},indent=2),encoding='utf-8')
    return {'ok':True,'reordered_records_pass':True,'missing_record_fails':True,
            'mesh_checkpoint_roundtrip':True,'parameter_propagation_and_restoration':True,
            'stale_baseline_refused':True,'deliberate_requirement_failed':True,
            'mesh_restore_recomputed':True,'mesh_seed_build_verified':True,'mesh_build_cache_reverified':True,
            'edit_and_face_feedback_ms':timings,**inspect(adapter)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true', required=True,
                        help='Confirm exclusive Fusion automation from prepare through cleanup')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('action', choices=('prepare', 'fixture', 'inspect', 'cleanup', 'core', 'mesh', 'workspace', 'nested', 'mesh_guards'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with adapter_for(args.output) as adapter:
        if args.action == 'prepare':
            result = prepare(adapter, args.output)
        elif args.action in ('core', 'mesh', 'workspace'):
            result = globals()[args.action + '_checks'](adapter, args.output)
        elif args.action == 'nested':
            result = nested_face_checks(adapter)
        elif args.action == 'mesh_guards':
            result = mesh_guard_checks(adapter, args.output)
        else:
            result = globals()[args.action](adapter)
    (args.output / (args.action + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
