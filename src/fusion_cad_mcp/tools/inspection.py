"""Read-only, bounded inspection of Fusion design entities.

Distances and sample points are in root assembly coordinates, in millimetres.
Topology predicates describe current geometry, not durable semantic identities.
"""

from __future__ import annotations

import json
import math
from typing import Any

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from ..handles import emit_scope_expr
from ..resolver import RESOLVER_SCRIPT


def _bounds(limit: int, offset: int = 0) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be an integer from 1 to 200")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _run(adapter: FusionAdapter, script: str, label: str) -> Envelope:
    env = execute_typed_script(adapter, script, read_only=True)
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return script_parse_failure(env, label + "_parse_failed", read_only=True)
    return Envelope(state=env.state,
        ok=parsed.get("ok") is not False,
        error=parsed.get("error"),
        result=parsed,
        # Successful inspection data is already complete in result. Retaining
        # its JSON stdout here doubles every handle, transform and entity row.
        # Keep failure stdout intact for diagnosis; raw execute is unaffected.
        message="" if parsed["ok"] else env.message,
    )


def _script(body: str, params: dict[str, Any]) -> str:
    return (
        "import adsk.core, adsk.fusion, json, math\n"
        + RESOLVER_SCRIPT
        + "\n"
        + "P = json.loads("
        + repr(json.dumps(params, allow_nan=False))
        + ")\n"
        + "def _scope(): return "
        + emit_scope_expr()
        + "\n"
        + _HELPERS
        + "\n"
        + body
    )


_HELPERS = """
def _items(collection):
    return [collection.item(i) for i in range(collection.count)]
def _vec(p, scale=1):
    return [p.x * scale, p.y * scale, p.z * scale]
def _box(b):
    box = b.boundingBox
    return {'min': _vec(box.minPoint, 10), 'max': _vec(box.maxPoint, 10)}
def _handle(kind, path, entity, occ=None):
    return kind + ':' + path + (('|occ=' + occ.fullPathName) if occ else '') + _scope() + ':' + entity.entityToken
def _state(entity):
    try: state = int(entity.healthState)
    except Exception: state = None
    try: message = entity.errorOrWarningMessage or ''
    except Exception: message = ''
    return {'health_state': state, 'message': message[:500]}
def _type(entity):
    try: return entity.objectType.split('::')[-1]
    except Exception: return None
def _root():
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    return design.rootComponent if design else None
def _out(value):
    print(json.dumps(value))
"""


def build_query_geometry(
    body: str,
    kind: str,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
    expected_count: int | None = None,
) -> str:
    _bounds(limit, offset)
    if not isinstance(body, str) or not body:
        raise ValueError("body selector required")
    if kind not in ("face", "edge", "vertex"):
        raise ValueError("kind must be face, edge, or vertex")
    filters = {} if filters is None else filters
    if not isinstance(filters, dict) or set(filters) - {
        "type",
        "normal",
        "parallel",
        "radius",
        "length",
        "near",
        "tolerance",
        "feature",
    }:
        raise ValueError("invalid geometry filters")
    filters = filters.copy()
    if expected_count is not None and (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 0
    ):
        raise ValueError("expected_count must be a nonnegative integer")
    for k in ("normal", "parallel", "near"):
        if k in filters:
            v = filters[k]
            if not isinstance(v, list) or len(v) != 3 or any(not _finite(x) for x in v):
                raise ValueError(k + " must be a finite 3-vector")
            if k != "near":
                scale = max(abs(x) for x in v)
                if not scale:
                    raise ValueError(k + " must be nonzero")
                scaled = [x / scale for x in v]
                length = math.hypot(*scaled)
                filters[k] = [x / length for x in scaled]
    for k in ("radius", "length", "tolerance"):
        if k in filters and (not _finite(filters[k]) or filters[k] < 0):
            raise ValueError(k + " must be a finite nonnegative number")
    for k in ("type", "feature"):
        if k in filters and (not isinstance(filters[k], str) or not filters[k]):
            raise ValueError(k + " must be a nonempty string")
    if (
        ("normal" in filters and kind != "face")
        or ("parallel" in filters and kind != "edge")
        or ("length" in filters and kind != "edge")
        or ("feature" in filters and kind != "face")
    ):
        raise ValueError("filter is not supported for this entity kind")
    return _script(
        _QUERY,
        {
            "body": body,
            "kind": kind,
            "filters": filters,
            "limit": limit,
            "offset": offset,
            "expected_count": expected_count,
        },
    )


_QUERY = """
def run(_ctx):
    root = _root()
    if root is None: _out({'ok': False, 'error': 'no_active_design'}); return
    body, error = _select_body(root, P['body'])
    if error: _out(error); return
    kind = P['kind']; f = P['filters']; tol = f.get('tolerance', 0.01)
    occ = getattr(body, 'assemblyContext', None)
    feature_faces = None
    if 'feature' in f:
        feature, owner, error = _select_feature(root, f['feature'], for_edit=False)
        if error: _out(error); return
        native_body = getattr(body, 'nativeObject', None) or body
        if getattr(native_body, 'parentComponent', None) != owner:
            _out({'ok': False, 'error': 'feature_body_context_mismatch'}); return
        if ' / ' in f['feature']:
            feature_occ = f['feature'].rsplit(' / ', 1)[0]
            if not occ or feature_occ != occ.fullPathName:
                _out({'ok': False, 'error': 'feature_body_context_mismatch'}); return
        elif occ and sum(1 for i in range(root.allOccurrences.count) if root.allOccurrences.item(i).component == owner) != 1:
            _out({'ok': False, 'error': 'feature_occurrence_ambiguous'}); return
        try:
            feature_faces = [getattr(x, 'nativeObject', None) or x for x in _items(feature.faces)]
        except Exception as exc:
            _out({'ok': False, 'error': 'feature_faces_unavailable', 'detail': str(exc)}); return
    entities = getattr(body, {'face':'faces', 'edge':'edges', 'vertex':'vertices'}[kind])
    count = 0; matches = []
    for index in range(entities.count):
        try:
            ent = entities.item(index)
            geom = ent.geometry if kind != 'vertex' else None
            typ = _type(geom) if geom else 'Point3D'
            entry = {'index': index, 'type': typ}
            if kind == 'face':
                pt = ent.pointOnFace
                ok, n = ent.evaluator.getNormalAtPoint(pt)
                entry['sample_point_mm'] = _vec(pt, 10)
                entry['normal'] = _vec(n) if ok else None
            elif kind == 'edge':
                start, end = ent.startVertex, ent.endVertex
                entry['start_mm'] = _vec(start.geometry, 10) if start else None
                entry['end_mm'] = _vec(end.geometry, 10) if end else None
                entry['length_mm'] = ent.length * 10
                entry['sample_point_mm'] = _vec(ent.pointOnEdge, 10)
            else:
                entry['sample_point_mm'] = _vec(ent.geometry, 10)
            if geom is not None and hasattr(geom, 'radius'): entry['radius_mm'] = geom.radius * 10
            if 'type' in f and (typ or '').lower() != f['type'].lower(): continue
            if feature_faces is not None:
                native = getattr(ent, 'nativeObject', None) or ent
                if not any(native == candidate for candidate in feature_faces): continue
            if 'radius' in f and ('radius_mm' not in entry or abs(entry['radius_mm'] - f['radius']) > tol): continue
            if 'length' in f and abs(entry['length_mm'] - f['length']) > tol: continue
            if 'near' in f and math.dist(entry['sample_point_mm'], f['near']) > tol: continue
            if 'normal' in f:
                n = entry['normal']; v = f['normal']
                if n is None or math.dist(n, v) > tol: continue
            if 'parallel' in f:
                if typ != 'Line3D' or entry['start_mm'] is None or entry['end_mm'] is None: continue
                d = [b-a for a,b in zip(entry['start_mm'], entry['end_mm'])]; v = f['parallel']
                dl = math.hypot(*d)
                if not dl or 1 - abs(sum((a/dl)*b for a,b in zip(d,v))) > tol: continue
            if P['offset'] <= count < P['offset'] + P['limit']:
                try: entry['handle'] = _handle(kind, P['body'] + '/' + kind + '[' + str(index) + ']', ent, occ)
                except Exception as exc: entry['handle_unavailable'] = str(exc)
                matches.append(entry)
            count += 1
        except Exception as exc:
            _out({'ok': False, 'error': 'geometry_inspection_failed', 'index': index, 'detail': str(exc)}); return
    expected = P['expected_count']
    _out({'ok': expected is None or count == expected, 'error': 'count_mismatch' if expected is not None and count != expected else None,
          'body': P['body'], 'kind': kind, 'frame': 'root', 'units': 'mm', 'near_semantics': 'sample point distance',
          'feature_provenance': 'Only surviving faces returned by Feature.faces are matched; a zero count does not prove no faces were originally created.' if feature_faces is not None else None,
          'tolerance': tol, 'tolerance_semantics': 'mm for radius, length and sample distance; dimensionless for direction', 'count': count, 'offset': P['offset'], 'limit': P['limit'], 'items': matches})
"""


def query_geometry(
    adapter: FusionAdapter,
    body: str,
    kind: str,
    filters: dict[str, Any] | None = None,
    limit: int = 50,
    offset: int = 0,
    expected_count: int | None = None,
) -> Envelope:
    try:
        script = build_query_geometry(body, kind, filters, limit, offset, expected_count)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    return _run(adapter, script, "query_geometry")


def build_inspect_feature(feature: str, limit: int = 50) -> str:
    _bounds(limit)
    if not isinstance(feature, str) or not feature:
        raise ValueError("feature selector required")
    return _script(_FEATURE, {"feature": feature, "limit": limit})


_FEATURE = """
def run(_ctx):
    root = _root()
    if root is None: _out({'ok': False, 'error': 'no_active_design'}); return
    feature, owner, error = _select_feature(root, P['feature'], for_edit=False)
    if error: _out(error); return
    occ = None
    if owner != root:
        occurrences = [x for x in _items(root.allOccurrences) if x.component == owner]
        if ' / ' in P['feature']:
            occurrences = [x for x in occurrences if x.fullPathName == P['feature'].rsplit(' / ', 1)[0]]
        if len(occurrences) != 1:
            _out({'ok': False, 'error': 'feature_occurrence_ambiguous'}); return
        occ = occurrences[0]
    result = {'ok': True, 'name': feature.name, 'type': _type(feature), 'health': _state(feature),
              'is_parametric': getattr(feature, 'isParametric', None), 'is_suppressed': getattr(feature, 'isSuppressed', None),
              'dependencies': {'available': False, 'reason': 'generic dependency graph unavailable'},
              'face_provenance': 'currently surviving faces; may be incomplete after later edits'}
    try:
        contextual = feature.createForAssemblyContext(occ) if occ else feature
        result['handle'] = _handle('feature', P['feature'], contextual, occ)
    except Exception:
        result['handle_unavailable'] = True
    for prop in ('faces', 'bodies', 'linkedFeatures'):
        try:
            values = _items(getattr(feature, prop)); entries = []
            for obj in values[:P['limit']]:
                item = {'type': _type(obj), 'name': getattr(obj, 'name', None)}
                try:
                    # Linked features can belong to another component; do not
                    # attach the selected feature's occurrence to those entities.
                    if prop == 'linkedFeatures' and obj.parentComponent != owner:
                        item['handle_unavailable'] = True
                    else:
                        contextual = obj.createForAssemblyContext(occ) if occ else obj
                        kind = {'faces': 'face', 'bodies': 'body', 'linkedFeatures': 'feature'}[prop]
                        item['handle'] = _handle(kind, P['feature'] + '/' + prop + '[' + str(len(entries)) + ']', contextual, occ)
                except Exception:
                    item['handle_unavailable'] = True
                entries.append(item)
            result[prop] = {'count': len(values), 'items': entries}
        except Exception as exc: result[prop] = {'available': False, 'reason': str(exc)}
    try:
        params = [x for x in _items(owner.modelParameters) if x.createdBy == feature]
        result['parameters'] = {'count': len(params), 'items': [
            {'name': x.name, 'expression': x.expression, 'unit': x.unit} for x in params[:P['limit']]]}
    except Exception as exc: result['parameters'] = {'available': False, 'reason': str(exc)}
    _out(result)
"""


def inspect_feature(adapter: FusionAdapter, feature: str, limit: int = 50) -> Envelope:
    try:
        script = build_inspect_feature(feature, limit)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    return _run(adapter, script, "inspect_feature")


def build_inspect_assembly(
    component: str | None = None, include: list[str] | None = None, limit: int = 50, offset: int = 0,
    detail: str = "full", fields: list[str] | None = None, health_filter: str | None = None,
) -> str:
    _bounds(limit, offset)
    if component is not None and (not isinstance(component, str) or not component):
        raise ValueError("component must be a nonempty selector")
    if include is None:
        include = ["occurrences", "bodies", "sketches", "features", "joints"]
    if (
        not isinstance(include, list)
        or not include
        or any(
            x not in ("occurrences", "bodies", "sketches", "features", "joints", "as_built_joints")
            for x in include
        )
    ):
        raise ValueError("include must contain supported sections")
    if detail not in ("full", "summary"):
        raise ValueError("detail must be full or summary")
    allowed_fields = ("handles", "transforms", "health", "bounds")
    if fields is not None and (not isinstance(fields, list) or any(x not in allowed_fields for x in fields)):
        raise ValueError("fields must contain supported detail groups")
    if health_filter not in (None, "non_healthy"):
        raise ValueError("health_filter must be non_healthy or None")
    return _script(
        _ASSEMBLY, {"component": component, "include": list(dict.fromkeys(include)), "limit": limit,
                    "offset": offset, "detail": detail, "fields": list(allowed_fields) if fields is None else fields,
                    "health_filter": health_filter}
    )


_ASSEMBLY = """
def _occ_parent(occurrence):
    return occurrence.assemblyContext

def _under(occurrence, selected):
    if selected is None: return True
    seen = []
    parent = _occ_parent(occurrence)
    while parent is not None:
        if parent == selected: return True
        if any(parent == item for item in seen): raise ValueError('cyclic occurrence context')
        seen.append(parent)
        parent = _occ_parent(parent)
    return False

def _property(obj, name):
    try:
        value = getattr(obj, name)
        return {'available': True, 'value': value}
    except Exception as exc:
        return {'available': False, 'reason': str(exc)}

def _matrix(obj):
    try: return {'available': True, 'matrix_cm': list(obj.transform2.asArray())}
    except Exception as exc: return {'available': False, 'reason': str(exc)}

def _occ_entry(occurrence, all_occurrences):
    parent = _occ_parent(occurrence)
    count = sum(1 for x in all_occurrences if x.component == occurrence.component)
    item = {'path': occurrence.fullPathName, 'parent_path': parent.fullPathName if parent else None,
            'component_name': occurrence.component.name, 'definition_occurrence_count': count,
            'shared_definition': count > 1,
            'is_grounded': _property(occurrence, 'isGrounded'),
            'is_ground_to_parent': _property(occurrence, 'isGroundToParent'),
            'is_referenced_component': _property(occurrence, 'isReferencedComponent')}
    if 'transforms' in P['fields']:
        item['root_transform'] = _matrix(occurrence)
        try:
            native = occurrence.nativeObject or occurrence
            item['local_transform'] = _matrix(native)
        except Exception as exc: item['local_transform'] = {'available': False, 'reason': str(exc)}
    if 'handles' in P['fields']:
        try: item['handle'] = _handle('occurrence', occurrence.fullPathName, occurrence, parent)
        except Exception as exc: item['handle_unavailable'] = str(exc)
    return item

def run(_ctx):
    root = _root()
    if root is None: _out({'ok': False, 'error': 'no_active_design'}); return
    if P['component'] is None:
        comp, occ = root, None
    else:
        comp, occ, error = _select_component(root, P['component'], for_edit=False)
        if error: _out(error); return
    result = {'ok': True, 'component': comp.name, 'occurrence': occ.fullPathName if occ else None,
              'frame': 'root' if occ or comp == root else 'component', 'units': 'mm', 'sections': {}}
    if occ and 'transforms' in P['fields']:
        try: result['root_transform'] = {'matrix_cm': list(occ.transform2.asArray()), 'convention': 'Fusion Matrix3D.asArray; translation uses cm'}
        except Exception as exc: result['root_transform'] = {'available': False, 'reason': str(exc)}
    elif 'transforms' in P['fields']: result['root_transform'] = None
    for section in P['include']:
        try:
            all_occurrences = _items(root.allOccurrences) if section == 'occurrences' else []
            if section == 'occurrences':
                values = [item for item in all_occurrences if _under(item, occ)]
            else:
                attr = 'asBuiltJoints' if section == 'as_built_joints' else section
                collection = (occ.bRepBodies if occ else comp.bRepBodies) if section == 'bodies' else getattr(comp, attr)
                values = _items(collection)
            if P['health_filter'] == 'non_healthy' and section in ('sketches', 'features'):
                values = [obj for obj in values if _state(obj)['health_state'] != 0]
            out = []
            page = values[P['offset']:P['offset']+P['limit']] if P['detail'] == 'full' else []
            for obj in page:
                if section == 'occurrences':
                    out.append(_occ_entry(obj, all_occurrences)); continue
                entry = {'name': obj.name, 'type': _type(obj)}
                if section == 'bodies':
                    if 'bounds' in P['fields']:
                        try: entry['bounding_box_mm'] = _box(obj)
                        except Exception as exc: entry['bounds_unavailable'] = str(exc)
                    if 'handles' in P['fields']:
                        try: entry['handle'] = _handle('body', obj.name, obj, occ)
                        except Exception as exc: entry['handle_unavailable'] = str(exc)
                elif section in ('sketches', 'features'):
                    if 'health' in P['fields']: entry['health'] = _state(obj)
                    entry['selector'] = (occ.fullPathName + ' / ' if occ else '') + obj.name
                    if 'handles' in P['fields']:
                        try:
                            contextual = obj.createForAssemblyContext(occ) if occ else obj
                            if contextual is None: raise ValueError('proxy unavailable')
                            entry['handle'] = _handle('sketch' if section == 'sketches' else 'feature', entry['selector'], contextual, occ)
                        except Exception as exc: entry['handle_unavailable'] = str(exc)
                out.append(entry)
            end = P['offset'] + len(out)
            section_out = {'count': len(values), 'offset': P['offset'], 'limit': P['limit'], 'items': out,
                           'returned_count': len(out), 'next_offset': end if P['detail'] == 'full' and end < len(values) else None,
                           'truncated': end < len(values)}
            if section == 'occurrences':
                section_out['scope'] = 'all descendants; selected occurrence excluded' if occ else 'all occurrences in root assembly'
                section_out['transform_convention'] = 'Fusion Matrix3D.asArray; translation cm; root and immediate-parent frames'
            result['sections'][section] = section_out
        except Exception as exc: result['sections'][section] = {'available': False, 'reason': str(exc)}
    _out(result)
"""


def inspect_assembly(
    adapter: FusionAdapter,
    component: str | None = None,
    include: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    detail: str = "full",
    fields: list[str] | None = None,
    health_filter: str | None = None,
) -> Envelope:
    try:
        script = build_inspect_assembly(component, include, limit, offset, detail, fields, health_filter)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    return _run(adapter, script, "inspect_assembly")
