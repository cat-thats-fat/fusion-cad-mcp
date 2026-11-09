"""Generate bounded Fusion scripts for local workspace coordination.

The registry contains native object references only in the current Fusion Python
session. Its handles cannot be reconstructed from names or persisted hints.
"""
from __future__ import annotations

import hashlib
import inspect
import json

from . import edit_contract
from .resolver import RESOLVER_SCRIPT
from .tools.parameters import _PARAMETER_SCRIPT
from .tools.validation import _SCRIPT as _VALIDATION_SCRIPT


def build_workspace_script(action: str, payload: dict) -> str:
    if action not in {"bind", "inspect", "edit", "export", "open", "activate", "close"}:
        raise ValueError("unsupported workspace action")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a mapping")
    # JSON also rejects opaque objects and makes generated source deterministic.
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return (
        "import adsk.core, adsk.fusion, json, math, os, sys, types, uuid, hashlib, tempfile, time\n"
        + RESOLVER_SCRIPT + _PARAMETER_SCRIPT + _VALIDATION_SCRIPT.split("\ndef run(_ctx):", 1)[0]
        + "\n" + inspect.getsource(edit_contract)
        + "\n_ACTION = " + repr(action) + "\n_ARGS = json.loads(" + repr(data) + ")\n"
        + _WORKSPACE_SCRIPT
    )


def runtime_digest() -> str:
    """Hash the complete embedded executable, excluding caller-specific payloads."""
    source = build_workspace_script("bind", {})
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


_WORKSPACE_SCRIPT = r'''
_REGISTRY_NAME = '_fusion_cad_workspace_v1'
_EFFECT_STARTED = False

class _WorkspaceError(Exception):
    def __init__(self, code, detail=''):
        self.code, self.detail = code, detail
        super().__init__(detail or code)

def _registry():
    module = sys.modules.get(_REGISTRY_NAME)
    if module is None:
        module = types.ModuleType(_REGISTRY_NAME)
        module.epoch = str(uuid.uuid4())
        module.entries = {}
        sys.modules[_REGISTRY_NAME] = module
    if not isinstance(module, types.ModuleType) or not hasattr(module, 'entries') or not hasattr(module, 'epoch'):
        raise _WorkspaceError('registry_invalid')
    return module

def _live(entry):
    try:
        return bool(entry['document'].isValid and entry['design'].isValid and entry['root'].isValid)
    except Exception:
        return False

def _capacity():
    reg = _registry()
    for token, entry in list(reg.entries.items()):
        if not _live(entry): del reg.entries[token]
    if len(reg.entries) >= 128: raise _WorkspaceError('registry_full')
    return reg

def _register(app, document, design, root, owned=False):
    reg = _capacity()
    token = str(uuid.uuid4())
    reg.entries[token] = {'document': document, 'design': design, 'root': root, 'owned': owned, 'pending': None}
    handle = reg.epoch + ':' + token
    _resolve(app, handle)
    return handle

def _resolve(app, handle):
    reg = sys.modules.get(_REGISTRY_NAME)
    if not isinstance(reg, types.ModuleType) or not hasattr(reg, 'entries') or not hasattr(reg, 'epoch'):
        raise _WorkspaceError('target_invalid')
    if not isinstance(handle, str) or ':' not in handle: raise _WorkspaceError('target_invalid')
    epoch, token = handle.split(':', 1)
    entry = reg.entries.get(token) if epoch == reg.epoch else None
    if entry is None or not _live(entry): raise _WorkspaceError('target_invalid')
    document, design, root = entry['document'], entry['design'], entry['root']
    matches = [app.documents.item(i) for i in range(app.documents.count) if app.documents.item(i) == document]
    if len(matches) != 1 or design.parentDocument != document or design.rootComponent != root or root.parentDesign != design:
        raise _WorkspaceError('target_binding_changed')
    return entry

def _idle(app):
    if app.userInterface.activeCommand != 'SelectCommand':
        raise _WorkspaceError('command_active')

def _active(app, entry):
    design, root, document = entry['design'], entry['root'], entry['document']
    if app.activeDocument != document or app.activeProduct != design:
        raise _WorkspaceError('target_not_active')
    if design.activeComponent != root or design.activeEditObject != root:
        raise _WorkspaceError('root_edit_context_required')
    _idle(app)

def _no_external(entry):
    refs = entry['document'].documentReferences
    if refs is None or refs.count: raise _WorkspaceError('external_references_unsupported')
    occurrences = entry['root'].allOccurrences
    for i in range(occurrences.count):
        if occurrences.item(i).isReferencedComponent: raise _WorkspaceError('external_references_unsupported')

def _timeline_end(design):
    timeline = design.timeline
    if timeline is None or timeline.markerPosition != timeline.count:
        raise _WorkspaceError('timeline_rolled_back')

def _available(entry):
    if entry['pending'] is not None: raise _WorkspaceError('operation_pending')

def _environment(app):
    return {'fusion_version': str(app.version), 'registry_version': 1}

def _binding(app, doc, owned=False):
    design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    if design is None: raise _WorkspaceError('design_unavailable')
    return _register(app, doc, design, design.rootComponent, owned)

def _parameter(design, name):
    parameter = design.userParameters.itemByName(name)
    if parameter is None or not parameter.isValid: raise _WorkspaceError('parameter_not_found')
    units = parameter.unit
    manager = getattr(design, 'fusionUnitsManager', None) or design.unitsManager
    if not units or not manager.isValidExpression('1 ' + units, 'mm'):
        raise _WorkspaceError('parameter_not_length')
    value = _mutation_parameter_value(parameter) * 10
    if not math.isfinite(value): raise _WorkspaceError('parameter_value_unavailable')
    return parameter, {'expression': parameter.expression, 'units': units, 'value_mm': value}

def _strict_body(root, selector):
    matches = []
    for i in range(root.bRepBodies.count):
        body = root.bRepBodies.item(i)
        if selector == 'root / ' + body.name: matches.append(body)
    for i in range(root.allOccurrences.count):
        occ = root.allOccurrences.item(i)
        for j in range(occ.bRepBodies.count):
            body = occ.bRepBodies.item(j)
            if selector == occ.fullPathName + ' / ' + body.name: matches.append(body)
    if len(matches) != 1: raise _WorkspaceError('body_unavailable')
    if not matches[0].isValid: raise _WorkspaceError('body_invalid')
    return matches[0]

def _strict_occurrence(root, selector):
    matches = [root.allOccurrences.item(i) for i in range(root.allOccurrences.count)
               if root.allOccurrences.item(i).fullPathName == selector]
    if len(matches) != 1 or not matches[0].isValid: raise _WorkspaceError('occurrence_unavailable')
    return matches[0]

def _measure(app, entry, checks):
    design, root = entry['design'], entry['root']
    result = {}
    for spec in checks:
        row = {key: spec[key] for key in ('metric', 'entities', 'frame', 'units')}
        if 'axis' in spec: row['axis'] = spec['axis']
        try:
            metric = spec['metric']
            if metric == 'occurrence_translation':
                occ = _strict_occurrence(root, spec['entities'][0])
                matrix = list(occ.transform2.asArray())
                if len(matrix) != 16 or not all(math.isfinite(v) for v in matrix): raise ValueError('invalid transform')
                row['matrix'] = matrix
                row['value'] = getattr(occ.transform2.translation, spec['axis']) * 10
            else:
                bodies = [_strict_body(root, name) for name in spec['entities']]
                if len(bodies) == 2 and bodies[0] == bodies[1]: raise ValueError('same body')
                if metric == 'body_extent':
                    box = bodies[0].preciseBoundingBox
                    row['value'] = (getattr(box.maxPoint, spec['axis']) - getattr(box.minPoint, spec['axis'])) * 10
                elif metric == 'body_volume':
                    if not bodies[0].isSolid: raise ValueError('volume requires solid body')
                    row['value'] = bodies[0].volume * 1000
                elif metric in ('clearance', 'interference'):
                    count, volumes = _interference(design, bodies[0], bodies[1])
                    row['interference_count'] = count
                    if metric == 'interference':
                        if any(v is None for v in volumes): raise ValueError('interference volume unavailable')
                        row['value'] = sum(volumes)
                    else:
                        try:
                            distance = app.measureManager.measureMinimumDistance(bodies[0], bodies[1])
                            if distance is None or not math.isfinite(distance.value) or distance.value < 0:
                                raise ValueError('distance unavailable')
                            row['value'] = distance.value * 10
                        except Exception:
                            if not count: raise
                            row['classification'] = 'exact'
                            row['reason'] = 'distance unavailable; interference detected'
                            result[spec['id']] = row
                            continue
                else: raise ValueError('unsupported metric')
            if not math.isfinite(row['value']): raise ValueError('nonfinite value')
            row['classification'] = 'exact'
        except Exception as exc:
            row.pop('value', None)
            row.pop('matrix', None)
            row.update({'classification': 'unknown', 'reason': str(exc)[:160]})
        result[spec['id']] = row
    return result

def _health(design):
    counts = {'healthy': 0, 'warning': 0, 'error': 0, 'unknown': 0}
    total = 0
    for i in range(design.allComponents.count):
        component = design.allComponents.item(i)
        for collection in (component.features, component.sketches):
            total += collection.count
            if total > 1000: raise _WorkspaceError('health_scope_too_large')
            for j in range(collection.count):
                entity = collection.item(j)
                state = getattr(entity, 'healthState', None) if entity.isValid else None
                key = {0: 'healthy', 1: 'warning', 2: 'error'}.get(state, 'unknown')
                counts[key] += 1
    return counts

def _workspace_pose_valid(matrix):
    return isinstance(matrix, list) and len(matrix) == 16 and all(_contract_finite(cell) for cell in matrix)

def _same_workspace_pose(before, after):
    return (_workspace_pose_valid(before) and _workspace_pose_valid(after)
            and all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8)
                    for a, b in zip(before, after, strict=True)))

def _rollback_witnesses(app, entry, contract, before_parameter, baseline, before_health):
    # The transaction snapshot guards inventory and coarse geometry. Reacquire
    # the declared exact measurements on the same live target after recompute.
    if _resolve(app, contract['target_handle']) is not entry:
        raise _WorkspaceError('target_binding_changed')
    _active(app, entry)
    _no_external(entry)
    design = entry['design']
    _timeline_end(design)
    _, parameter = _parameter(design, contract['parameter_name'])
    parameter_tolerance = contract['value_tolerance_mm']
    parameter_matches = (
        parameter['expression'] == before_parameter['expression']
        and parameter['units'] == before_parameter['units']
        and within_tolerance(parameter['value_mm'], before_parameter['value_mm'], parameter_tolerance)
        and within_tolerance(parameter['value_mm'], contract['expected_value_mm'], parameter_tolerance)
    )
    health_matches = _health(design) == before_health
    checks = contract['requirements'] + contract['protected']
    restored = _measure(app, entry, checks)
    mismatches, unavailable = [], []
    for spec in checks:
        check_id = spec['id']
        a, _, _, count_a = _contract_measurement(spec, baseline)
        b, _, _, count_b = _contract_measurement(spec, restored)
        if a is None or b is None:
            unavailable.append(check_id)
            continue
        if spec['metric'] == 'interference':
            count_a = baseline[check_id].get('interference_count')
            count_b = restored[check_id].get('interference_count')
            if any(type(count) is not int or count < 0 for count in (count_a, count_b)):
                unavailable.append(check_id)
                continue
        tolerance = spec['baseline_tolerance']
        if (count_a != count_b or not within_tolerance(b, a, tolerance)
                or not within_tolerance(b, spec['baseline'], tolerance)):
            mismatches.append(check_id)
        elif spec['metric'] == 'occurrence_translation':
            before_pose = baseline[check_id].get('matrix')
            after_pose = restored[check_id].get('matrix')
            if not (_workspace_pose_valid(before_pose) and _workspace_pose_valid(after_pose)):
                unavailable.append(check_id)
            elif not _same_workspace_pose(before_pose, after_pose):
                mismatches.append(check_id)
    status = 'passed'
    if mismatches or not parameter_matches or not health_matches:
        status = 'failed'
    elif unavailable:
        status = 'unknown'
    return {'status': status, 'classification': 'unknown' if unavailable else 'exact',
            'scope': 'declared rollback measurements', 'parameter_matches': parameter_matches,
            'health_matches': health_matches, 'mismatch_ids': mismatches, 'unavailable_ids': unavailable}

def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk: break
            digest.update(chunk); size += len(chunk)
    return digest.hexdigest(), size

def _dispatch(app):
    global _EFFECT_STARTED
    env = _environment(app)
    if _ACTION == 'bind':
        name = _ARGS.get('document_name')
        if name is None: doc = app.activeDocument
        else:
            matches = [app.documents.item(i) for i in range(app.documents.count) if app.documents.item(i).name == name]
            if len(matches) != 1: raise _WorkspaceError('document_not_unique')
            doc = matches[0]
        if doc is None: raise _WorkspaceError('document_unavailable')
        return {'ok': True, 'target_handle': _binding(app, doc), 'environment': env, 'document': {'name': doc.name}, 'owned': False}
    if _ACTION == 'open':
        _idle(app)
        _capacity()
        path, expected = _ARGS['path'], _ARGS['sha256']
        if not os.path.isabs(path) or len(expected) != 64: raise _WorkspaceError('invalid_import')
        fd, staged = tempfile.mkstemp(suffix='.f3d')
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, 'wb') as out, open(path, 'rb') as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk: break
                    digest.update(chunk); out.write(chunk)
                out.flush(); os.fsync(out.fileno())
            if digest.hexdigest() != expected: raise _WorkspaceError('artifact_hash_mismatch')
            manager = app.importManager
            options = manager.createFusionArchiveImportOptions(staged)
            if options is None: raise _WorkspaceError('import_options_failed')
            _EFFECT_STARTED = True
            doc = manager.importToNewDocument(options)
            if doc is None: raise _WorkspaceError('import_failed')
            return {'ok': True, 'target_handle': _binding(app, doc, True), 'environment': env, 'owned': True}
        finally:
            try: os.unlink(staged)
            except OSError: pass
    entry = _resolve(app, _ARGS['target_handle'])
    doc, design, root = entry['document'], entry['design'], entry['root']
    if _ACTION == 'activate':
        _idle(app)
        _available(entry)
        _EFFECT_STARTED = True
        if not doc.activate(): raise _WorkspaceError('activate_failed')
        return {'ok': True, 'environment': env}
    if _ACTION == 'close':
        _idle(app)
        _available(entry)
        if not entry['owned']: raise _WorkspaceError('user_document_close_refused')
        if doc.isSaved: raise _WorkspaceError('saved_document_close_refused')
        _EFFECT_STARTED = True
        if not doc.close(False): raise _WorkspaceError('close_failed')
        return {'ok': True, 'environment': env}
    _active(app, entry)
    if _ACTION == 'inspect':
        _no_external(entry)
        _timeline_end(design)
        parameter = None
        native_parameter = None
        expected_expression = _ARGS.get('expected_expression')
        if expected_expression is not None and not _ARGS.get('parameter_name'):
            raise _WorkspaceError('parameter_name_required')
        if _ARGS.get('parameter_name'):
            native_parameter, parameter = _parameter(design, _ARGS['parameter_name'])
        measurements = _measure(app, entry, _ARGS.get('checks', []))
        response = {'ok': True, 'parameter': parameter, 'measurements': measurements,
                    'health': _health(design), 'pending': entry['pending'], 'environment': env}
        if expected_expression is not None:
            try:
                expected = _parameter_expectation(design, expected_expression, native_parameter.unit)
                _parameter_readback(design, native_parameter, native_parameter.unit, expected)
                response['parameter_matches_expected'] = True
            except _MutationError:
                response['parameter_matches_expected'] = False
        return response
    _available(entry)
    _no_external(entry)
    _timeline_end(design)
    if _ACTION == 'export':
        path = _ARGS['path']
        if not os.path.isabs(path) or not path.lower().endswith('.f3d'): raise _WorkspaceError('invalid_export_path')
        manager = design.exportManager
        options = manager.createFusionArchiveExportOptions(path, root)
        if options is None: raise _WorkspaceError('export_options_failed')
        _EFFECT_STARTED = True
        if not manager.execute(options): raise _WorkspaceError('export_failed')
        digest, size = _hash_file(path)
        if size == 0: raise _WorkspaceError('empty_export')
        return {'ok': True, 'sha256': digest, 'bytes': size, 'environment': env}
    if _ACTION != 'edit': raise _WorkspaceError('unsupported_action')
    if not entry['owned']: raise _WorkspaceError('user_document_edit_refused')
    contract = {k: v for k, v in _ARGS.items() if k != 'custom_script'}
    prepared = prepare_edit_contract(**{k: v for k, v in contract.items() if k != 'version'})
    if prepared != contract: raise _WorkspaceError('invalid_contract')
    started = time.perf_counter()
    checks = prepared['requirements'] + prepared['protected']
    parameter, before_parameter = _parameter(design, prepared['parameter_name'])
    baseline = _measure(app, entry, checks)
    before_health = _health(design)
    if before_health['warning'] or before_health['error'] or before_health['unknown']:
        raise _WorkspaceError('baseline_health_not_clean')
    baseline_check = compare_baseline(prepared, baseline)
    if (before_parameter['expression'] != prepared['expected_expression'] or
        not within_tolerance(before_parameter['value_mm'], prepared['expected_value_mm'], prepared['value_tolerance_mm']) or
        not baseline_check['passed']):
        return {'ok': True, 'execution': 'refused', 'verification': 'not_run', 'mutation_state': 'not_started', 'baseline': baseline,
                'parameter': before_parameter, 'checks': baseline_check['checks'], 'recovery': {'status': 'not_needed'},
                'environment': env}
    expected = _parameter_expectation(design, prepared['expression'], parameter.unit)
    tx = _Mutation(design, None)
    tx.record_expression(parameter)
    entry['pending'] = prepared['operation_id']
    start_edit = time.perf_counter()
    try:
        _EFFECT_STARTED = True
        custom = _ARGS.get('custom_script')
        if custom is None:
            parameter.expression = prepared['expression']
        else:
            # Local suppression does not replace Fusion's process-wide stdout.
            namespace = {'print': lambda *args, **kwargs: None}
            exec(custom, namespace)
            namespace['run']({'app': app, 'document': doc, 'design': design, 'root': root})
        try:
            _resolve(app, prepared['target_handle'])
            _active(app, entry)
        except Exception as guard_exc:
            return {'ok': False, 'error': 'mutation_state_unknown', 'detail': str(guard_exc)[:160],
                    'mutation_state': 'unknown', 'execution': 'unknown', 'verification': 'incomplete',
                    'recovery': {'status': 'unresolved_partial'}, 'environment': env}
        health = tx.verify()
        after_health = _health(design)
        _parameter_readback(design, parameter, parameter.unit, expected)
        _, after_parameter = _parameter(design, prepared['parameter_name'])
        measured_at = time.perf_counter()
        result = _measure(app, entry, checks)
        comparison = compare_evidence(prepared, baseline, result, execution='completed')
        for row in comparison['checks']:
            if not row['protected'] or row['metric'] != 'occurrence_translation' or row['status'] == 'failed':
                continue
            check_id = row['id']
            before_matrix = baseline.get(check_id, {}).get('matrix')
            after_matrix = result.get(check_id, {}).get('matrix')
            if not (_workspace_pose_valid(before_matrix) and _workspace_pose_valid(after_matrix)):
                row.update(status='unknown', classification='unknown', reason='protected_pose_unavailable')
            elif not _same_workspace_pose(before_matrix, after_matrix):
                row.update(status='failed', reason='protected_pose_changed')
        comparison.update(_contract_check_summary(comparison['checks']))
        if comparison['failed_requirement_ids']:
            comparison['verification'] = 'failed'
        elif (comparison['unknown_requirement_ids'] or after_health['warning']
              or after_health['error'] or after_health['unknown']):
            comparison['verification'] = 'incomplete'
        entry['pending'] = None
        return {'ok': True, **comparison, 'baseline': baseline, 'result': result, 'parameter': after_parameter,
                'health': {'transaction': health, 'baseline': before_health, 'result': after_health}, 'recovery': {'status': 'not_needed'}, 'environment': env,
                'timings_ms': {'baseline': (start_edit-started)*1000, 'edit': (measured_at-start_edit)*1000,
                               'measure': (time.perf_counter()-measured_at)*1000}}
    except Exception as exc:
        try:
            _resolve(app, prepared['target_handle'])
            _active(app, entry)
        except Exception as guard_exc:
            return {'ok': False, 'error': 'mutation_state_unknown', 'detail': str(guard_exc)[:160],
                    'mutation_state': 'unknown', 'execution': 'unknown', 'verification': 'incomplete',
                    'recovery': {'status': 'unresolved_partial'}, 'environment': env}
        try: rollback = tx.rollback()
        except Exception as rollback_exc: rollback = {'status': 'failed', 'error': str(rollback_exc)[:160]}
        if rollback.get('status') == 'verified' and _ARGS.get('custom_script') is None:
            try:
                witnesses = _rollback_witnesses(app, entry, prepared, before_parameter, baseline, before_health)
            except Exception as witness_exc:
                witnesses = {'status': 'unknown', 'classification': 'unknown', 'reason': str(witness_exc)[:160]}
            rollback['witnesses'] = witnesses
            if witnesses['status'] == 'passed':
                entry['pending'] = None
                return {'ok': True, 'execution': 'failed', 'verification': 'not_run', 'error': str(exc)[:160],
                        'rollback_baseline': {'parameter': before_parameter, 'measurements': baseline,
                                              'health': before_health},
                        'recovery': {'status': 'rollback_verified', 'details': rollback}, 'environment': env}
            rollback['status'] = 'unconfirmed'
        return {'ok': False, 'error': 'mutation_state_unknown', 'detail': str(exc)[:160],
                'execution': 'unknown', 'verification': 'incomplete', 'mutation_state': 'unknown',
                'recovery': {'status': 'unresolved_partial', 'details': rollback}, 'environment': env}

def run(_ctx):
    global _EFFECT_STARTED
    _EFFECT_STARTED = False
    try:
        response = _dispatch(adsk.core.Application.get())
    except _WorkspaceError as exc:
        response = {'ok': False, 'error': exc.code, 'detail': exc.detail}
    except Exception as exc:
        response = {'ok': False, 'error': 'workspace_native_error', 'detail': str(exc)[:160]}
    if response.get('ok') is False and 'mutation_state' not in response:
        response['mutation_state'] = 'unknown' if _EFFECT_STARTED else 'not_started'
    json.dumps(response, allow_nan=False)
    print(json.dumps(response))
'''
