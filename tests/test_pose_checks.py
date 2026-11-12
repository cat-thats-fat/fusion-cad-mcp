"""Pose checks use a small independent box kernel, not Fusion geometry."""
import json
import math
import uuid
from types import SimpleNamespace as NS

import pytest

from fusion_cad_mcp import receipts
from fusion_cad_mcp.tools.validation import build_validate_design, validate_design
from tests._assembly_fakes import Collection, point
from tests.test_validation import (
    Adapter,
    _assert_bounded_receipt,
    _box_surface_adapter,
    _receipt_validation,
    surface_check,
)


class Box:
    def __init__(self, name, low, high, parent=None):
        self.name, self.parentComponent = name, parent
        self.points = [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
        self.faces, self.isSolid, self.isValid = Collection(range(6)), True, True
        self.nativeObject, self.assemblyContext = None, None

    @property
    def boundingBox(self):
        return NS(minPoint=point(*[min(p[i] for p in self.points) for i in range(3)]),
                  maxPoint=point(*[max(p[i] for p in self.points) for i in range(3)]))

    @property
    def volume(self):
        box = self.boundingBox
        return math.prod(getattr(box.maxPoint, a) - getattr(box.minPoint, a) for a in 'xyz')


class Matrix:
    def __init__(self):
        self.angle, self.pivot, self.translation = 0, point(), point()

    def setToRotation(self, angle, axis, pivot):
        assert (axis.x, axis.y, axis.z) == (0, 0, 1)
        self.angle, self.pivot = angle, pivot
        return True


class Kernel:
    def __init__(self):
        self.fail = None

    def copy(self, body):
        copy = Box('temporary', (0, 0, 0), (1, 1, 1))
        copy.points = list(body.points)
        return copy

    def transform(self, body, matrix):
        c, s, pivot, t = math.cos(matrix.angle), math.sin(matrix.angle), matrix.pivot, matrix.translation
        body.points = [(pivot.x + c*(x-pivot.x) - s*(y-pivot.y) + t.x,
                        pivot.y + s*(x-pivot.x) + c*(y-pivot.y) + t.y, z+t.z) for x, y, z in body.points]
        return True

    def booleanOperation(self, target, tool, _kind):
        if self.fail == 'false':
            return False
        if self.fail == 'throw':
            raise RuntimeError('ASM_EDGECOIN')
        a, b = target.boundingBox, tool.boundingBox
        low = [max(getattr(a.minPoint, c), getattr(b.minPoint, c)) for c in 'xyz']
        high = [min(getattr(a.maxPoint, c), getattr(b.maxPoint, c)) for c in 'xyz']
        if any(low[i] >= high[i] for i in range(3)):
            raise RuntimeError('contact')
        target.points = Box('intersection', low, high).points
        return True


def fixture(monkeypatch):
    a, b, unrelated = Box('a', (0, 0, 0), (1, 2, 1)), Box('b', (2, 0, 0), (3, 1, 1)), Box('other', (20, 0, 0), (21, 1, 1))
    adapter = Adapter(monkeypatch, [a, b, unrelated])
    for obj in (a, b, unrelated):
        obj.parentComponent = adapter.root
    import adsk.core
    import adsk.fusion
    adsk.core.Matrix3D = NS(create=Matrix)
    adsk.core.Vector3D = NS(create=point)
    adsk.core.Point3D = NS(create=point)
    kernel = Kernel()
    adsk.fusion.TemporaryBRepManager = NS(get=lambda: kernel)
    adsk.fusion.BooleanTypes = NS(IntersectionBooleanType=1)
    return adapter, kernel, (a, b, unrelated)


def check(poses, **kwargs):
    return {'type': 'pose_interference', 'body_a': 'a', 'body_b': 'b', 'frame': 'root', 'poses': poses, **kwargs}


def pose(name, translation, **kwargs):
    return {'id': name, 'translation_mm': translation, **kwargs}


def test_sampled_transforms_and_box_intersection_preserve_originals(monkeypatch):
    adapter, _, bodies = fixture(monkeypatch)
    before = [list(body.points) for body in bodies]
    poses = [pose('separate', [0, 0, 0]), pose('overlap', [15, 0, 0]),
             pose('rotate', [30, 0, 0], rotation_deg=90, pivot_mm=[0, 0, 0])]
    row = validate_design(adapter, [check(poses)]).result['checks'][0]
    assert adapter.read_only and row['status'] == 'violation'
    assert [x['status'] for x in row['poses']] == ['pass', 'violation', 'violation']
    assert row['poses'][1]['overlap_mm3'] == pytest.approx(500)
    assert row['poses'][2]['overlap_mm3'] == pytest.approx(1000)
    assert [body.points for body in bodies] == before


@pytest.mark.parametrize('failure', ['false', 'throw'])
def test_failed_boolean_is_unavailable_and_independent_samples_continue(monkeypatch, failure):
    adapter, kernel, _ = fixture(monkeypatch)
    kernel.fail = failure
    row = validate_design(adapter, [check([pose('fail', [15, 0, 0]), pose('separate', [0, 0, 0])])]).result['checks'][0]
    assert row['status'] == 'unavailable'
    assert [x['status'] for x in row['poses']] == ['unavailable', 'pass']


def test_violation_beats_unavailable(monkeypatch):
    adapter, kernel, _ = fixture(monkeypatch)
    old = kernel.booleanOperation
    count = 0
    def intermittent(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('ASM_EDGECOIN')
        return old(*args)
    kernel.booleanOperation = intermittent
    row = validate_design(adapter, [check([pose('hit', [15, 0, 0]), pose('failed', [15, 0, 0])])]).result['checks'][0]
    assert row['status'] == 'violation' and row['poses'][1]['status'] == 'unavailable'


def test_same_ambiguous_and_stale_are_unavailable(monkeypatch):
    adapter, _, bodies = fixture(monkeypatch)
    adapter.root.bRepBodies.add(Box('a', (0, 0, 0), (1, 1, 1), adapter.root))
    checks = [check([pose('p', [0, 0, 0])]), check([pose('p', [0, 0, 0])], body_a='b'),
              check([pose('p', [0, 0, 0])], body_a='body:a|doc=646f63:missing')]
    rows = validate_design(adapter, checks).result['checks']
    assert [r['status'] for r in rows] == ['unavailable'] * 3
    assert rows[0]['reason']['error'] == 'body_ambiguous' and rows[1]['reason'] == 'same_body'
    assert rows[2]['reason']['error'] == 'handle_invalid'


def test_nested_occurrence_transform_applied_once(monkeypatch):
    adapter, _, bodies = fixture(monkeypatch)
    a, _, _ = bodies
    transform = Matrix()
    transform.translation = point(1.5, 0, 0)
    proxy = Box('a', (0, 0, 0), (1, 2, 1))
    occ = NS(fullPathName='Parent:1+Child:1', component=NS(name='Child'), transform2=transform)
    proxy.nativeObject, proxy.assemblyContext = a, occ
    occ.bRepBodies = Collection([proxy])
    adapter.root.bRepBodies.items.remove(a)
    adapter.root.allOccurrences = Collection([occ])
    row = validate_design(adapter, [check([pose('nested', [0, 0, 0])], body_a='Parent:1+Child:1 / a')]).result['checks'][0]
    assert row['status'] == 'violation' and row['poses'][0]['overlap_mm3'] == pytest.approx(500)


@pytest.mark.parametrize('alteration', [
    {'frame': 'local'}, {'poses': []}, {'poses': [pose('p', [0, 0, float('nan')])]},
    {'poses': [pose('p', [0, 0, 0], rotation_axis=[0, 0, 0])]},
    {'poses': [pose('p', [0, 0, 0])] * 2}, {'max_overlap_mm3': -1},
    {'poses': [pose(str(i), [0, 0, 0]) for i in range(33)]},
])
def test_invalid_inputs_rejected(alteration):
    with pytest.raises(ValueError):
        build_validate_design([{**check([pose('p', [0, 0, 0])]), **alteration}])


def test_total_pose_budget_and_face_caps(monkeypatch):
    checks = [check([pose(str(i), [0, 0, 0]) for i in range(32)])] * 5
    with pytest.raises(ValueError, match='128'):
        build_validate_design(checks)
    adapter, _, bodies = fixture(monkeypatch)
    bodies[0].faces = Collection(range(513))
    row = validate_design(adapter, [check([pose('p', [0, 0, 0])])]).result['checks'][0]
    assert row['status'] == 'unavailable' and 'face limit' in row['poses'][0]['reason']


def test_contact_and_empty_boolean_are_unavailable(monkeypatch):
    adapter, kernel, _ = fixture(monkeypatch)
    contact = validate_design(adapter, [check([pose('contact', [10, 0, 0])])]).result['checks'][0]
    assert contact['status'] == 'unavailable'
    def empty(target, tool, kind):
        target.points = [(0, 0, 0)] * 8
        return True
    kernel.booleanOperation = empty
    row = validate_design(adapter, [check([pose('empty', [15, 0, 0])])]).result['checks'][0]
    assert row['status'] == 'unavailable' and 'empty' in row['poses'][0]['reason']


def test_overlap_threshold_inclusive(monkeypatch):
    adapter, _, _ = fixture(monkeypatch)
    rows = validate_design(adapter, [check([pose('p', [15, 0, 0])], max_overlap_mm3=500),
                                     check([pose('p', [15, 0, 0])], max_overlap_mm3=499)]).result['checks']
    assert [row['status'] for row in rows] == ['pass', 'violation']


def test_pose_diagnostic_budget_preserves_all_samples_in_receipt(monkeypatch, tmp_path):
    adapter, kernel, _ = fixture(monkeypatch)
    def fail(*args):
        raise RuntimeError('\x00\u754c' * 5000)
    kernel.booleanOperation = fail
    ids = [f'{i:03}' + '\x01' * 20 for i in range(32)]
    checks = [check([pose(name, [15, 0, 0]) for name in ids]) for _ in range(4)]
    env = receipts.run_typed(adapter.execute_script, build_validate_design(checks), 'endpoint', 1,
                             str(uuid.uuid4()), read_only=True, journal_dir=tmp_path)
    assert env.ok, env
    result = json.loads(env.message)
    assert not result['passed']
    assert len(result['checks']) == 4
    rows = [row for group in result['checks'] for row in group['poses']]
    assert len(rows) == 128
    assert all(row['status'] == 'unavailable' and row['reason_truncated'] for row in rows)
    assert all(len(json.dumps(row['reason'], ensure_ascii=True).encode()) - 2 <= 512 for row in rows)
    terminal = tmp_path / (env.state['operation_id'] + '.terminal.json')
    assert terminal.stat().st_size < 256 * 1024


def test_pose_id_byte_limits_and_unstringifiable_exception(monkeypatch):
    with pytest.raises(ValueError, match='escaped JSON bytes'):
        build_validate_design([check([pose('\x00' * 22, [0, 0, 0])])])
    with pytest.raises(ValueError, match='UTF-8'):
        build_validate_design([check([pose('\u00e9' * 65, [0, 0, 0])])])
    adapter, kernel, _ = fixture(monkeypatch)
    class BadError(Exception):
        def __str__(self): raise ValueError('unprintable')
    def fail(*args): raise BadError()
    kernel.booleanOperation = fail
    result = validate_design(adapter, [check([pose('p', [15, 0, 0])])]).result
    row = result['checks'][0]['poses'][0]
    assert not result['passed'] and row['status'] == 'unavailable'
    assert row['reason'] == 'exception message unavailable' and not row['reason_truncated']


@pytest.mark.parametrize('character', ['\x01', '😀'])
def test_combined_maximum_pose_and_surface_diagnostics_fit_real_receipt(monkeypatch, tmp_path, character):
    path = 'Parent:1+' + 'x' * 270
    adapter, target = _box_surface_adapter(monkeypatch, occurrence_path=path)
    import adsk.core
    import adsk.fusion
    target.assemblyContext.bRepBodies = Collection([])
    for name, low, high in [('a', (0, 0, 0), (1, 2, 1)), ('b', (2, 0, 0), (3, 1, 1))]:
        adapter.root.bRepBodies.add(Box(name, low, high, adapter.root))
    adsk.core.Matrix3D = NS(create=Matrix)
    adsk.core.Vector3D = NS(create=point)
    kernel = Kernel()
    def failed_intersection(*args):
        raise RuntimeError('\x00界' * 50000)
    kernel.booleanOperation = failed_intersection
    adsk.fusion.TemporaryBRepManager = NS(get=lambda: kernel)
    adsk.fusion.BooleanTypes = NS(IntersectionBooleanType=1)
    native = adapter.app.measureManager.measureMinimumDistance.side_effect
    def partly_failed(p, face):
        if any(face is selected for selected in target.faces.items[:3]):
            raise RuntimeError('\x00界' * 50000)
        return native(p, face)
    adapter.app.measureManager.measureMinimumDistance.side_effect = partly_failed
    # IDs use exactly the accepted 128 escaped bytes; names use all 100 allowed
    # characters, testing both control expansion and astral surrogate expansion.
    ids = [f'{index:02d}' + '\x01' * 21 for index in range(32)]
    checks = [check([pose(name, [15, 0, 0]) for name in ids]) for _ in range(4)]
    names = [f'{index:02d}' + character * 98 for index in range(46)]
    checks += [surface_check(name=name, body=f'body:box|occ={path}|doc=646f63:box',
                             point_mm=[15, 65, 95], max_mm=1000) for name in names]
    env = _receipt_validation(adapter, checks, tmp_path)
    _assert_bounded_receipt(env, tmp_path)
    assert not env.result['passed'] and len(env.result['checks']) == 50
    assert env.result['output_compaction'] != ['input_echoes']
    for group in env.result['checks'][:4]:
        assert group['status'] == 'unavailable'
        assert [row['id'] for row in group['poses']] == ids
        assert all(row['status'] == 'unavailable' and row['reason_truncated'] for row in group['poses'])
    assert [row['evidence']['name'] for row in env.result['checks'][4:]] == names
    for group in env.result['checks'][4:]:
        evidence = group['evidence']
        assert group['status'] == 'pass' and evidence['proof'] == 'validated_witness_upper_bound'
        assert evidence['face_failure_count'] == 3
        assert evidence['face_failures_omitted'] + len(evidence['face_failures']) == 3
        for sample in (evidence['minimum_scalar_candidate'], evidence['satisfying_witness']):
            assert sample['scalar_valid'] and sample['minimum_distance_mm'] > 0
            assert sample['witness_consistency']['status'] == 'consistent'
            assert sample['surface_membership']['status'] == 'on_face'
            assert sample['surface_membership']['tolerance_mm'] == 1e-6
            assert len(sample['closest_point_a_mm']) == len(sample['closest_point_b_mm']) == 3
            assert len(sample['surface_membership']['membership_point_mm']) == 3
        assert evidence['satisfying_witness']['witness_upper_bound_mm'] <= 1000
