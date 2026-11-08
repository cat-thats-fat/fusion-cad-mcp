"""Bounded native Fusion sweep over explicitly selected sketch curves."""

from __future__ import annotations

import json
import re

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..handles import emit_scope_expr
from ..mutation import MUTATION_SCRIPT
from .sketch import SKETCH_RESOLVER_SCRIPT, _resolve_ref

_PATH_REF = re.compile(r"(line|arc|circle):([0-9]+)\Z")


def build_sweep(
    sketch: str,
    path_sketch: str,
    path_entities: list[str],
    profile_index: int = 0,
    name: str | None = None,
) -> str:
    if (
        not isinstance(sketch, str)
        or not sketch
        or not isinstance(path_sketch, str)
        or not path_sketch
    ):
        raise ValueError("sketch and path_sketch must be nonempty selectors")
    if isinstance(profile_index, bool) or not isinstance(profile_index, int) or profile_index < 0:
        raise ValueError("profile_index must be a nonnegative integer")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "name is required; use a unique feature name to guard against duplicate retries"
        )
    if not isinstance(path_entities, list) or not 1 <= len(path_entities) <= 128:
        raise ValueError("path_entities must contain 1 to 128 sketch-curve references")
    refs = []
    for ref in path_entities:
        match = _PATH_REF.fullmatch(ref) if isinstance(ref, str) else None
        if not match:
            raise ValueError("path references must be line:N, arc:N, or circle:N")
        canonical = f"{match.group(1)}:{int(match.group(2))}"
        if canonical in refs:
            raise ValueError("duplicate path reference")
        refs.append(canonical)
    expressions = [_resolve_ref(ref, "path_sk") for ref in refs]
    params = {
        "sketch": sketch,
        "path_sketch": path_sketch,
        "refs": refs,
        "profile_index": profile_index,
        "name": name,
    }
    return (
        "import adsk.core, adsk.fusion, json\n"
        + SKETCH_RESOLVER_SCRIPT
        + MUTATION_SCRIPT
        + "\nDATA = json.loads("
        + repr(json.dumps(params, allow_nan=False))
        + ")\n"
        + "def _scope(): return "
        + emit_scope_expr()
        + "\n"
        + "def _curves(path_sk): return ["
        + ", ".join(expressions)
        + "]\n"
        + _SWEEP
    )


_SWEEP = """
def run(_ctx):
    app = adsk.core.Application.get(); design = adsk.fusion.Design.cast(app.activeProduct)
    if design is None:
        print(json.dumps({'ok': False, 'error': 'no_active_design'})); return
    root = design.rootComponent
    sk, owner, error = _select_sketch(root, DATA['sketch'])
    if error: print(json.dumps(error)); return
    path_sk, path_owner, error = _select_sketch(root, DATA['path_sketch'])
    if error: print(json.dumps(error)); return
    if owner != path_owner:
        print(json.dumps({'ok': False, 'error': 'path_wrong_component'})); return
    occ = None
    if owner != root:
        occs = [root.allOccurrences.item(i) for i in range(root.allOccurrences.count) if root.allOccurrences.item(i).component == owner]
        if len(occs) != 1:
            print(json.dumps({'ok': False, 'error': 'shared_component_edit_unsupported'})); return
        occ = occs[0]
        if occ.isReferencedComponent:
            print(json.dumps({'ok': False, 'error': 'referenced_component_edit_unsupported'})); return
    if DATA['name'] and any(owner.features.item(i).name == DATA['name'] for i in range(owner.features.count)):
        print(json.dumps({'ok': False, 'error': 'feature_name_taken', 'name': DATA['name']})); return
    if DATA['profile_index'] >= sk.profiles.count:
        print(json.dumps({'ok': False, 'error': 'profile_index_out_of_range', 'count': sk.profiles.count})); return
    try:
        curves = _curves(path_sk)
        if any(curve is None or not curve.isValid for curve in curves):
            raise ValueError('One or more path curves are missing or invalid')
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': 'path_entity_invalid', 'detail': str(exc)})); return
    tx = None; attempted = False
    try:
        # Snapshot before feature creation; Path and input are non-document objects.
        tx = _Mutation(design, owner)
        collection = adsk.core.ObjectCollection.create()
        for curve in curves:
            contextual = curve.createForAssemblyContext(occ) if occ else curve
            if contextual is None:
                raise _MutationError('path_proxy_unavailable', 'Could not create path curve assembly proxy')
            collection.add(contextual)
        path = adsk.fusion.Path.create(collection, adsk.fusion.ChainedCurveOptions.noChainedCurves)
        if path is None: raise _MutationError('path_invalid', 'Fusion could not create the specified connected path')
        sweeps = owner.features.sweepFeatures
        inp = sweeps.createInput(sk.profiles.item(DATA['profile_index']), path,
                                  adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        if inp is None: raise _MutationError('sweep_input_invalid', 'Fusion could not create a sweep input')
        attempted = True
        feat = tx.track(sweeps.add(inp))
        if DATA['name']:
            feat.name = DATA['name']
            if feat.name != DATA['name']: raise _MutationError('feature_name_readback_failed', 'Feature name did not persist')
        verification = tx.verify()
        if feat.bodies.count == 0: raise _MutationError('sweep_empty', 'Sweep created no bodies')
        suffix = ('|occ=' + occ.fullPathName if occ else '') + _scope()
        bodies = []
        for i in range(feat.bodies.count):
            b = feat.bodies.item(i)
            if not b.isValid or not b.isSolid or not math.isfinite(b.volume) or b.volume <= 0:
                raise _MutationError('sweep_invalid_body', 'Sweep body must be a valid positive-volume solid')
            bounds = _mutation_body_values(b)
            handle_body = b.createForAssemblyContext(occ) if occ else b
            if handle_body is None:
                raise _MutationError('body_proxy_unavailable', 'Could not create body assembly proxy')
            bodies.append({'name': b.name, 'handle': 'body:' + b.name + suffix + ':' + handle_body.entityToken,
                           'volume_mm3': b.volume * 1000,
                           'bounds_mm': {'min': [x * 10 for x in bounds[1:4]], 'max': [x * 10 for x in bounds[4:7]]},
                           'frame': 'component_local'})
        handle_feature = feat.createForAssemblyContext(occ) if occ else feat
        if handle_feature is None:
            raise _MutationError('feature_proxy_unavailable', 'Could not create feature assembly proxy')
        print(json.dumps({'ok': True, 'feature_name': feat.name,
            'feature_handle': 'feature:' + feat.name + suffix + ':' + handle_feature.entityToken,
            'bodies': bodies, 'verification': verification, 'path_entities': DATA['refs']}))
    except Exception as exc:
        result = _mutation_failure(tx, exc, 'sweep_failed')
        if attempted and (tx is None or not tx.created): result['mutation_state'] = 'unknown'
        print(json.dumps(result))
"""


def sweep(
    adapter: FusionAdapter,
    sketch: str,
    path_sketch: str,
    path_entities: list[str],
    profile_index: int = 0,
    name: str | None = None,
) -> Envelope:
    try:
        script = build_sweep(sketch, path_sketch, path_entities, profile_index, name)
    except (ValueError, TypeError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    env = adapter.execute_script(script)
    if not env.ok:
        return env
    parsed = parse_status_json(env)
    if parsed is None:
        return script_parse_failure(env, "sweep_parse_failed")
    return Envelope(ok=parsed.get("ok") is True, error=parsed.get("error"), result=parsed)
