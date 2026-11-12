"""Read-only, bounded geometric design checks in root assembly coordinates.

Interference uses Fusion transient results and never creates model bodies. Bounding
checks compare axis-aligned boxes; they do not establish containment in a cavity.
"""

from __future__ import annotations

import math
import textwrap
from typing import Any

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from ..handles import emit_resolve, is_handle, parse_handle
from ..measurement import DISTANCE_EVIDENCE_SCRIPT
from ..resolver import RESOLVER_SCRIPT


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _number(value: Any, name: str) -> float:
    if not _finite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def _vector(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or any(
        not _finite(x) for x in value
    ):
        raise ValueError(f"{name} must contain three finite coordinates")
    return [float(x) for x in value]


def _prepare(checks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not isinstance(checks, list) or not 1 <= len(checks) <= 50:
        raise ValueError("checks must contain between 1 and 50 checks")
    prepared = []
    handles: dict[str, str] = {}
    probe_names: set[str] = set()
    face_budget = 0
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ValueError(f"checks[{index}] must be an object")
        kind = check.get("type")
        if kind == "clearance":
            allowed, required = {"type", "body_a", "body_b", "min_mm"}, {"body_a", "body_b", "min_mm"}
            numeric = {"min_mm": _number(check.get("min_mm"), "min_mm")}
        elif kind == "interference":
            allowed, required = {"type", "body_a", "body_b", "max_overlap_mm3"}, {"body_a", "body_b"}
            numeric = {"max_overlap_mm3": _number(check.get("max_overlap_mm3", 0), "max_overlap_mm3")}
        elif kind == "bounds":
            allowed, required = {"type", "body", "min_mm", "max_mm", "tolerance_mm"}, {"body", "min_mm", "max_mm"}
            low, high = _vector(check.get("min_mm"), "min_mm"), _vector(check.get("max_mm"), "max_mm")
            if any(a > b for a, b in zip(low, high, strict=True)):
                raise ValueError("min_mm coordinates must not exceed max_mm")
            numeric = {"min_mm": low, "max_mm": high, "tolerance_mm": _number(check.get("tolerance_mm", 0), "tolerance_mm")}
        elif kind == "surface_distance":
            required = {"name", "body", "point_mm", "frame", "max_mm", "tolerance_mm", "max_faces"}
            allowed = required | {"type"}
            name = check.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100 or name in probe_names:
                raise ValueError("surface_distance name must be unique, nonempty and at most 100 characters")
            probe_names.add(name)
            if check.get("frame") != "root":
                raise ValueError("surface_distance frame must be root")
            cap = check.get("max_faces")
            if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 512:
                raise ValueError("surface_distance max_faces must be an integer in 1..512")
            face_budget += cap
            if face_budget > 4096:
                raise ValueError("surface_distance total declared max_faces must not exceed 4096")
            numeric = {"name": name, "point_mm": _vector(check.get("point_mm"), "point_mm"),
                       "frame": "root", "max_faces": cap,
                       "max_mm": _number(check.get("max_mm"), "max_mm"),
                       "tolerance_mm": _number(check.get("tolerance_mm"), "tolerance_mm")}
        elif kind == "face_distance":
            required = {"face_a", "face_b", "tolerance_mm"}
            allowed = required | {"type", "min_mm", "max_mm"}
            bounds = {field: _number(check[field], field) for field in ("min_mm", "max_mm") if field in check}
            if not bounds:
                raise ValueError("face_distance requires min_mm or max_mm")
            if bounds.get("min_mm", 0) > bounds.get("max_mm", math.inf):
                raise ValueError("min_mm must not exceed max_mm")
            numeric = {**bounds, "tolerance_mm": _number(check.get("tolerance_mm"), "tolerance_mm")}
        else:
            raise ValueError(f"checks[{index}].type is unsupported")
        if set(check) - allowed or not required.issubset(check):
            raise ValueError(f"checks[{index}] has missing or unsupported fields")
        selectors = {
            "bounds": ("body",), "surface_distance": ("body",), "face_distance": ("face_a", "face_b"),
        }.get(kind, ("body_a", "body_b"))
        item = {"type": kind, **numeric}
        for field in selectors:
            value = check[field]
            if not isinstance(value, str) or not value:
                raise ValueError(f"checks[{index}].{field} must be a nonempty selector")
            if kind in {"face_distance", "surface_distance"}:
                handle = parse_handle(value)
                expected_kind = "face" if kind == "face_distance" else "body"
                if handle["kind"] != expected_kind or not any(
                    part.startswith("doc=") for part in handle["path"].split("|")[1:]
                ):
                    raise ValueError(f"checks[{index}].{field} must be a document-scoped {expected_kind} handle")
                handles.setdefault(value, f"_entity_handle_{len(handles)}")
            elif is_handle(value):
                if parse_handle(value)["kind"] != "body":
                    raise ValueError(f"checks[{index}].{field} must be a body handle")
                handles.setdefault(value, f"_entity_handle_{len(handles)}")
            item[field] = value
        prepared.append(item)
    return prepared, handles


def build_validate_design(checks: list[dict[str, Any]]) -> str:
    prepared, handles = _prepare(checks)
    functions = []
    for handle, name in handles.items():
        code = "def print(value): errors.append(json.loads(value))\n" + emit_resolve(handle, var="_entity")
        functions.append(f"def {name}(design, errors):\n" + textwrap.indent(code + "return _entity\n", "    "))
    mapping = "{" + ", ".join(f"{h!r}: {name}" for h, name in handles.items()) + "}"
    return (
        "import adsk.core, adsk.fusion, json, math\n"
        + RESOLVER_SCRIPT + "\n" + "\n".join(functions)
        + "\n_HANDLES = " + mapping + "\n_CHECKS = " + repr(prepared) + "\n"
        + DISTANCE_EVIDENCE_SCRIPT + _SCRIPT
    )


_SCRIPT = '''
_FACE_DIAGNOSTIC_BYTES = 16384

def _json_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))

def _face_failure(evidence, failure):
    global _FACE_DIAGNOSTIC_BYTES
    evidence['face_failure_count'] += 1
    issues = failure.get('evidence', {}).get('witness_consistency', {}).get('issues', [])
    membership = failure.get('evidence', {}).get('surface_membership', {})
    if failure.get('detail_truncated') or membership.get('detail_truncated') or any(issue.get('detail_truncated') for issue in issues):
        evidence['diagnostics_truncated'] = True
    size = _json_size(failure)
    if len(evidence['face_failures']) < 3 and size <= min(2048 - evidence['_failure_bytes'], _FACE_DIAGNOSTIC_BYTES):
        evidence['face_failures'].append(failure)
        evidence['_failure_bytes'] += size
        _FACE_DIAGNOSTIC_BYTES -= size
    else:
        evidence['face_failures_omitted'] += 1
        evidence['diagnostics_truncated'] = True

def _bound_validation_output(results):
    # Leave room for receipt identity/metadata inside the 256 KiB record limit.
    # Never drop a check status or its minimum native scalar candidate.
    if _json_size(results) <= 196608: return
    for index, row in enumerate(results):
        if 'check' in row:
            row.pop('check')
            row.update(check_index=index, input_echo_omitted=True)
        reason = row.get('reason')
        if reason is not None and _json_size(reason) > 1024:
            code = reason.get('error', 'diagnostic_omitted_for_size') if isinstance(reason, dict) else reason
            text, _ = _measurement_detail(code)
            row['reason'] = {'error': text, 'diagnostic_truncated': True}
        if _json_size(results) <= 196608: return

def _point(p):
    if p is None: raise ValueError('closest point unavailable')
    result = [p.x * 10, p.y * 10, p.z * 10]
    if not all(math.isfinite(x) for x in result): raise ValueError('non-finite point')
    return result

def _resolve_check_handle(design, selector):
    errors = []
    entity = _HANDLES[selector](design, errors)
    if entity is None:
        return None, errors[0] if errors else {'error': 'handle_invalid'}
    return entity, None

def _body(design, root, selector):
    if selector not in _HANDLES:
        return _select_body(root, selector)
    return _resolve_check_handle(design, selector)

def _face_distance(measure, a, b, spec):
    result = {'type': 'face_distance', 'check': spec}
    context_a = getattr(a, 'assemblyContext', None)
    context_b = getattr(b, 'assemblyContext', None)
    if getattr(context_a, 'fullPathName', None) == getattr(context_b, 'fullPathName', None) and a == b:
        return dict(result, status='unavailable', reason='same_face')
    try:
        evidence = _distance_evidence(measure.measureMinimumDistance(a, b))
        evidence['frame'] = 'root'
        _face_pair_witnesses(evidence, a, b)
    except Exception as exc:
        detail, truncated = _measurement_detail(exc)
        return dict(result, status='unavailable', reason={
            'error': 'face_distance_unavailable', 'detail': detail, 'detail_truncated': truncated,
        })
    if (not evidence['scalar_valid'] or evidence['witness_consistency']['status'] != 'consistent'
            or any(item['status'] != 'on_face' for item in evidence['face_membership'].values())):
        return dict(result, status='unavailable', evidence=evidence,
                    reason={'error': 'face_distance_unavailable', 'detail': 'native witness consistency or trimmed-face membership unavailable'})
    distance_mm = evidence['minimum_distance_mm']
    tolerance = spec['tolerance_mm']
    passes = spec.get('min_mm', 0) - tolerance <= distance_mm <= spec.get('max_mm', math.inf) + tolerance
    return dict(result, status='pass' if passes else 'violation', evidence=evidence)

def _face_pair_check(design, root, measure, spec):
    faces = []
    for field in ('face_a', 'face_b'):
        handle = spec[field]
        face, error = _resolve_check_handle(design, handle)
        if not error and not getattr(face, 'isValid', True):
            error = {'error': 'face_invalid'}
        if not error:
            occurrence_path = next((part[4:] for part in handle.rsplit(':', 1)[0].split('|')[1:]
                                    if part.startswith('occ=')), None)
            context = getattr(face, 'assemblyContext', None)
            if getattr(context, 'fullPathName', None) != occurrence_path or (
                context is None and face.body.parentComponent != root
            ):
                error = {'error': 'face_root_context_unavailable'}
        if error:
            return {'type': 'face_distance', 'status': 'unavailable', 'reason': error,
                    'check': spec, 'target': field}
        faces.append(face)
    return _face_distance(measure, faces[0], faces[1], spec)

def _surface_distance(design, root, measure, spec):
    result = {'type': 'surface_distance', 'check': spec}
    body, error = _resolve_check_handle(design, spec['body'])
    if error:
        return dict(result, status='unavailable', reason=error)
    if not getattr(body, 'isValid', True):
        return dict(result, status='unavailable', reason='body_invalid')
    occurrence_path = next((part[4:] for part in spec['body'].rsplit(':', 1)[0].split('|')[1:]
                            if part.startswith('occ=')), None)
    context = getattr(body, 'assemblyContext', None)
    if getattr(context, 'fullPathName', None) != occurrence_path or (
        context is None and body.parentComponent != root
    ):
        return dict(result, status='unavailable', reason='body_root_context_unavailable')
    count = body.faces.count
    evidence = {'name': spec['name'], 'sample_point_mm': spec['point_mm'], 'frame': 'root',
                'units': 'mm', 'face_count': count, 'faces_attempted': 0, 'faces_measured': 0, 'faces_valid': 0,
                'scope': 'one caller-supplied point to selected body surface; sampled and directional; not Hausdorff or full shape equivalence',
                'method': 'minimum native Point3D-to-BRepFace scalar across every selected body face',
                'minimum_scalar_candidate': None, 'satisfying_witness': None, 'proof': None, 'proof_scope': None,
                'face_failures': [], 'face_failure_count': 0,
                'face_failures_omitted': 0, 'diagnostics_truncated': False, '_failure_bytes': 0}
    if count < 1 or count > spec['max_faces']:
        evidence.pop('_failure_bytes')
        return dict(result, status='unavailable', evidence=evidence,
                    reason='empty_surface' if count < 1 else 'face_budget_exceeded')
    point = adsk.core.Point3D.create(*[x / 10 for x in spec['point_mm']])
    for index in range(count):
        try:
            face = body.faces.item(index)
            if face is None or not getattr(face, 'isValid', True):
                raise ValueError('invalid body face')
            # Some API versions expose native faces from proxy bodies. Recreate the
            # proxy explicitly rather than measuring component-local geometry.
            face_context = getattr(face, 'assemblyContext', None)
            if context is not None and face_context is None:
                face = face.createForAssemblyContext(context)
                face_context = getattr(face, 'assemblyContext', None)
            if face is None or not getattr(face, 'isValid', True) or getattr(face_context, 'fullPathName', None) != occurrence_path:
                raise ValueError('face root context unavailable')
            evidence['faces_attempted'] += 1
            native_result = measure.measureMinimumDistance(point, face)
            evidence['faces_measured'] += 1
            sample = _distance_evidence(native_result, spec['point_mm'])
            sample['face_index'] = index
            sample['input_point_separation_mm'] = None
            sample['witness_upper_bound_mm'] = None
            sample['surface_membership'] = _face_membership(face, sample['closest_point_b_mm'])
            value = sample['minimum_distance_mm']
            if value is not None and (evidence['minimum_scalar_candidate'] is None or value < evidence['minimum_scalar_candidate']['minimum_distance_mm']):
                evidence['minimum_scalar_candidate'] = sample
            if (not sample['scalar_valid'] or sample['witness_consistency']['status'] != 'consistent'
                    or sample['surface_membership']['status'] != 'on_face'):
                _face_failure(evidence, {'face_index': index, 'evidence': sample})
            else:
                evidence['faces_valid'] += 1
                separation = math.dist(spec['point_mm'], sample['closest_point_b_mm'])
                sample['input_point_separation_mm'] = separation if math.isfinite(separation) else None
                membership_tolerance = sample['surface_membership']['tolerance_mm']
                upper_bound = separation + membership_tolerance if membership_tolerance is not None else None
                sample['witness_upper_bound_mm'] = upper_bound if upper_bound is not None and math.isfinite(upper_bound) else None
                if sample['witness_upper_bound_mm'] is not None and upper_bound <= spec['max_mm'] + spec['tolerance_mm']:
                    previous = evidence['satisfying_witness']
                    if previous is None or upper_bound < previous['witness_upper_bound_mm']:
                        evidence['satisfying_witness'] = sample
        except Exception as exc:
            detail, truncated = _measurement_detail(exc)
            _face_failure(evidence, {'face_index': index, 'detail': detail, 'detail_truncated': truncated})
    evidence.pop('_failure_bytes')
    if evidence['satisfying_witness'] is not None:
        evidence['proof'] = 'validated_witness_upper_bound'
        evidence['proof_scope'] = 'native face membership tolerance plus query-to-witness separation; not a global minimum proof'
        return dict(result, status='pass', evidence=evidence)
    if evidence['face_failure_count'] or evidence['minimum_scalar_candidate'] is None:
        return dict(result, status='unavailable', evidence=evidence, reason='surface_distance_unavailable')
    value = evidence['minimum_scalar_candidate']['minimum_distance_mm']
    if value > spec['max_mm'] + spec['tolerance_mm']:
        evidence['proof'] = 'native_face_minima_above_maximum'
        evidence['proof_scope'] = 'all selected face native minima usable; conditional on native minimum correctness'
        return dict(result, status='violation', evidence=evidence)
    return dict(result, status='unavailable', evidence=evidence, reason='surface_maximum_boundary_uncertain')

def _interference(design, a, b):
    if not getattr(a, 'isSolid', False) or not getattr(b, 'isSolid', False):
        raise ValueError('interference requires two solid BRep bodies')
    entities = adsk.core.ObjectCollection.create()
    entities.add(a); entities.add(b)
    inp = design.createInterferenceInput(entities)
    if inp is None: raise ValueError('Fusion could not create interference input')
    inp.areCoincidentFacesIncluded = False
    results = design.analyzeInterference(inp)
    if results is None: raise ValueError('Fusion returned no interference result')
    volumes = []
    for i in range(results.count):
        body = results.item(i).interferenceBody
        if body is None: volumes.append(None); continue
        volume = body.volume * 1000
        if not math.isfinite(volume) or volume < 0: raise ValueError('invalid intersection volume')
        volumes.append(volume)
    return results.count, volumes

def _check(design, root, measure, spec):
    kind = spec['type']
    if kind == 'face_distance':
        return _face_pair_check(design, root, measure, spec)
    if kind == 'surface_distance':
        return _surface_distance(design, root, measure, spec)
    names = ['body'] if kind == 'bounds' else ['body_a', 'body_b']
    bodies = []
    for field in names:
        body, error = _body(design, root, spec[field])
        if error: return {'type': kind, 'status': 'unavailable', 'reason': error, 'check': spec}
        if not getattr(body, 'isValid', True):
            return {'type': kind, 'status': 'unavailable', 'reason': 'body_invalid', 'check': spec}
        bodies.append(body)
    if len(bodies) == 2 and bodies[0] == bodies[1]:
        return {'type': kind, 'status': 'unavailable', 'reason': 'same_body', 'check': spec}
    if kind == 'bounds':
        box = bodies[0].boundingBox
        low, high = _point(box.minPoint), _point(box.maxPoint)
        tolerance = spec['tolerance_mm']
        inside = all(x >= y - tolerance for x, y in zip(low, spec['min_mm'])) and all(x <= y + tolerance for x, y in zip(high, spec['max_mm']))
        return {'type': kind, 'status': 'pass' if inside else 'violation', 'check': spec,
                'evidence': {'body_bounds_min_mm': low, 'body_bounds_max_mm': high, 'frame': 'root',
                             'method': 'axis-aligned bounding box; not cavity containment'}}
    count, volumes = _interference(design, bodies[0], bodies[1])
    if kind == 'interference':
        if any(v is None for v in volumes) and spec['max_overlap_mm3'] > 0:
            return {'type': kind, 'status': 'unavailable', 'reason': 'overlap_volume_unavailable', 'check': spec}
        total = sum(v for v in volumes if v is not None)
        passes = (count == 0) if spec['max_overlap_mm3'] == 0 else total <= spec['max_overlap_mm3']
        return {'type': kind, 'status': 'pass' if passes else 'violation', 'check': spec,
                'evidence': {'interference_count': count, 'overlap_mm3': total if all(v is not None for v in volumes) else None}}
    evidence = {'interference_count': count, 'frame': 'root',
                'overlap_mm3': sum(volumes) if all(v is not None for v in volumes) else None}
    try:
        evidence.update(_distance_evidence(measure.measureMinimumDistance(bodies[0], bodies[1])))
    except Exception as exc:
        detail, truncated = _measurement_detail(exc)
        evidence.update({'minimum_distance_mm': None, 'closest_point_a_mm': None,
                         'closest_point_b_mm': None, 'measurement_unavailable': detail,
                         'measurement_detail_truncated': truncated})
        return {'type': kind, 'status': 'violation' if count else 'unavailable', 'check': spec, 'evidence': evidence}
    if not evidence['scalar_valid'] or evidence['witness_consistency']['status'] != 'consistent':
        return {'type': kind, 'status': 'violation' if count else 'unavailable', 'check': spec,
                'reason': 'clearance_distance_witness_unavailable', 'evidence': evidence}
    distance_mm = evidence['minimum_distance_mm']
    passes = count == 0 and distance_mm >= spec['min_mm']
    return {'type': kind, 'status': 'pass' if passes else 'violation', 'check': spec, 'evidence': evidence}

def run(_ctx):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({'ok': False, 'error': 'no_active_design'})); return
    root = design.rootComponent
    results = []
    for spec in _CHECKS:
        try: results.append(_check(design, root, app.measureManager, spec))
        except Exception as exc: results.append({'type': spec['type'], 'status': 'unavailable', 'reason': str(exc), 'check': spec})
    _bound_validation_output(results)
    print(json.dumps({'ok': True, 'passed': all(r['status'] == 'pass' for r in results),
                      'checks': results, 'units': {'distance': 'mm', 'volume': 'mm^3'}}))
'''


def validate_design(adapter: FusionAdapter, checks: list[dict[str, Any]]) -> Envelope:
    try:
        script = build_validate_design(checks)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    env = execute_typed_script(adapter, script, read_only=True)
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return script_parse_failure(env, "validation_parse_failed", read_only=True)
    return Envelope(state=env.state, ok=parsed.get("ok") is True, error=parsed.get("error"), result=parsed, message=env.message)
