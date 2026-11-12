"""Shared, read-only native distance evidence for generated Fusion scripts.

Consistency is a numerical cross-check, not a proof of minimum distance or of
point membership on the selected geometry. Keep native scalars on witness failure.
"""

DISTANCE_EVIDENCE_SCRIPT = '''
def _measurement_detail(value, limit=256):
    try: text = str(value)
    except BaseException: text = '<detail unavailable>'
    # Bound the escaped JSON footprint, including control characters and Unicode.
    lo, hi = 0, min(len(text), limit)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(json.dumps(text[:mid], ensure_ascii=True)) <= limit: lo = mid
        else: hi = mid - 1
    return text[:lo], lo < len(text)

def _measurement_issue(field, detail):
    text, truncated = _measurement_detail(detail)
    return {'field': field, 'detail': text, 'detail_truncated': truncated}

def _distance_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

def _distance_point(result, field, issues):
    try:
        point = getattr(result, field)
        raw = [point.x, point.y, point.z]
        if not all(_distance_number(x) for x in raw):
            raise ValueError('non-finite coordinates')
        values = [x * 10 for x in raw]
        if not all(_distance_number(x) for x in values):
            raise ValueError('non-finite coordinates')
        return values
    except Exception as exc:
        issues.append(_measurement_issue(field, exc))
        return None

def _face_membership(face, point_mm):
    evidence = {'status': 'unavailable', 'method': 'Fusion BRepFace.isPointOnFace',
                'tolerance_source': 'explicit Application.pointTolerance', 'tolerance_mm': None,
                'membership_frame': 'native_component', 'frame_conversion': 'none'}
    try:
        raw_tolerance = adsk.core.Application.get().pointTolerance
        if not _distance_number(raw_tolerance) or raw_tolerance <= 0:
            raise ValueError('native point tolerance unavailable')
        tolerance = raw_tolerance * 10
        if _distance_number(tolerance) and tolerance >= 0:
            evidence['tolerance_mm'] = tolerance
        else:
            raise ValueError('native point tolerance unavailable')
        if point_mm is None:
            raise ValueError('surface witness point unavailable')
        evidence['input_point_mm'] = list(point_mm)
        point = adsk.core.Point3D.create(*[x / 10 for x in point_mm])
        if point is None: raise ValueError('native point creation unavailable')
        context = getattr(face, 'assemblyContext', None)
        native = getattr(face, 'nativeObject', None)
        target = face
        if context is not None:
            if native is None or not getattr(native, 'isValid', True):
                raise ValueError('native face unavailable for proxy membership')
            inverse = context.transform2.copy()
            if inverse is None or inverse.invert() is not True:
                raise ValueError('occurrence transform inversion unavailable')
            if point.transformBy(inverse) is not True:
                raise ValueError('native membership point transformation unavailable')
            target = native
            path, path_truncated = _measurement_detail(context.fullPathName)
            evidence.update(frame_conversion='inverse_occurrence_transform2',
                            occurrence_path=path, occurrence_path_truncated=path_truncated)
        elif native is not None:
            raise ValueError('proxy membership context unavailable')
        native_point_mm = [point.x * 10, point.y * 10, point.z * 10]
        if not all(_distance_number(x) for x in native_point_mm):
            raise ValueError('non-finite native membership point')
        evidence['membership_point_mm'] = native_point_mm
        member = target.isPointOnFace(point, raw_tolerance)
        if type(member) is not bool:
            raise ValueError('native face membership did not return a boolean')
        evidence['status'] = 'on_face' if member else 'off_face'
    except Exception as exc:
        detail, truncated = _measurement_detail(exc)
        evidence.update(detail=detail, detail_truncated=truncated)
    return evidence

def _face_pair_witnesses(evidence, a, b):
    raw_a, raw_b = evidence['closest_point_a_mm'], evidence['closest_point_b_mm']
    original = {'a': _face_membership(a, raw_a), 'b': _face_membership(b, raw_b)}
    evidence.update(face_membership=original, witness_order='as_returned')
    if all(item['status'] == 'on_face' for item in original.values()) or raw_a == raw_b: return
    evidence['native_witnesses'] = {'closest_point_a_mm': raw_a, 'closest_point_b_mm': raw_b,
                                    'face_membership': original}
    swapped = {'a': _face_membership(a, raw_b), 'b': _face_membership(b, raw_a)}
    evidence['swapped_face_membership'] = swapped
    if (evidence['scalar_valid'] and evidence['witness_consistency']['status'] == 'consistent'
            and all(item['status'] == 'on_face' for item in swapped.values())):
        evidence.pop('swapped_face_membership')  # The accepted checks are face_membership below.
        evidence.update(closest_point_a_mm=raw_b, closest_point_b_mm=raw_a,
                        face_membership=swapped, witness_order='swapped_to_match_entities')

def _distance_evidence(result, input_point_mm=None):
    evidence = {'minimum_distance_mm': None, 'closest_point_a_mm': None,
                'closest_point_b_mm': None, 'units': 'mm',
                'method': 'Fusion MeasureManager.measureMinimumDistance'}
    issues = []
    scalar_valid = False
    try:
        raw = result.value
        if not _distance_number(raw):
            evidence['native_value_repr'] = repr(raw)[:200]
            raise ValueError('distance must be finite and nonnegative')
        value = raw * 10
        if not _distance_number(value) or value < 0:
            evidence['native_value_repr'] = repr(result.value)[:200]
            raise ValueError('distance must be finite and nonnegative')
        evidence['minimum_distance_mm'] = value
        scalar_valid = True
    except Exception as exc:
        issues.append(_measurement_issue('value', exc))
    try:
        native_valid = result is not None and bool(getattr(result, 'isValid', True))
    except Exception as exc:
        native_valid = False
        issues.append(_measurement_issue('isValid', exc))
    if not native_valid:
        issues.append({'field': 'isValid', 'detail': 'native measurement unavailable or invalid'})
    a = _distance_point(result, 'positionOne', issues)
    b = _distance_point(result, 'positionTwo', issues)
    evidence['closest_point_a_mm'], evidence['closest_point_b_mm'] = a, b
    consistency = {'status': 'unavailable', 'separation_mm': None, 'scalar_delta_mm': None,
                   'numerical_tolerance_mm': None,
                   'scope': 'scalar/witness consistency only; not surface membership or minimum proof'}
    if scalar_valid and native_valid and a is not None and b is not None:
        # Diagnostic floor plus floating-point roundoff, independent of caller acceptance.
        scale = max([1.0, evidence['minimum_distance_mm']] + [abs(x) for x in a + b])
        tolerance = 1e-6 + 64 * math.ulp(scale)
        separation = math.dist(a, b)
        delta = abs(separation - evidence['minimum_distance_mm'])
        consistency.update(numerical_tolerance_mm=tolerance)
        if math.isfinite(separation) and math.isfinite(delta):
            consistency.update(separation_mm=separation, scalar_delta_mm=delta,
                               status='consistent' if delta <= tolerance else 'inconsistent')
        else:
            issues.append({'field': 'separation', 'detail': 'non-finite witness separation'})
        if input_point_mm is not None:
            anchor_delta = math.dist(a, input_point_mm)
            consistency['input_point_delta_mm'] = anchor_delta if math.isfinite(anchor_delta) else None
            if not math.isfinite(anchor_delta) or anchor_delta > tolerance:
                consistency['status'] = 'inconsistent'
                issues.append({'field': 'positionOne', 'detail': 'does not match input point'})
    if issues:
        consistency['issues'] = issues
    evidence['scalar_valid'] = scalar_valid and native_valid
    evidence['witness_consistency'] = consistency
    return evidence
'''
