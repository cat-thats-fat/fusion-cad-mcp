"""Shared, read-only native distance evidence for generated Fusion scripts.

Consistency is a numerical cross-check, not a proof of minimum distance or of
point membership on the selected geometry. Keep native scalars on witness failure.
"""

DISTANCE_EVIDENCE_SCRIPT = '''
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
        issues.append({'field': field, 'detail': str(exc)})
        return None

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
        issues.append({'field': 'value', 'detail': str(exc)})
    try:
        native_valid = result is not None and bool(getattr(result, 'isValid', True))
    except Exception as exc:
        native_valid = False
        issues.append({'field': 'isValid', 'detail': str(exc)})
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
