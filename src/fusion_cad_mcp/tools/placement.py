"""Create and place local Fusion components with bounded, explicit recovery."""
from __future__ import annotations

import math
import textwrap
from typing import Any

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..handles import emit_resolve, emit_scope_expr, is_handle, parse_handle
from ..mutation import MUTATION_SCRIPT
from ..resolver import RESOLVER_SCRIPT


def _name(value: Any, label: str, *, optional: bool = False, selector: bool = False) -> str | None:
    if value is None and optional:
        return None
    limit = 4096 if selector else 256
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a nonempty string of at most {limit} characters")
    return value


def _vector(value: Any, label: str) -> list[float]:
    error = f"{label} must contain three finite numbers"
    if not isinstance(value, (list, tuple)) or len(value) != 3 or any(
        isinstance(x, bool) or not isinstance(x, (int, float)) for x in value
    ):
        raise ValueError(error)
    try:
        coordinates = [float(x) for x in value]
    except OverflowError as exc:
        raise ValueError(error) from exc
    if not all(math.isfinite(x) for x in coordinates):
        raise ValueError(error)
    return coordinates


def _basis(x_axis: Any, y_axis: Any) -> tuple[list[float], list[float]]:
    x, y = _vector(x_axis, "x_axis"), _vector(y_axis, "y_axis")
    lx, ly = math.sqrt(sum(v*v for v in x)), math.sqrt(sum(v*v for v in y))
    if not math.isclose(lx, 1, abs_tol=1e-8) or not math.isclose(ly, 1, abs_tol=1e-8):
        raise ValueError("x_axis and y_axis must be unit vectors")
    if abs(sum(a*b for a, b in zip(x, y, strict=True))) > 1e-8:
        raise ValueError("x_axis and y_axis must be perpendicular")
    return x, y


def _script(data: dict[str, Any], body: str) -> str:
    handles = {}
    for key in ("parent", "source", "component", "relative_to"):
        value = data.get(key)
        if is_handle(value):
            if parse_handle(value)["kind"] != "occurrence":
                raise ValueError(f"{key} must be an occurrence handle")
            handles.setdefault(value, f"_resolve_occurrence_{len(handles)}")
    functions = []
    for handle, name in handles.items():
        code = "def print(value): errors.append(json.loads(value))\n" + emit_resolve(handle, var="_entity")
        functions.append(f"def {name}(design, errors):\n" + textwrap.indent(code + "return _entity\n", "    "))
    mapping = "{" + ", ".join(f"{h!r}: {name}" for h, name in handles.items()) + "}"
    return ("import adsk.core, adsk.fusion, json, math\n" + RESOLVER_SCRIPT + MUTATION_SCRIPT
            + "\nDATA = " + repr(data) + "\n" + "def _scope(): return " + emit_scope_expr()
            + "\n" + "\n".join(functions) + "\n_HANDLES = " + mapping + "\n" + _HELPERS + "\n" + body)


_HELPERS = '''
_named_occurrence = _select_occurrence
def _select_occurrence(root, selector):
    if selector not in _HANDLES: return _named_occurrence(root, selector)
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    errors = []
    entity = _HANDLES[selector](design, errors)
    if entity is None:
        return None, errors[0] if errors else {'ok': False, 'error': 'handle_invalid'}
    return entity, None

def _out(data): print(json.dumps(data))
def _error(code, **details): return {'ok': False, 'error': code, **details}
def _preflight(design):
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        return _error('parametric_design_required')
    if design.timeline.markerPosition != design.timeline.count:
        return _error('timeline_rolled_back')
    if design.snapshots.hasPendingSnapshot:
        return _error('pending_assembly_transforms')
    return None

def _context_ok(root, occ):
    seen = []
    while occ is not None:
        if any(occ == previous for previous in seen): return _error('occurrence_context_invalid')
        seen.append(occ)
        if occ.isReferencedComponent: return _error('referenced_component_edit_unsupported', name=occ.fullPathName)
        occ = getattr(occ, 'assemblyContext', None)
    return None

def _editable_parent(root, selector):
    comp, occ, error = _select_component(root, selector, for_edit=True)
    if error: return None, None, error
    if occ:
        error = _context_ok(root, occ)
        if error: return None, None, error
    return comp, occ, None

def _unique_occurrence(root, selector):
    occ, error = _select_occurrence(root, selector)
    if error: return None, error
    error = _context_ok(root, occ)
    if error: return None, error
    parent_occ = getattr(occ, 'assemblyContext', None)
    if parent_occ:
        count = sum(1 for i in range(root.allOccurrences.count)
                    if root.allOccurrences.item(i).component == parent_occ.component)
        if count != 1: return None, _error('shared_component_edit_unsupported', name=parent_occ.fullPathName)
    return occ, None

def _proxy(native, parent_occ):
    if parent_occ is None: return native
    result = native.createForAssemblyContext(parent_occ)
    if result is None: raise RuntimeError('Fusion could not create the assembly-context occurrence')
    return result

def _handle(occ):
    context = getattr(occ, 'assemblyContext', None)
    return ('occurrence:' + occ.fullPathName + (('|occ=' + context.fullPathName) if context else '')
            + _scope() + ':' + occ.entityToken)

def _describe(root, occ):
    count = sum(1 for i in range(root.allOccurrences.count)
                if root.allOccurrences.item(i).component == occ.component)
    return {'path': occ.fullPathName, 'handle': _handle(occ), 'component_name': occ.component.name,
            'shared_definition': count > 1, 'transform': list(occ.transform2.asArray()),
            'transform_convention': 'Fusion Matrix3D.asArray; root assembly context; translation cm'}

def _has_component_name(design, name):
    return any(design.allComponents.item(i).name == name for i in range(design.allComponents.count))

def _contains_component(source, target):
    visited = []
    def walk(comp):
        if comp == target: return True
        if any(comp == prior for prior in visited): return False
        visited.append(comp)
        return any(walk(comp.occurrences.item(i).component) for i in range(comp.occurrences.count))
    return walk(source)

def _same(a, b):
    return len(a) == len(b) and all(abs(x-y) <= 1e-7 for x, y in zip(a, b))
'''


_CREATE = '''
def run(_ctx):
    app = adsk.core.Application.get(); design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None: _out(_error('no_active_design')); return
    root = design.rootComponent
    error = _preflight(design)
    if error: _out(error); return
    parent, parent_occ, error = _editable_parent(root, DATA['parent'])
    if error: _out(error); return
    if _has_component_name(design, DATA['name']):
        _out(_error('component_name_taken', name=DATA['name'])); return
    tx = None; attempted = False
    try:
        tx = _Mutation(design, None)
        attempted = True
        native = tx.track(parent.occurrences.addNewComponent(adsk.core.Matrix3D.create()))
        native.component.name = DATA['name']
        if native.component.name != DATA['name']: raise RuntimeError('Component name did not persist')
        verification = tx.verify()
        if design.snapshots.hasPendingSnapshot: raise RuntimeError('Unexpected pending snapshot after creation')
        occurrence = _proxy(native, parent_occ)
        _out({'ok': True, 'occurrence': _describe(root, occurrence), 'verification': verification})
    except Exception as exc:
        result = _mutation_failure(tx, exc, 'component_creation_failed')
        if design.snapshots.hasPendingSnapshot:
            result.setdefault('rollback', {})['status'] = 'failed'
            result['rollback']['pending_snapshot'] = True
        if attempted and (tx is None or not tx.created): result['mutation_state'] = 'unknown'
        _out(result)
'''

_INSERT = '''
def run(_ctx):
    app = adsk.core.Application.get(); design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None: _out(_error('no_active_design')); return
    root = design.rootComponent
    error = _preflight(design)
    if error: _out(error); return
    parent, parent_occ, error = _editable_parent(root, DATA['parent'])
    if error: _out(error); return
    source, error = _select_occurrence(root, DATA['source'])
    if error: _out(error); return
    error = _context_ok(root, source)
    if error: _out(error); return
    for i in range(parent.occurrences.count):
        existing = parent.occurrences.item(i)
        attribute = existing.attributes.itemByName('fusion_cad_mcp', 'insert_operation_id')
        if attribute and attribute.value == DATA['operation_id']:
            _out(_error('operation_id_exists', operation_id=DATA['operation_id'], existing_path=_proxy(existing, parent_occ).fullPathName)); return
    if _contains_component(source.component, parent):
        _out(_error('component_cycle_unsupported')); return
    if DATA['independent'] and _has_component_name(design, DATA['operation_id']):
        _out(_error('component_name_taken', name=DATA['operation_id'])); return
    tx = None; attempted = False
    try:
        tx = _Mutation(design, None)
        matrix = adsk.core.Matrix3D.create(); attempted = True
        if DATA['independent']:
            native = tx.track(parent.occurrences.addNewComponentCopy(source.component, matrix))
            native.component.name = DATA['operation_id']
            if native.component.name != DATA['operation_id']: raise RuntimeError('Copied component name did not persist')
            if native.component == source.component: raise RuntimeError('Independent copy shares source definition')
        else:
            native = tx.track(parent.occurrences.addExistingComponent(source.component, matrix))
            if native.component != source.component: raise RuntimeError('Inserted occurrence has unexpected component')
        attr = native.attributes.add('fusion_cad_mcp', 'insert_operation_id', DATA['operation_id'])
        if attr is None or attr.value != DATA['operation_id']:
            raise RuntimeError('Insert operation identity did not persist')
        verification = tx.verify()
        if design.snapshots.hasPendingSnapshot: raise RuntimeError('Unexpected pending snapshot after insertion')
        occurrence = _proxy(native, parent_occ)
        _out({'ok': True, 'operation_id': DATA['operation_id'], 'independent': DATA['independent'],
              'occurrence': _describe(root, occurrence), 'verification': verification})
    except Exception as exc:
        result = _mutation_failure(tx, exc, 'component_insertion_failed')
        if design.snapshots.hasPendingSnapshot:
            result.setdefault('rollback', {})['status'] = 'failed'
            result['rollback']['pending_snapshot'] = True
        if attempted and (tx is None or not tx.created): result['mutation_state'] = 'unknown'
        _out(result)
'''

_PLACE = '''
def run(_ctx):
    app = adsk.core.Application.get(); design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None: _out(_error('no_active_design')); return
    root = design.rootComponent
    error = _preflight(design)
    if error: _out(error); return
    occ, error = _unique_occurrence(root, DATA['component'])
    if error: _out(error); return
    if occ.isGrounded or getattr(occ, 'isGroundToParent', False):
        _out(_error('occurrence_grounded')); return
    ref = None
    if DATA['relative_to'] is not None:
        ref, error = _select_occurrence(root, DATA['relative_to'])
        if error: _out(error); return
        current = ref; paths = set()
        while current is not None:
            path = current.fullPathName
            if path == occ.fullPathName:
                _out(_error('self_relative_placement', name=path)); return
            if path in paths:
                _out(_error('occurrence_context_invalid')); return
            paths.add(path)
            current = getattr(current, 'assemblyContext', None)
    p = adsk.core.Point3D.create(*[v / 10 for v in DATA['origin_mm']])
    x = adsk.core.Vector3D.create(*DATA['x_axis']); y = adsk.core.Vector3D.create(*DATA['y_axis'])
    z = x.crossProduct(y)
    if ref is not None:
        transform = ref.transform2
        if not p.transformBy(transform) or not x.transformBy(transform) or not y.transformBy(transform) or not z.transformBy(transform):
            _out(_error('reference_transform_failed')); return
    desired = adsk.core.Matrix3D.create()
    if not desired.setWithCoordinateSystem(p, x, y, z): _out(_error('invalid_transform')); return
    expected = list(desired.asArray()); original = occ.transform2; before = list(original.asArray())
    if _same(expected, before):
        _out({'ok': True, 'changed': False, 'occurrence': _describe(root, occ), 'before_transform': before}); return
    snapshots = design.snapshots; snapshot = None; add_attempted = False; after = None
    try:
        occ.transform2 = desired
        after = list(occ.transform2.asArray())
        if not _same(after, expected): raise RuntimeError('Requested transform was not applied')
        if snapshots.hasPendingSnapshot:
            add_attempted = True
            snapshot = snapshots.add()
            if snapshot is None or snapshots.hasPendingSnapshot: raise RuntimeError('Snapshot did not commit')
        after = list(occ.transform2.asArray())
        if not _same(after, expected): raise RuntimeError('Transform changed after snapshot')
        _out({'ok': True, 'changed': True, 'occurrence': _describe(root, occ), 'before_transform': before,
              'expected_transform': expected, 'after_transform': after, 'snapshot_created': snapshot is not None})
    except Exception as exc:
        rollback = {'pose_restored': False, 'pending_snapshot_cleared': False, 'snapshot_deleted': None, 'status': 'failed'}
        try:
            if snapshot is not None:
                rollback['snapshot_deleted'] = bool(snapshot.deleteMe())
            if not snapshots.hasPendingSnapshot and not _same(occ.transform2.asArray(), before):
                occ.transform2 = original
            if snapshots.hasPendingSnapshot:
                snapshots.revertPendingSnapshot()
            rollback['pending_snapshot_cleared'] = not snapshots.hasPendingSnapshot
            rollback['pose_restored'] = _same(occ.transform2.asArray(), before)
            if add_attempted and snapshot is None: rollback['status'] = 'unknown'
            elif rollback['pose_restored'] and rollback['pending_snapshot_cleared'] and (snapshot is None or rollback['snapshot_deleted']):
                rollback['status'] = 'verified_pose_only'
        except Exception as rollback_error:
            rollback['detail'] = str(rollback_error)
        _out(_error('placement_failed', detail=str(exc), before_transform=before,
                    expected_transform=expected, after_transform=after, rollback=rollback))
'''


def build_create_component(name: str, parent: str | None = None) -> str:
    return _script({"name": _name(name, "name"), "parent": _name(parent, "parent", optional=True, selector=True)}, _CREATE)


def build_insert_component(source: str, operation_id: str, parent: str | None = None, independent: bool = False) -> str:
    if not isinstance(independent, bool):
        raise ValueError("independent must be a boolean")
    return _script({"source": _name(source, "source", selector=True), "operation_id": _name(operation_id, "operation_id"),
                    "parent": _name(parent, "parent", optional=True, selector=True), "independent": independent}, _INSERT)


def build_place_component(component: str, origin_mm: list[float], x_axis: list[float] | None = None,
                          y_axis: list[float] | None = None, relative_to: str | None = None) -> str:
    x, y = _basis([1, 0, 0] if x_axis is None else x_axis, [0, 1, 0] if y_axis is None else y_axis)
    return _script({"component": _name(component, "component", selector=True), "origin_mm": _vector(origin_mm, "origin_mm"),
                    "x_axis": x, "y_axis": y, "relative_to": _name(relative_to, "relative_to", optional=True, selector=True)}, _PLACE)


def _run(adapter: FusionAdapter, builder, *args, **kwargs) -> Envelope:
    try:
        script = builder(*args, **kwargs)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    env = adapter.execute_script(script)
    if not env.ok:
        return env
    result = parse_status_json(env)
    if result is None:
        return script_parse_failure(env, "placement_parse_failed")
    return Envelope(ok=result.get("ok") is True, error=result.get("error"), result=result, message=env.message)


def create_component(adapter: FusionAdapter, name: str, parent: str | None = None) -> Envelope:
    return _run(adapter, build_create_component, name, parent)


def insert_component(adapter: FusionAdapter, source: str, operation_id: str,
                     parent: str | None = None, independent: bool = False) -> Envelope:
    return _run(adapter, build_insert_component, source, operation_id, parent, independent)


def place_component(adapter: FusionAdapter, component: str, origin_mm: list[float],
                    x_axis: list[float] | None = None, y_axis: list[float] | None = None,
                    relative_to: str | None = None) -> Envelope:
    return _run(adapter, build_place_component, component, origin_mm, x_axis, y_axis, relative_to)
