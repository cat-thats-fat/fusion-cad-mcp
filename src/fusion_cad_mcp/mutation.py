"""Embedded Fusion helpers for targeted mutation cleanup and measured readback.

The helper is scoped to one native component, or all design components when
owner is None. It tracks only entities and expression edits recorded by the
caller. A verified rollback means the recorded inventory, identity, health,
expressions, body measurements, and recorded assembly poses match. It makes no
claim about equivalent topology, document history, viewport, or saved state.
"""

PARAMETER_VALUE_SCRIPT = """
import math


def _mutation_parameter_value(parameter):
    if getattr(parameter, 'valueType', 0) == 1 or parameter.unit.casefold() == 'text':
        return parameter.textValue
    value = parameter.value
    if not math.isfinite(value):
        raise ValueError('Parameter value is not finite')
    return value
"""


MUTATION_SCRIPT = PARAMETER_VALUE_SCRIPT + """
class _MutationError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def _mutation_items(collection):
    return [collection.item(i) for i in range(collection.count)]


def _mutation_health(entity, required=False):
    if not entity.isValid:
        raise _MutationError('entity_invalid', 'An inspected entity is no longer valid')
    state = getattr(entity, 'healthState', None)
    if required and state not in (0, 1, 2, 3, 4):
        raise _MutationError('health_unavailable', 'Fusion did not report an inspectable health state',
                             entity_name=entity.name)
    message = entity.errorOrWarningMessage if state in (1, 2) else ''
    return state, message or ''


def _mutation_body_values(body):
    box = body.boundingBox
    values = (body.volume, box.minPoint.x, box.minPoint.y, box.minPoint.z,
              box.maxPoint.x, box.maxPoint.y, box.maxPoint.z)
    if not all(math.isfinite(value) for value in values):
        raise _MutationError('body_measurement_invalid', 'Body measurements are not finite')
    return values


def _mutation_pose(design, occurrence):
    # Native occurrences inside subcomponents do not expose isGrounded in
    # Fusion. Their root-context proxies do, and repeated parent instances can
    # produce several such proxies for the same native occurrence.
    local_transform = tuple(occurrence.transform2.asArray())
    proxies = []
    occurrences = design.rootComponent.allOccurrences
    for i in range(occurrences.count):
        proxy = occurrences.item(i)
        native = getattr(proxy, 'nativeObject', None) or proxy
        if native == occurrence:
            path = getattr(proxy, 'fullPathName', None) or proxy.name
            proxies.append((path, tuple(proxy.transform2.asArray()), proxy.isGrounded,
                            getattr(proxy, 'isGroundToParent', None)))
    if not proxies:
        raise _MutationError('occurrence_context_unavailable',
                             'Cannot inspect a root-context occurrence for rollback',
                             entity_name=occurrence.name)
    proxies.sort(key=lambda row: row[0])
    return local_transform, tuple(proxies)


def _mutation_joint_values(joint, health_required):
    motion = joint.jointMotion
    attributes = ('rotationValue', 'slideValue', 'primarySlideValue', 'secondarySlideValue',
                  'rollValue', 'pitchValue', 'yawValue')
    return (_mutation_health(joint, required=health_required), joint.isSuppressed,
            getattr(joint, 'isLocked', None),
            tuple(getattr(motion, attr, None) for attr in attributes))


def _mutation_same(a, b):
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_mutation_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8)
    return a == b


class _Mutation:
    def __init__(self, design, owner):
        self.design = design
        self.owner = owner
        self.actions = []
        self.created = []
        timeline = getattr(design, 'timeline', None)
        if timeline is not None and timeline.markerPosition != timeline.count:
            raise _MutationError('timeline_rolled_back', 'Move the timeline marker to the end before editing')
        self.timeline_count = timeline.count if timeline is not None else None
        self.baseline = self._snapshot()

    def _snapshot(self):
        components = [self.owner] if self.owner is not None else _mutation_items(self.design.allComponents)
        if self.owner is None and self.design.rootComponent not in components:
            components.insert(0, self.design.rootComponent)
        groups = [('components', [(comp, comp.name) for comp in components])]
        for comp in components:
            for category in ('features', 'sketches', 'bRepBodies', 'occurrences', 'joints',
                             'asBuiltJoints', 'constructionPlanes', 'constructionAxes', 'constructionPoints'):
                rows = []
                collection = getattr(comp, category, None)
                if collection is None and category in ('joints', 'asBuiltJoints',
                                                        'constructionPlanes', 'constructionAxes', 'constructionPoints'):
                    continue
                for entity in _mutation_items(collection):
                    if not entity.isValid:
                        raise _MutationError('entity_invalid', 'A baseline entity is no longer valid')
                    if category in ('features', 'sketches', 'constructionPlanes', 'constructionAxes', 'constructionPoints'):
                        value = _mutation_health(entity, required=True)
                    elif category == 'bRepBodies':
                        value = _mutation_body_values(entity)
                    elif category == 'occurrences':
                        value = _mutation_pose(self.design, entity)
                    else:
                        value = _mutation_joint_values(entity, health_required=category == 'joints')
                    rows.append((entity, (entity.name, value)))
                groups.append((comp.name + ' / ' + category, rows))
        params = []
        for param in _mutation_items(self.design.userParameters):
            if not param.isValid:
                raise _MutationError('entity_invalid', 'A baseline parameter is no longer valid')
            params.append((param, (param.name, param.expression, param.unit, param.comment,
                                   _mutation_parameter_value(param))))
        groups.append(('userParameters', params))
        return groups

    def track(self, entity):
        if entity is None:
            raise _MutationError('creation_failed', 'Fusion returned no created entity')
        self.created.append(entity)
        self.actions.append((entity, None))
        return entity

    def record_expression(self, parameter):
        self.actions.append((parameter, parameter.expression))

    def _compute(self):
        if not self.design.computeAll():
            raise _MutationError('recompute_failed', 'Fusion did not complete recompute')

    def verify(self):
        self._compute()
        timeline = getattr(self.design, 'timeline', None)
        if timeline is not None and timeline.markerPosition != timeline.count:
            raise _MutationError('timeline_rolled_back', 'The mutation left the timeline rolled back')
        health_checked = 0
        for entity in self.created:
            kind = getattr(entity, 'objectType', '').rsplit('::', 1)[-1]
            requires_health = kind.endswith('Feature') or kind in ('Sketch', 'Joint',
                                                                 'ConstructionPlane', 'ConstructionAxis', 'ConstructionPoint')
            state, message = _mutation_health(entity, required=requires_health)
            health_checked += state is not None
            if state not in (None, 0):
                raise _MutationError('created_entity_unhealthy', 'A created entity is unhealthy',
                                     entity_name=entity.name, health=state, warning=message)
        checked = preexisting = 0
        for label, rows in self.baseline:
            category = label.rsplit(' / ', 1)[-1]
            if category not in ('features', 'sketches', 'joints', 'constructionPlanes', 'constructionAxes', 'constructionPoints'):
                continue
            for entity, (name, before) in rows:
                if category == 'joints':
                    before = before[0]
                after = _mutation_health(entity, required=True)
                checked += 1
                preexisting += before[0] in (1, 2)
                if after[0] not in (None, 0) and after != before and not (before[0] == 2 and after[0] == 1):
                    raise _MutationError('feature_health_regressed', 'An existing entity changed health',
                                         entity_name=name, before=before, after=after)
        return {'recomputed': True, 'created_checked': len(self.created), 'created_health_checked': health_checked,
                'existing_health_checked': checked, 'preexisting_unhealthy': preexisting}

    def rollback(self):
        deleted = 0
        problems = []
        for entity, expression in reversed(self.actions):
            try:
                if expression is None:
                    if entity.isValid:
                        entity.deleteMe()
                    if entity.isValid:
                        raise RuntimeError('Tracked entity still exists after delete')
                    deleted += 1
                else:
                    if entity.expression != expression:
                        entity.expression = expression
                    if entity.expression != expression:
                        raise RuntimeError('Prior parameter expression could not be restored')
            except Exception as exc:
                problems.append(str(exc))
        recomputed = False
        try:
            self._compute()
            recomputed = True
        except Exception as exc:
            problems.append(str(exc))
        checks = {'recomputed': recomputed, 'inventory_checked': False, 'changed_groups': []}
        try:
            timeline = getattr(self.design, 'timeline', None)
            if timeline is not None and (timeline.markerPosition != self.timeline_count
                                         or timeline.count != self.timeline_count):
                checks['changed_groups'].append('timeline')
            current = self._snapshot()
            if len(current) != len(self.baseline):
                checks['changed_groups'].append('component scope')
            for (label, prior), (current_label, now) in zip(self.baseline, current):
                if label != current_label or len(prior) != len(now) or any(
                    not old.isValid or old != new or not _mutation_same(old_value, new_value)
                    for (old, old_value), (new, new_value) in zip(prior, now)
                ):
                    checks['changed_groups'].append(label)
            checks['inventory_checked'] = True
        except Exception as exc:
            problems.append(str(exc))
        return {'status': 'failed' if problems or checks['changed_groups'] else 'verified',
                'deleted_count': deleted, 'checks': checks, 'problems': problems}


def _mutation_failure(tx, exc, code='mutation_failed', **details):
    if isinstance(exc, _MutationError):
        code = exc.code
        details.update(exc.details)
    result = {'ok': False, 'error': code, 'detail': str(exc), **details}
    if tx is not None:
        result['rollback'] = tx.rollback()
    return result
"""
