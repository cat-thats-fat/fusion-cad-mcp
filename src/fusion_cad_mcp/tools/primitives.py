"""Named, editable Fusion primitives for enclosure authoring."""

from __future__ import annotations

from ..adapter import FusionAdapter
from ..envelope import Envelope, parse_status_json, script_parse_failure
from ..mutation import MUTATION_SCRIPT
from ..resolver import RESOLVER_SCRIPT

_PRIMITIVE_SCRIPT = """
P = adsk.core.Point3D.create
VI = adsk.core.ValueInput.createByString
FO = adsk.fusion.FeatureOperations
DO = adsk.fusion.DimensionOrientations
ED = adsk.fusion.ExtentDirections
AXES = {'xy': 2, 'xz': 1, 'yz': 0}


def _require(condition, code, message, **details):
    if not condition:
        raise _MutationError(code, message, **details)


def _near(actual, expected):
    return math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-8)


def _lengths(manager, args, fields):
    units = manager.defaultLengthUnits
    values = {}
    for field in fields:
        expression = args[field]
        _require(manager.isValidExpression(expression, units), 'invalid_expression',
                 'Fusion could not evaluate a length expression', field=field, units=units)
        value = manager.evaluateExpression(expression, units)
        _require(math.isfinite(value), 'invalid_expression',
                 'A length must resolve to a finite value', field=field)
        values[field] = value
    return values


def _offset(tx, base, expression, name):
    planes = tx.owner.constructionPlanes
    inputs = planes.createInput()
    _require(inputs.setByOffset(base, VI(expression)), 'offset_plane_failed',
             'Fusion refused the offset plane', entity_name=name)
    plane = tx.track(planes.add(inputs))
    plane.name = name
    definition = adsk.fusion.ConstructionPlaneOffsetDefinition.cast(plane.definition)
    _require(definition is not None, 'offset_not_parametric', 'Fusion did not retain an editable plane offset')
    return plane, (definition.offset, expression)


def _offset_coordinate(tx, base, axis, expression, name):
    normal = base.geometry.normal.asArray()[axis]
    _require(_near(abs(normal), 1), 'invalid_plane_frame', 'Origin plane has an unexpected normal')
    signed_expression = expression if normal > 0 else '-(' + expression + ')'
    return _offset(tx, base, signed_expression, name)


def _anchored_sketch(tx, args, values):
    owner, name = tx.owner, args['name']
    coordinates = ('x', 'y', 'z')
    origin_planes = (owner.yZConstructionPlane, owner.xZConstructionPlane, owner.xYConstructionPlane)
    offsets = [
        _offset_coordinate(tx, base, axis, args[field], name + ' - ' + field.upper() + ' Datum')
        for axis, (base, field) in enumerate(zip(origin_planes, coordinates))
    ]
    planes, bindings = zip(*offsets)
    points = owner.constructionPoints
    inputs = points.createInput()
    _require(inputs.setByThreePlanes(*planes), 'anchor_failed', 'Fusion refused the plane intersection')
    anchor = tx.track(points.add(inputs))
    anchor.name = name + ' - Anchor'
    expected = [values[field] for field in coordinates]
    _require(all(_near(a, b) for a, b in zip(anchor.geometry.asArray(), expected)),
             'anchor_mismatch', 'Construction point did not reach the requested component coordinates')
    sketch = tx.track(owner.sketches.add(planes[AXES[args['plane']]]))
    sketch.name = name + ' - Profile'
    _require(hasattr(sketch, 'project2'), 'linked_projection_unavailable',
             'This Fusion build lacks linked Sketch.project2')
    projected = sketch.project2([anchor], True)
    _require(projected and len(projected) == 1, 'anchor_projection_failed',
             'Expected one linked point projected into the sketch',
             projected_count=len(projected) if projected is not None else 0,
             projected_types=[getattr(item, 'objectType', None) for item in projected or ()])
    point = projected[0]
    # Fusion can raise while resolving referencedEntity for this valid linked
    # projection. Read its link state and its actual position instead.
    _verify_anchor(sketch, point, values)
    return sketch, point, list(bindings), [item.name for item in (*planes, anchor)]


def _verify_anchor(sketch, point, values):
    expected = [values[field] for field in ('x', 'y', 'z')]
    projected_position = sketch.sketchToModelSpace(point.geometry).asArray()
    _require(point.isLinked and all(_near(a, b) for a, b in zip(projected_position, expected)),
             'anchor_projection_failed', 'The linked point did not reach the requested anchor',
             linked=point.isLinked)


def _constraint(constraint):
    _require(constraint is not None, 'constraint_failed', 'Fusion refused a sketch constraint')


def _bind(dimension, expression):
    _require(dimension is not None and dimension.parameter is not None, 'dimension_failed',
             'Fusion did not create a driving dimension')
    # A bare parameter name can be interpreted as its current numeric value
    # by addDistanceDimension. Preserve the expression as a length equation.
    equation = '(' + expression + ') + 0 mm'
    dimension.parameter.expression = equation
    return dimension.parameter, equation


def _verify_bindings(manager, bindings):
    units = manager.defaultLengthUnits
    for parameter, requested in bindings:
        _require(parameter is not None and parameter.isValid, 'dimension_expression_mismatch',
                 'Fusion did not retain a native feature parameter')
        expected = manager.standardizeExpression(requested, units)
        actual = manager.standardizeExpression(parameter.expression, units)
        _require(expected and actual == expected, 'dimension_expression_mismatch',
                 'A native dimension or extent lost its requested expression')


def _rectangle(sketch, anchor, width, height, width_expr, height_expr, centered):
    constraints = sketch.geometricConstraints
    before = constraints.count
    x, y = anchor.geometry.x, anchor.geometry.y
    lines = sketch.sketchCurves.sketchLines
    if centered:
        rect = lines.addCenterPointRectangle(P(x, y, 0), P(x + width / 2, y + height / 2, 0))
    else:
        rect = lines.addTwoPointRectangle(anchor, P(x + width, y + height, 0))
    _require(rect is not None and rect.count == 4, 'rectangle_failed', 'Fusion did not create four rectangle edges')
    # Constrain the new lines explicitly. Preserve constraints that belong to
    # the linked reference point and remove only inferred constraints from the rectangle call.
    for i in range(constraints.count - 1, before - 1, -1):
        _require(constraints.item(i).deleteMe(), 'constraint_failed', 'Could not clear inferred rectangle constraint')
    horizontal, vertical = [], []
    for line in _mutation_items(rect):
        start, end = line.startSketchPoint.geometry, line.endSketchPoint.geometry
        if _near(start.y, end.y) and not _near(start.x, end.x):
            horizontal.append(line)
            _constraint(constraints.addHorizontal(line))
        elif _near(start.x, end.x) and not _near(start.y, end.y):
            vertical.append(line)
            _constraint(constraints.addVertical(line))
    _require(len(horizontal) == len(vertical) == 2, 'rectangle_failed', 'Expected orthogonal rectangle edges')
    lower = min(horizontal, key=lambda line: line.startSketchPoint.geometry.y)
    left = min(vertical, key=lambda line: line.startSketchPoint.geometry.x)
    corner = min((lower.startSketchPoint, lower.endSketchPoint), key=lambda point: point.geometry.x)
    if not centered and corner != anchor:
        _constraint(constraints.addCoincident(corner, anchor))
    dimensions = sketch.sketchDimensions
    h, v = DO.HorizontalDimensionOrientation, DO.VerticalDimensionOrientation
    bindings = [
        _bind(dimensions.addDistanceDimension(lower.startSketchPoint, lower.endSketchPoint, h,
                                             P(x + width / 2, y - height, 0)), width_expr),
        _bind(dimensions.addDistanceDimension(left.startSketchPoint, left.endSketchPoint, v,
                                             P(x - width, y + height / 2, 0)), height_expr),
    ]
    if centered:
        bindings.extend([
            _bind(dimensions.addDistanceDimension(anchor, corner, h, P(x - width / 3, y - height, 0)),
                  '(' + width_expr + ') / 2'),
            _bind(dimensions.addDistanceDimension(anchor, corner, v, P(x - width, y - height / 3, 0)),
                  '(' + height_expr + ') / 2'),
        ])
    _require(sketch.isFullyConstrained and sketch.profiles.count == 1, 'rectangle_not_constrained',
             'Rectangle must be fully constrained with one closed profile', entity_name=sketch.name)
    upper = max(horizontal, key=lambda line: line.startSketchPoint.geometry.y)
    corners = [
        point.geometry for line in (lower, upper)
        for point in sorted((line.startSketchPoint, line.endSketchPoint), key=lambda p: p.geometry.x)
    ]
    return sketch.profiles.item(0), bindings, corners


def _circle(sketch, anchor, diameter, expression):
    center = anchor.geometry
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(anchor, diameter / 2)
    _require(circle is not None, 'circle_failed', 'Fusion refused a boss circle')
    return _bind(sketch.sketchDimensions.addDiameterDimension(circle,
                 P(center.x + diameter, center.y + diameter, 0)), expression)


def _extrude(tx, profile, expression, name, *, target=None, direction='positive'):
    features = tx.owner.features.extrudeFeatures
    operation = FO.NewBodyFeatureOperation if target is None else FO.CutFeatureOperation
    inputs = features.createInput(profile, operation)
    _require(inputs is not None, 'extrude_input_failed', 'Fusion refused the extrusion input')
    extent = adsk.fusion.DistanceExtentDefinition.create(VI(expression))
    _require(inputs.setOneSideExtent(extent, ED.PositiveExtentDirection if direction == 'positive'
                                    else ED.NegativeExtentDirection),
             'extrude_extent_failed', 'Fusion refused the extrusion extent')
    if target is not None:
        inputs.participantBodies = [target]
    feature = tx.track(features.add(inputs))
    feature.name = name
    native_extent = adsk.fusion.DistanceExtentDefinition.cast(feature.extentOne)
    _require(native_extent is not None, 'extrude_not_parametric', 'Fusion did not retain a distance extent')
    return feature, (native_extent.distance, expression)


def _positive_z(sketch):
    origin = sketch.sketchToModelSpace(P(0, 0, 0))
    normal_point = sketch.sketchToModelSpace(P(0, 0, 1))
    return 'positive' if normal_point.z > origin.z else 'negative'


def _cutout_frame(sketch, corners, args, values):
    model = [sketch.sketchToModelSpace(point).asArray() for point in corners]
    start = model[0]
    direction = 1 if args['direction'] == 'positive' else -1
    end = sketch.sketchToModelSpace(P(corners[0].x, corners[0].y, direction)).asArray()
    vector = [b - a for a, b in zip(start, end)]
    axis = AXES[args['plane']]
    _require(all(_near(a, values[key]) for a, key in zip(start, ('x', 'y', 'z')))
             and all(_near(point[axis], start[axis]) for point in model)
             and _near(math.dist(model[0], model[1]), values['width'])
             and _near(math.dist(model[0], model[2]), values['height'])
             and _near(abs(vector[axis]), 1), 'cutout_frame_mismatch',
             'The profile corners or extrusion axis did not match the requested component frame')
    return {'profile_corners_component_mm': [[v * 10 for v in point] for point in model],
            'cut_direction_component': vector}


def _one_body(feature, name):
    _require(feature.bodies.count == 1, 'unexpected_body_count', 'Expected one body from the extrusion')
    body = feature.bodies.item(0)
    _require(body.isValid and body.isSolid, 'invalid_solid', 'Fusion did not create a valid solid')
    body.name = name
    return body


def _body_snapshot(owner, except_body=None):
    return [(body, _mutation_body_values(body)) for body in _mutation_items(owner.bRepBodies)
            if body != except_body]


def _verify_siblings(owner, expected, except_body=None):
    siblings = _body_snapshot(owner, except_body)
    _require(len(expected) == len(siblings) and all(
        before == now and _mutation_same(old, new)
        for (before, old), (now, new) in zip(expected, siblings)
    ), 'other_bodies_changed', 'The operation changed bodies outside its named scope')


def _local_body(owner, name):
    found = [body for body in _mutation_items(owner.bRepBodies) if body.name == name]
    _require(len(found) == 1 and found[0].isValid and found[0].isSolid,
             'target_body_lost', 'Expected one valid solid with the target body name', body=name)
    return found[0]


def _check_solid(body, expected_volume, expected_bounds):
    actual = _solid_values(body)
    _require(_near(actual[0], expected_volume) and all(_near(a, b) for a, b in zip(actual[1:], expected_bounds)),
             'geometry_mismatch', 'Solid volume or bounds did not match the requested dimensions',
             actual_volume_mm3=actual[0] * 1000, expected_volume_mm3=expected_volume * 1000)


def _solid_values(body):
    box = getattr(body, 'preciseBoundingBox', None) or body.boundingBox
    return (body.volume, *box.minPoint.asArray(), *box.maxPoint.asArray())


def _describe(body, features, sketches):
    volume, *bounds = _solid_values(body)
    return {'body_name': body.name, 'volume_mm3': volume * 1000,
            'bounds_mm': {'min': [v * 10 for v in bounds[:3]], 'max': [v * 10 for v in bounds[3:]]},
            'feature_names': [feature.name for feature in features],
            'sketch_names': [sketch.name for sketch in sketches]}


def _enclosure(tx, manager, args, values, target):
    owner, name = tx.owner, args['name']
    w, d, h, wall, floor = (values[k] for k in ('width', 'depth', 'height', 'wall', 'floor'))
    siblings = _body_snapshot(owner)
    outer = tx.track(owner.sketches.add(owner.xYConstructionPlane))
    outer.name = name + ' - Outside Profile'
    profile, outer_dims, _ = _rectangle(outer, outer.originPoint, w, d, args['width'], args['depth'], True)
    solid, height_binding = _extrude(tx, profile, args['height'], name + ' - Outside',
                                     direction=_positive_z(outer))
    body = _one_body(solid, name)

    floor_plane, floor_binding = _offset_coordinate(tx, owner.xYConstructionPlane, 2, args['floor'],
                                                    name + ' - Floor Datum')
    cavity = tx.track(owner.sketches.add(floor_plane))
    cavity.name = name + ' - Cavity Profile'
    inner_width = '(' + args['width'] + ') - 2 * (' + args['wall'] + ')'
    inner_depth = '(' + args['depth'] + ') - 2 * (' + args['wall'] + ')'
    profile, inner_dims, _ = _rectangle(cavity, cavity.originPoint, w - 2 * wall, d - 2 * wall,
                                        inner_width, inner_depth, True)
    cut, cut_binding = _extrude(tx, profile, '(' + args['height'] + ') - (' + args['floor'] + ')',
                                name + ' - Cavity', target=body, direction=_positive_z(cavity))
    verification = tx.verify()
    _verify_bindings(manager, outer_dims + inner_dims + [height_binding, floor_binding, cut_binding])
    body = _local_body(owner, name)
    _verify_siblings(owner, siblings, body)
    expected = w * d * h - (w - 2 * wall) * (d - 2 * wall) * (h - floor)
    _check_solid(body, expected, (-w / 2, -d / 2, 0, w / 2, d / 2, h))
    return {**_describe(body, (solid, cut), (outer, cavity)), 'verification': verification,
            'datum_names': [floor_plane.name]}


def _boss(tx, manager, args, values, target):
    owner, name = tx.owner, args['name']
    outer, inner, height = (values[k] for k in ('outer_diameter', 'inner_diameter', 'height'))
    x, y, z = (values[k] for k in ('x', 'y', 'z'))
    radius = outer / 2
    expected = math.pi * (outer ** 2 - inner ** 2) * height / 4
    siblings = _body_snapshot(owner, target)
    before = target.volume if target is not None else 0
    target_name = target.name if target is not None else name
    sketch, anchor, bindings, datums = _anchored_sketch(tx, args, values)
    bindings.append(_circle(sketch, anchor, outer, args['outer_diameter']))
    if inner > 0:
        bindings.append(_circle(sketch, anchor, inner, args['inner_diameter']))
    loops = 2 if inner > 0 else 1
    profiles = [p for p in _mutation_items(sketch.profiles) if p.profileLoops.count == loops]
    _require(sketch.isFullyConstrained and len(profiles) == 1, 'boss_profile_invalid',
             'Expected one fully constrained boss profile of the requested shape')
    extrusion, height_binding = _extrude(tx, profiles[0], args['height'], name + ' - Boss',
                                         direction=_positive_z(sketch))
    body = _one_body(extrusion, name)
    _check_solid(body, expected, (x - radius, y - radius, z, x + radius, y + radius, z + height))
    features = [extrusion]
    if target is not None:
        tools = adsk.core.ObjectCollection.create()
        tools.add(body)
        combines = owner.features.combineFeatures
        inputs = combines.createInput(target, tools)
        _require(inputs is not None, 'boss_join_failed', 'Fusion refused the combine input')
        inputs.operation = FO.JoinFeatureOperation
        inputs.isKeepToolBodies = False
        joined = tx.track(combines.add(inputs))
        joined.name = name + ' - Join'
        features.append(joined)
    verification = tx.verify()
    _verify_anchor(sketch, anchor, values)
    _verify_bindings(manager, bindings + [height_binding])
    body = _local_body(owner, target_name)
    _verify_siblings(owner, siblings, body)
    added = body.volume - before
    _require(added > 1e-8 and (added < expected or _near(added, expected)), 'boss_volume_mismatch',
             'The boss did not add the expected range of material to its body')
    return {**_describe(body, features, (sketch,)), 'verification': verification,
            'added_volume_mm3': added * 1000, 'joined': target is not None, 'datum_names': datums}


def _cutout(tx, manager, args, values, target):
    owner, name = tx.owner, args['name']
    width, height, depth = (values[k] for k in ('width', 'height', 'depth'))
    target_name = target.name
    before = target.volume
    siblings = _body_snapshot(owner, target)
    sketch, anchor, anchor_bindings, datums = _anchored_sketch(tx, args, values)
    profile, bindings, corners = _rectangle(sketch, anchor, width, height, args['width'], args['height'], False)
    frame = _cutout_frame(sketch, corners, args, values)
    cut, depth_binding = _extrude(tx, profile, args['depth'], name + ' - Cut',
                                 target=target, direction=args['direction'])
    verification = tx.verify()
    _verify_anchor(sketch, anchor, values)
    _verify_bindings(manager, anchor_bindings + bindings + [depth_binding])
    body = _local_body(owner, target_name)
    _verify_siblings(owner, siblings, body)
    removed, maximum = before - body.volume, width * height * depth
    _require(removed > 1e-8 and (removed < maximum or _near(removed, maximum)), 'cutout_volume_mismatch',
             'The cut did not remove the expected range of material from the named body')
    return {**_describe(body, (cut,), (sketch,)), **frame, 'verification': verification,
            'removed_volume_mm3': removed * 1000, 'datum_names': datums,
            'plane': args['plane'], 'direction': args['direction']}


def _run_primitive(design, args):
    root = design.rootComponent
    owner, occurrence, error = _select_component(root, args['component'], for_edit=True)
    if error:
        return error
    tx = None
    try:
        _require(design.designType == adsk.fusion.DesignTypes.ParametricDesignType,
                 'parametric_design_required', 'Use a parametric Fusion design')
        intents = getattr(adsk.fusion, 'DesignIntentTypes', None)
        if intents is not None:
            _require(design.designIntent != intents.AssemblyDesignIntentType,
                     'modeling_intent_required', 'Use a Hybrid or Part design to author features')
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
        operation = args['operation']
        dimensions = {'enclosure': ('width', 'depth', 'height', 'wall', 'floor'),
                      'boss': ('x', 'y', 'z', 'outer_diameter', 'inner_diameter', 'height'),
                      'cutout': ('x', 'y', 'z', 'width', 'height', 'depth')}
        values = _lengths(manager, args, dimensions[operation])
        positive = [field for field in values if field not in ('x', 'y', 'z', 'inner_diameter')]
        _require(all(values[field] > 0 for field in positive), 'invalid_dimensions',
                 'Sizes and thicknesses must be positive')
        if operation == 'enclosure':
            _require(2 * values['wall'] < min(values['width'], values['depth'])
                     and values['floor'] < values['height'], 'invalid_cavity',
                     'The wall and floor must leave room for the cavity')
        if operation == 'boss':
            _require(0 <= values['inner_diameter'] < values['outer_diameter'], 'invalid_boss_diameter',
                     'Inner diameter must be zero or smaller than the outer diameter')
        target = None
        if args.get('body') is not None:
            selected, error = _select_body(root, args['body'])
            if error:
                return error
            target = selected.nativeObject or selected
            _require(target.parentComponent == owner and target.isSolid, 'participant_wrong_component',
                     'The target solid must belong to the selected component')
        tx = _Mutation(design, owner)
        handler = {'enclosure': _enclosure, 'boss': _boss, 'cutout': _cutout}[operation]
        result = handler(tx, manager, args, values, target)
        prefix = occurrence.fullPathName + ' / ' if occurrence is not None else ''
        return {'ok': True, **result, 'component': occurrence.fullPathName if occurrence is not None else 'root',
                'body': prefix + result['body_name'], 'features': [prefix + v for v in result['feature_names']],
                'sketches': [prefix + v for v in result['sketch_names']],
                'datums': [prefix + v for v in result['datum_names']]}
    except Exception as exc:
        return _mutation_failure(tx, exc, 'primitive_failed', name=args['name'])
"""


def _run(adapter: FusionAdapter, operation: str, **arguments: str | None) -> Envelope:
    for key, value in arguments.items():
        if value is None and key == "body" and operation == "boss":
            continue
        if not isinstance(value, str) or not value.strip():
            return Envelope(ok=False, error="invalid_input", message=f"{key} must be a non-empty string")
    name = arguments["name"]
    if name is None or name != name.strip() or any(c in name for c in "\r\n\0"):
        return Envelope(ok=False, error="invalid_input", message="name must be a single-line label")
    if "plane" in arguments and arguments["plane"] not in {"xy", "xz", "yz"}:
        return Envelope(ok=False, error="invalid_input", message="plane must be xy, xz or yz")
    if "direction" in arguments and arguments["direction"] not in {"positive", "negative"}:
        return Envelope(ok=False, error="invalid_input", message="direction must be positive or negative")
    script = f"""
import adsk.core, adsk.fusion
import json
{RESOLVER_SCRIPT}
{MUTATION_SCRIPT}
{_PRIMITIVE_SCRIPT}

def run(_ctx):
    design = adsk.fusion.Design.cast(adsk.core.Application.get().activeProduct)
    if design is None:
        print(json.dumps({{'ok': False, 'error': 'no_active_design'}}))
        return
    print(json.dumps(_run_primitive(design, {dict(operation=operation, **arguments)!r})))
"""
    env = adapter.execute_script(script)
    if not env.ok:
        return env
    result = parse_status_json(env)
    if result is None:
        return script_parse_failure(env, operation + "_parse_failed")
    return Envelope(ok=result["ok"], error=result.get("error"), result=result, message=env.message)


def create_parametric_enclosure(
    adapter: FusionAdapter, name: str, width: str, depth: str, height: str, wall: str,
    floor: str, component: str = "root",
) -> Envelope:
    """Create a centered open-top enclosure with its floor at native Z=0."""
    return _run(adapter, "enclosure", name=name, width=width, depth=depth, height=height,
                wall=wall, floor=floor, component=component)


def create_mounting_boss(
    adapter: FusionAdapter, name: str, x: str, y: str, z: str, outer_diameter: str,
    inner_diameter: str, height: str, component: str = "root", target_body: str | None = None,
) -> Envelope:
    """Create a boss at native XYZ, with optional Join into a named solid."""
    return _run(adapter, "boss", name=name, x=x, y=y, z=z, outer_diameter=outer_diameter,
                inner_diameter=inner_diameter, height=height, component=component,
                body=target_body, plane="xy")


def create_cutout(
    adapter: FusionAdapter, name: str, body: str, x: str, y: str, z: str,
    width: str, height: str, depth: str, plane: str = "xy", direction: str = "positive",
    component: str = "root",
) -> Envelope:
    """Cut a rectangle from a named solid at native XYZ.

    The anchor is the lower-left corner in the Fusion sketch frame. Width and
    height run along positive sketch X and Y. Direction is the sketch normal
    or its reverse. The result reports the corresponding component frame.
    """
    return _run(adapter, "cutout", name=name, body=body, x=x, y=y, z=z, width=width,
                height=height, depth=depth, plane=plane, direction=direction, component=component)
