"""Shared post-creation feature health check for generated Fusion scripts."""

FEATURE_HEALTH_SCRIPT = """
def _feature_health_error(feature):
    try:
        name = getattr(feature, 'name', None)
    except Exception:
        name = None
    try:
        state = feature.healthState
        if state is None:
            raise ValueError('missing health state')
        state = int(state)
    except Exception:
        return {'ok': False, 'error': 'feature_health_unavailable', 'feature_name': name,
                'model_may_have_changed': True}
    if state != int(adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState):
        try:
            message = feature.errorOrWarningMessage or ''
        except Exception:
            message = ''
        return {'ok': False, 'error': 'feature_unhealthy', 'feature_name': name,
                'health_state': state, 'health_message': message,
                'model_may_have_changed': True}
    return None
"""

FEATURE_HEALTH_CHECK = """    _health_error = _feature_health_error(feat)
    if _health_error:
        print(json.dumps(_health_error)); return
"""
