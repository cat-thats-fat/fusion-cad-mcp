"""Bounded preparation and transient native implementation for sampled pose checks."""

from __future__ import annotations

import json
import math
from typing import Any


def _finite(value: Any) -> bool:
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        return False


def _vector(value: Any, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3 or not all(_finite(x) for x in value):
        raise ValueError(f"{field} must contain three finite numbers")
    return [float(x) for x in value]


def prepare_pose(check: dict[str, Any]) -> dict[str, Any]:
    if check.get("frame") != "root":
        raise ValueError("pose_interference requires frame=root")
    threshold = check.get("max_overlap_mm3", 0)
    if not _finite(threshold) or threshold < 0:
        raise ValueError("max_overlap_mm3 must be finite and nonnegative")
    poses = check.get("poses")
    if not isinstance(poses, list) or not 1 <= len(poses) <= 32:
        raise ValueError("pose_interference requires 1..32 poses")
    result, ids = [], set()
    for pose in poses:
        if not isinstance(pose, dict) or set(pose) - {"id", "translation_mm", "rotation_deg", "rotation_axis", "pivot_mm"}:
            raise ValueError("pose has unsupported fields")
        name = pose.get("id")
        if not isinstance(name, str) or not name or name in ids:
            raise ValueError("pose ids must be unique nonempty strings")
        try:
            valid_length = (len(name.encode("utf-8")) <= 128 and
                            len(json.dumps(name, ensure_ascii=True).encode("utf-8")) - 2 <= 128)
        except UnicodeEncodeError:
            valid_length = False
        if not valid_length:
            raise ValueError("pose id must fit 128 UTF-8 bytes and 128 escaped JSON bytes")
        ids.add(name)
        translation = _vector(pose.get("translation_mm"), "translation_mm")
        angle = pose.get("rotation_deg", 0)
        if not _finite(angle):
            raise ValueError("rotation_deg must be finite")
        axis = _vector(pose.get("rotation_axis", [0, 0, 1]), "rotation_axis")
        norm = math.hypot(*axis)
        if not math.isfinite(norm) or norm < 1e-12:
            raise ValueError("rotation_axis must be nonzero")
        result.append({"id": name, "translation_mm": translation, "rotation_deg": float(angle),
                       "rotation_axis": [x / norm for x in axis],
                       "pivot_mm": _vector(pose.get("pivot_mm", [0, 0, 0]), "pivot_mm")})
    return {"frame": "root", "max_overlap_mm3": float(threshold), "poses": result}


POSE_SCRIPT = '''
def _pose_reason(exc):
    try: detail = str(exc)
    except Exception: detail = 'exception message unavailable'
    # Return at most 512 bytes in the receipt's escaped JSON representation.
    def size(value): return len(json.dumps(value, ensure_ascii=True).encode('utf-8')) - 2
    if size(detail) <= 512: return detail, False
    lo, hi = 0, len(detail)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if size(detail[:mid]) <= 512: lo = mid
        else: hi = mid - 1
    return detail[:lo], True

def _pose_copy(manager, root, body):
    if not getattr(body, 'isSolid', False): raise ValueError('solid body required')
    if body.faces.count > 512: raise ValueError('body face limit exceeded')
    context = getattr(body, 'assemblyContext', None)
    native = getattr(body, 'nativeObject', None) if context is not None else body
    if native is None: raise ValueError('native body unavailable')
    if context is None:
        if getattr(body, 'parentComponent', None) != root: raise ValueError('body root context unavailable')
    else:
        matches = [root.allOccurrences.item(i) for i in range(root.allOccurrences.count)
                   if root.allOccurrences.item(i).fullPathName == context.fullPathName]
        if len(matches) != 1 or matches[0] != context: raise ValueError('occurrence root context unavailable')
    copy = manager.copy(native)
    if copy is None or copy == body or copy == native: raise ValueError('temporary copy unavailable')
    if context is not None and not manager.transform(copy, context.transform2):
        raise ValueError('occurrence transform failed')
    return copy

def _pose_matrix(pose):
    matrix = adsk.core.Matrix3D.create()
    axis = adsk.core.Vector3D.create(*pose['rotation_axis'])
    pivot = adsk.core.Point3D.create(*[x / 10 for x in pose['pivot_mm']])
    if not matrix.setToRotation(math.radians(pose['rotation_deg']), axis, pivot):
        raise ValueError('rotation matrix failed')
    return matrix

def _pose_disjoint(a, b):
    a, b = a.boundingBox, b.boundingBox
    vals = [a.minPoint.x, a.minPoint.y, a.minPoint.z, a.maxPoint.x, a.maxPoint.y, a.maxPoint.z,
            b.minPoint.x, b.minPoint.y, b.minPoint.z, b.maxPoint.x, b.maxPoint.y, b.maxPoint.z]
    if not all(math.isfinite(v) for v in vals) or any(vals[i] > vals[i+3] or vals[i+6] > vals[i+9] for i in range(3)):
        raise ValueError('invalid bounding box')
    return any(vals[i+3] < vals[i+6] or vals[i+9] < vals[i] for i in range(3))

def _pose_check(design, root, spec):
    result = {'type': 'pose_interference', 'check': spec}
    bodies = []
    for field in ('body_a', 'body_b'):
        body, error = _body(design, root, spec[field])
        if error: return dict(result, status='unavailable', reason=error, poses=[])
        if not getattr(body, 'isValid', True): return dict(result, status='unavailable', reason='body_invalid', poses=[])
        bodies.append(body)
    contexts = [getattr(getattr(b, 'assemblyContext', None), 'fullPathName', None) for b in bodies]
    natives = [getattr(b, 'nativeObject', None) or b for b in bodies]
    if bodies[0] == bodies[1] or (contexts[0] == contexts[1] and natives[0] == natives[1]):
        return dict(result, status='unavailable', reason='same_body', poses=[])
    rows = []
    try:
        if sum(b.faces.count for b in bodies) > 1024: raise ValueError('total face limit exceeded')
        manager = adsk.fusion.TemporaryBRepManager.get()
        if manager is None: raise ValueError('temporary BRep manager unavailable')
        stationary = _pose_copy(manager, root, bodies[1])
    except Exception as exc:
        reason, truncated = _pose_reason(exc)
        return dict(result, status='unavailable', reason=reason, reason_truncated=truncated, poses=[])
    for pose in spec['poses']:
        try:
            moving = _pose_copy(manager, root, bodies[0])
            if not manager.transform(moving, _pose_matrix(pose)): raise ValueError('pose rotation failed')
            translation = adsk.core.Matrix3D.create()
            translation.translation = adsk.core.Vector3D.create(*[x / 10 for x in pose['translation_mm']])
            if not manager.transform(moving, translation): raise ValueError('pose translation failed')
            if _pose_disjoint(moving, stationary):
                rows.append({'id': pose['id'], 'status': 'pass', 'overlap_mm3': 0, 'method': 'strictly disjoint AABBs'})
                continue
            if not manager.booleanOperation(moving, stationary, adsk.fusion.BooleanTypes.IntersectionBooleanType):
                raise ValueError('intersection returned false')
            volume = moving.volume * 1000
            if not math.isfinite(volume) or volume < 0: raise ValueError('intersection volume invalid')
            if volume == 0 or not getattr(moving, 'isSolid', False):
                raise ValueError('empty or non-solid intersection is not established')
            rows.append({'id': pose['id'], 'status': 'pass' if volume <= spec['max_overlap_mm3'] else 'violation',
                         'overlap_mm3': volume, 'method': 'temporary BRep intersection'})
        except Exception as exc:
            reason, truncated = _pose_reason(exc)
            rows.append({'id': pose['id'], 'status': 'unavailable', 'reason': reason, 'reason_truncated': truncated})
    status = 'violation' if any(r['status'] == 'violation' for r in rows) else ('unavailable' if any(r['status'] != 'pass' for r in rows) else 'pass')
    return dict(result, status=status, poses=rows, frame='root', units={'distance': 'mm', 'volume': 'mm^3'},
                moving_body='body_a', scope='sampled poses only; not continuous motion or clearance')
'''
