"""Create a scoped, editable rectangle sketch using primitive sketch machinery."""

from __future__ import annotations

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..execution import execute_typed_script
from ..mutation import MUTATION_SCRIPT
from ..resolver import RESOLVER_SCRIPT
from .primitives import _PRIMITIVE_SCRIPT

_RECTANGLE_SCRIPT = """
def _run_rectangle(design, args):
    root = design.rootComponent
    owner, occurrence, error = _select_component(root, args['component'], for_edit=True)
    if error: return error
    tx = None
    try:
        _require(design.designType == adsk.fusion.DesignTypes.ParametricDesignType,
                 'parametric_design_required', 'Use a parametric Fusion design')
        intents = getattr(adsk.fusion, 'DesignIntentTypes', None)
        if intents is not None:
            _require(design.designIntent != intents.AssemblyDesignIntentType,
                     'modeling_intent_required', 'Use a Hybrid or Part design')
        current = occurrence
        while current is not None:
            _require(not current.isReferencedComponent, 'linked_component_edit_unsupported',
                     'The selected component is linked from another design')
            current = current.assemblyContext
        _require(owner == root or owner.parentDesign == design, 'linked_component_edit_unsupported',
                 'The selected component is owned by another design')
        name = args['name']
        for category in ('features', 'sketches', 'bRepBodies', 'constructionPlanes', 'constructionPoints'):
            for entity in _mutation_items(getattr(owner, category)):
                _require(entity.name != name and not entity.name.startswith(name + ' - '),
                         'primitive_name_exists', 'The operation name is already used in this component', name=name)
        manager = getattr(design, 'fusionUnitsManager', None) or design.unitsManager
        values = _lengths(manager, args, ('x', 'y', 'z', 'width', 'height'))
        _require(values['width'] > 0 and values['height'] > 0, 'invalid_dimensions',
                 'Width and height must be positive')
        tx = _Mutation(design, owner)
        sketch, anchor, anchor_bindings, datums = _anchored_sketch(tx, args, values)
        profile, bindings, corners = _rectangle(sketch, anchor, values['width'], values['height'],
                                                 args['width'], args['height'], args['centered'])
        verification = tx.verify()
        _verify_anchor(sketch, anchor, values)
        _verify_bindings(manager, anchor_bindings + bindings)
        _require(sketch.isFullyConstrained and sketch.profiles.count == 1,
                 'rectangle_not_constrained', 'Rectangle must be fully constrained with one closed profile')
        prefix = occurrence.fullPathName + ' / ' if occurrence is not None else ''
        return {'ok': True, 'component': occurrence.fullPathName if occurrence is not None else 'root',
                'sketch': prefix + sketch.name, 'datums': [prefix + item for item in datums],
                'plane': args['plane'], 'centered': args['centered'], 'profile_count': sketch.profiles.count,
                'fully_constrained': sketch.isFullyConstrained,
                'profile_corners_component_mm': [[v * 10 for v in sketch.sketchToModelSpace(p).asArray()] for p in corners],
                'verification': verification}
    except Exception as exc:
        return _mutation_failure(tx, exc, 'parametric_rectangle_failed', name=args['name'])


def run(_ctx):
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    if design is None:
        print(json.dumps({'ok': False, 'error': 'no_active_design'})); return
    print(json.dumps(_run_rectangle(design, ARGS)))
"""


def build_create_parametric_rectangle(
    name: str, width: str, height: str, x: str = "0 mm", y: str = "0 mm", z: str = "0 mm",
    plane: str = "xy", centered: bool = False, component: str | None = None,
) -> str:
    arguments = dict(name=name, width=width, height=height, x=x, y=y, z=z, plane=plane,
                     centered=centered, component=component)
    for key in ("name", "width", "height", "x", "y", "z"):
        value = arguments[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
    if name != name.strip() or any(c in name for c in "\r\n\0"):
        raise ValueError("name must be a single-line label")
    if plane not in ("xy", "xz", "yz"):
        raise ValueError("plane must be xy, xz or yz")
    if not isinstance(centered, bool):
        raise ValueError("centered must be a boolean")
    if component is not None and (not isinstance(component, str) or not component.strip()):
        raise ValueError("component must be a non-empty selector or None")
    return ("import adsk.core, adsk.fusion, json\n" + RESOLVER_SCRIPT + MUTATION_SCRIPT
            + _PRIMITIVE_SCRIPT + "\nARGS = " + repr(arguments) + "\n" + _RECTANGLE_SCRIPT)


def create_parametric_rectangle(
    adapter: FusionAdapter, name: str, width: str, height: str, x: str = "0 mm", y: str = "0 mm",
    z: str = "0 mm", plane: str = "xy", centered: bool = False, component: str | None = None,
) -> Envelope:
    try:
        script = build_create_parametric_rectangle(name, width, height, x, y, z, plane, centered, component)
    except (TypeError, ValueError) as exc:
        return Envelope(ok=False, error="invalid_input", message=str(exc))
    env = execute_typed_script(adapter, script)
    if not env.ok:
        return env
    result = parse_status_json(env)
    if result is None:
        return script_parse_failure(env, "parametric_rectangle_parse_failed")
    return Envelope(state=env.state, ok=result["ok"], error=result.get("error"), result=result, message=env.message)
