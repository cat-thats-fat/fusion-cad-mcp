"""Exercise generated primitive scripts with measured geometry and rollback doubles."""

import math
import re
from types import SimpleNamespace as NS

import pytest

from fusion_cad_mcp.tools import primitives as p
from tests._assembly_fakes import Collection, NoScriptAdapter, ScriptAdapter, root_with


def _point(x, y, z):
    return NS(x=x, y=y, z=z, asArray=lambda: (x, y, z))


def _parameter(expression):
    return NS(isValid=True, expression=expression)


class _Entity:
    isValid = True
    healthState = 0

    def __init__(self, collection, **attributes):
        self.name = "unnamed"
        self.collection = collection
        self.restore = lambda: None
        vars(self).update(attributes)
        collection.add(self)

    def deleteMe(self):
        self.restore()
        self.collection.items.remove(self)
        self.isValid = False
        return True


class _Sketch(_Entity):
    def __init__(self, collection, plane):
        super().__init__(collection)
        self.plane = plane
        self.originPoint = NS(geometry=_point(0, 0, 0))
        self.profiles = Collection()
        self.dimensions = []
        self.geometricConstraints = Collection()
        self.geometricConstraints.addHorizontal = lambda *_: _Entity(self.geometricConstraints)
        self.geometricConstraints.addVertical = lambda *_: _Entity(self.geometricConstraints)
        self.geometricConstraints.addCoincident = self._coincident
        self.sketchDimensions = NS(addDistanceDimension=self._dimension, addDiameterDimension=self._dimension)
        self.sketchCurves = NS(
            sketchLines=NS(addCenterPointRectangle=self._center_rectangle, addTwoPointRectangle=self._corner_rectangle),
            sketchCircles=NS(addByCenterRadius=self._circle),
        )
        self.radii = []
        self.circles = []
        self.projected_point = None

    @property
    def isFullyConstrained(self):
        return len(self.dimensions) >= 2 if not self.radii else len(self.dimensions) == len(self.radii)

    def _dimension(self, *_):
        parameter = _parameter("")
        self.dimensions.append(parameter)
        return NS(parameter=parameter)

    def _coincident(self, point, target):
        return _Entity(self.geometricConstraints, point=point, target=target)

    def _rectangle(self, lower, upper):
        x0, y0, x1, y1 = lower.geometry.x, lower.geometry.y, upper.x, upper.y
        corners = [lower, NS(geometry=_point(x1, y0, 0)), NS(geometry=upper), NS(geometry=_point(x0, y1, 0))]
        self.profiles.add(NS(sketch=self, area=(x1 - x0) * (y1 - y0), bounds=(x0, y0, x1, y1)))
        return Collection(NS(startSketchPoint=a, endSketchPoint=b)
                          for a, b in zip(corners, corners[1:] + corners[:1], strict=True))

    def _center_rectangle(self, center, corner):
        lower = NS(geometry=_point(2 * center.x - corner.x, 2 * center.y - corner.y, 0))
        return self._rectangle(lower, corner)

    def _corner_rectangle(self, lower, upper):
        return self._rectangle(lower, upper)

    def _circle(self, center, radius):
        self.radii.append(radius)
        center_point = center if hasattr(center, "geometry") else NS(geometry=center)
        x, y, _ = center_point.geometry.asArray()
        bounds = (x - self.radii[0], y - self.radii[0], x + self.radii[0], y + self.radii[0])
        area = math.pi * (self.radii[0] ** 2 - radius ** 2) if len(self.radii) == 2 else math.pi * radius ** 2
        self.profiles.add(NS(sketch=self, profileLoops=NS(count=len(self.radii)), area=area, bounds=bounds))
        circle = NS(centerSketchPoint=center_point)
        self.circles.append(circle)
        return circle

    def project2(self, entities, linked):
        entity = entities[0]
        x, y, z = entity.geometry.asArray()
        uv = {0: (-z, y), 1: (x, -z), 2: (x, y)}[self.plane.axis]
        self.projected_point = _ProjectedPoint(geometry=_point(*uv, 0), isLinked=linked)
        return [self.projected_point]

    def sketchToModelSpace(self, position):
        u, v, n = position.asArray()
        offset = self.plane.coordinate + self.plane.sign * n
        return _point(*{0: (offset, v, -u), 1: (u, offset, -v), 2: (u, v, offset)}[self.plane.axis])


class _ProjectedPoint(NS):
    @property
    def referencedEntity(self):
        raise RuntimeError("InternalValidationError(res)")


class _Rig:
    def __init__(self, monkeypatch):
        self.driver_values = {"outer_w": 60}
        self.calls = []
        self.no_op = False
        self.lose_dimension = False
        self.warning = False
        self.root = root_with(name="Root", isValid=True, features=Collection(), sketches=Collection(),
                              constructionPlanes=Collection(), constructionPoints=Collection())
        self.root.occurrences = self.root.allOccurrences
        self.root.xYConstructionPlane = self._plane(2, 1, 0)
        self.root.xZConstructionPlane = self._plane(1, 1, 0)
        self.root.yZConstructionPlane = self._plane(0, 1, 0)
        self.root.sketches.add = self._add_sketch
        self.root.constructionPlanes.createInput = self._offset_input
        self.root.constructionPlanes.add = self._add_plane
        self.root.constructionPoints.createInput = self._point_input
        self.root.constructionPoints.add = self._add_point
        self.root.features.extrudeFeatures = NS(createInput=self._extrude_input, add=self._add_extrude)
        self.root.features.combineFeatures = NS(createInput=lambda target, tools: NS(target=target, tools=tools),
                                                add=self._add_combine)
        manager = NS(defaultLengthUnits="mm", isValidExpression=lambda *_: True,
                     evaluateExpression=self._evaluate, standardizeExpression=lambda s, _: "".join(s.split()))
        design = NS(designType="parametric", rootComponent=self.root, unitsManager=manager,
                    userParameters=Collection(), computeAll=lambda: True)
        cast = NS(cast=lambda obj: obj)
        self.adapter = ScriptAdapter(
            monkeypatch, design,
            core={"Point3D": NS(create=_point), "ValueInput": NS(createByString=lambda s: s),
                  "ObjectCollection": NS(create=Collection)},
            fusion={"DesignTypes": NS(ParametricDesignType="parametric"),
                    "FeatureOperations": NS(NewBodyFeatureOperation="new", CutFeatureOperation="cut",
                                            JoinFeatureOperation="join"),
                    "ExtentDirections": NS(PositiveExtentDirection="positive", NegativeExtentDirection="negative"),
                    "DimensionOrientations": NS(HorizontalDimensionOrientation="h", VerticalDimensionOrientation="v"),
                    "ConstructionPlaneOffsetDefinition": cast,
                    "DistanceExtentDefinition": NS(create=lambda s: s, cast=cast.cast)},
        )

    def _evaluate(self, expression, _units="mm"):
        expression = expression.replace("mm", "")
        for name, value in self.driver_values.items():
            expression = expression.replace(name, str(value))
        assert re.fullmatch(r"[0-9+*/(). -]+", expression)
        return eval(expression, {"__builtins__": {}}, {}) / 10

    def _plane(self, axis, sign, coordinate, expression="0 mm"):
        normal = [0, 0, 0]
        normal[axis] = sign
        return NS(axis=axis, sign=sign, coordinate=coordinate, geometry=NS(normal=_point(*normal)),
                  definition=NS(offset=_parameter(expression)))

    def _offset_input(self):
        inputs = NS()

        def set_offset(base, expression):
            inputs.result = vars(self._plane(base.axis, base.sign, base.sign * self._evaluate(expression), expression))
            return True

        inputs.setByOffset = set_offset
        return inputs

    def _add_plane(self, inputs):
        plane = self._new_entity(self.root.constructionPlanes, **inputs.result)
        self.calls.append(("offset", plane.axis, plane.definition.offset.expression))
        return plane

    def _point_input(self):
        inputs = NS()

        def set_point(*planes):
            inputs.geometry = _point(*(plane.coordinate for plane in planes))
            return True

        inputs.setByThreePlanes = set_point
        return inputs

    def _add_point(self, inputs):
        return self._new_entity(self.root.constructionPoints, geometry=inputs.geometry)

    def _add_sketch(self, plane):
        # A sketch is always registered in the native owner collection.
        sketch = _Sketch.__new__(_Sketch)
        add = self.root.sketches.add
        self.root.sketches.add = self.root.sketches.items.append
        try:
            _Sketch.__init__(sketch, self.root.sketches, plane)
        finally:
            self.root.sketches.add = add
        return sketch

    def _new_entity(self, collection, **attrs):
        add = collection.add
        collection.add = collection.items.append
        try:
            return _Entity(collection, **attrs)
        finally:
            collection.add = add

    def _extrude_input(self, profile, operation):
        inputs = NS(profile=profile, operation=operation, participantBodies=[])

        def extent(expression, direction):
            inputs.expression = expression
            inputs.direction = direction
            return True

        inputs.setOneSideExtent = extent
        return inputs

    def body(self, name, volume, bounds):
        body = _Entity(self.root.bRepBodies, parentComponent=self.root, nativeObject=None,
                       isSolid=True, volume=volume,
                       boundingBox=NS(minPoint=_point(*bounds[:3]), maxPoint=_point(*bounds[3:])))
        body.name = name
        return body

    def _add_extrude(self, inputs):
        profile = inputs.profile
        length = self._evaluate(inputs.expression)
        amount = profile.area * length
        self.calls.append((inputs.operation, [body.name for body in inputs.participantBodies], inputs.direction))
        if inputs.operation == "new":
            x0, y0, x1, y1 = profile.bounds
            z = profile.sketch.plane.coordinate
            body = self.body("new", amount, (x0, y0, z, x1, y1, z + length))
            restore = body.deleteMe
        else:
            body = inputs.participantBodies[0]
            prior = body.volume
            if not self.no_op:
                body.volume -= amount
            def restore():
                body.volume = prior
        negative = inputs.direction == "negative"
        signed_expression = f"-( {inputs.expression} )" if negative else inputs.expression
        extent = _parameter(signed_expression)
        extent.name = "d_extent"
        extent.value = -length if negative else length
        if self.lose_dimension:
            extent.expression = "1 mm"
        return _Entity(self.root.features, bodies=Collection([body]), restore=restore,
                       extentOne=NS(distance=extent), healthState=1 if self.warning else 0,
                       errorOrWarningMessage="Injected warning")

    def _add_combine(self, inputs):
        tool = inputs.tools.item(0)
        self.calls.append((inputs.operation, inputs.target.name, tool.name))
        prior = inputs.target.volume
        inputs.target.volume += tool.volume
        tool.deleteMe()

        def restore():
            inputs.target.volume = prior
            self.root.bRepBodies.add(tool)
            tool.isValid = True

        return _Entity(self.root.features, restore=restore)


def _enclosure(rig):
    return p.create_parametric_enclosure(rig.adapter, "Base", "outer_w", "40 mm", "25 mm", "2 mm", "3 mm")


def test_enclosure_uses_expressions_and_only_cuts_its_new_body(monkeypatch):
    rig = _Rig(monkeypatch)
    sibling = rig.body("PCB", 10.0, (0, 0, 0, 1, 2, 5))

    env = _enclosure(rig)

    assert env.ok, env.result
    assert env.result["body"] == "Base"
    assert env.result["bounds_mm"] == {"min": [-30, -20, 0], "max": [30, 20, 25]}
    assert env.result["volume_mm3"] == pytest.approx(60 * 40 * 25 - 56 * 36 * 22)
    assert env.result["features"] == ["Base - Outside", "Base - Cavity"]
    assert env.result["datums"] == ["Base - Floor Datum"]
    assert rig.calls == [("new", [], "positive"), ("offset", 2, "3 mm"), ("cut", ["Base"], "positive")]
    assert sibling.volume == 10.0
    assert "outer_w" in rig.root.sketches.item(1).dimensions[0].expression
    calls = rig.calls.copy()
    assert _enclosure(rig).error == "primitive_name_exists"
    assert rig.calls == calls


@pytest.mark.parametrize(("mode", "error"), [
    ("no_op", "geometry_mismatch"), ("lose_dimension", "dimension_expression_mismatch"),
    ("warning", "created_entity_unhealthy"),
])
def test_enclosure_failure_removes_only_tracked_entities(monkeypatch, mode, error):
    rig = _Rig(monkeypatch)
    sibling = rig.body("PCB", 10.0, (0, 0, 0, 1, 2, 5))
    setattr(rig, mode, True)

    env = _enclosure(rig)

    assert env.error == error, env.result
    assert env.result["rollback"]["status"] == "verified"
    assert rig.root.bRepBodies.items == [sibling]
    assert rig.root.features.count == rig.root.sketches.count == rig.root.constructionPlanes.count == 0


def test_enclosure_refuses_impossible_wall_before_creating_entities(monkeypatch):
    rig = _Rig(monkeypatch)
    env = p.create_parametric_enclosure(rig.adapter, "Base", "10 mm", "10 mm", "20 mm", "5 mm", "3 mm")

    assert env.error == "invalid_cavity"
    assert rig.calls == []


def test_boss_uses_ring_profile_signed_anchor_and_explicit_combine(monkeypatch):
    rig = _Rig(monkeypatch)
    body = rig.body("Base", 10.0, (-2, -2, 0, 2, 2, 3))
    sibling = rig.body("PCB", 2.0, (-1, -1, 0, 1, 1, 1))
    env = p.create_mounting_boss(rig.adapter, "Standoff", "-5 mm", "-7 mm", "3 mm",
                                "6 mm", "2 mm", "4 mm", target_body="Base")

    assert env.ok, env.result
    assert env.result["body"] == "Base"
    assert env.result["added_volume_mm3"] == pytest.approx(math.pi * (3**2 - 1**2) * 4)
    assert rig.root.constructionPoints.item(0).geometry.asArray() == (-0.5, -0.7, 0.3)
    assert rig.calls[-1] == ("join", "Base", "Standoff")
    assert rig.root.bRepBodies.items == [body, sibling]
    assert sibling.volume == 2.0

    sketch = rig.root.sketches.item(0)
    centers = [circle.centerSketchPoint for circle in sketch.circles]
    assert len(centers) == 2 and centers[0] is not centers[1]
    assert sketch.geometricConstraints.count == 2
    for center, constraint in zip(centers, sketch.geometricConstraints.items, strict=True):
        assert center is not sketch.projected_point
        assert center.geometry.asArray() == sketch.projected_point.geometry.asArray()
        assert constraint.point is center
        assert constraint.target is sketch.projected_point


def test_refused_boss_coincidence_rolls_back_before_extruding(monkeypatch):
    rig = _Rig(monkeypatch)
    monkeypatch.setattr(_Sketch, "_coincident", lambda *_: None)

    env = p.create_mounting_boss(rig.adapter, "Standoff", "-5 mm", "-7 mm", "3 mm",
                                "6 mm", "2 mm", "4 mm")

    assert env.error == "constraint_failed"
    assert env.result["rollback"]["status"] == "verified"
    assert all(call[0] == "offset" for call in rig.calls)
    assert rig.root.bRepBodies.count == rig.root.sketches.count == 0


@pytest.mark.parametrize(("plane", "direction", "corner", "normal"), [
    ("xy", "positive", [4, 8, 5], [0, 0, 1]),
    ("xz", "negative", [4, 3, 0], [0, -1, 0]),
    ("yz", "positive", [-4, 8, -3], [1, 0, 0]),
])
def test_cutout_scopes_the_cut_and_reports_the_component_frame(monkeypatch, plane, direction, corner, normal):
    rig = _Rig(monkeypatch)
    rig.driver_values["port_depth"] = 2
    target = rig.body("Lid", 15.0, (-2, -2, 0, 2, 2, 1))
    sibling = rig.body("PCB", 10.0, (-2, -2, 0, 2, 2, 1))
    env = p.create_cutout(rig.adapter, "Port", "Lid", "-4 mm", "3 mm", "5 mm",
                          "8 mm", "5 mm", "port_depth", plane=plane, direction=direction)

    assert env.ok, env.result
    assert env.result["removed_volume_mm3"] == pytest.approx(80)
    assert env.result["profile_corners_component_mm"][0] == pytest.approx([-4, 3, 5])
    assert env.result["profile_corners_component_mm"][3] == pytest.approx(corner)
    assert env.result["cut_direction_component"] == normal
    assert rig.calls[-1] == ("cut", ["Lid"], direction)
    extent = rig.root.features.item(0).extentOne.distance
    assert extent.expression == ("-( port_depth )" if direction == "negative" else "port_depth")
    assert extent.value == pytest.approx(-0.2 if direction == "negative" else 0.2)
    assert target.volume == pytest.approx(14.92)
    assert sibling.volume == 10.0


def test_negative_cut_rejects_equivalent_literal_that_loses_its_depth_driver(monkeypatch):
    rig = _Rig(monkeypatch)
    rig.driver_values["port_depth"] = 2
    target = rig.body("Lid", 15.0, (-2, -2, 0, 2, 2, 1))
    extrudes = rig.root.features.extrudeFeatures
    original_add = extrudes.add

    def freeze_depth(inputs):
        feature = original_add(inputs)
        feature.extentOne.distance.expression = "-2 mm"
        return feature

    monkeypatch.setattr(extrudes, "add", freeze_depth)
    env = p.create_cutout(rig.adapter, "Port", "Lid", "-4 mm", "3 mm", "5 mm",
                          "8 mm", "5 mm", "port_depth", plane="xz", direction="negative")

    assert env.error == "dimension_expression_mismatch"
    assert env.result["requested_expression"] == "-(port_depth)"
    assert env.result["actual_expression"] == "-2 mm"
    assert env.result["parameter_name"] == "d_extent"
    assert env.result["rollback"]["status"] == "verified"
    assert target.volume == 15


def test_noop_cutout_rolls_back_linked_anchor_and_empty_feature(monkeypatch):
    rig = _Rig(monkeypatch)
    target = rig.body("Lid", 15.0, (-2, -2, 0, 2, 2, 1))
    rig.no_op = True
    env = p.create_cutout(rig.adapter, "Port", "Lid", "0 mm", "0 mm", "5 mm", "8 mm", "5 mm", "2 mm")

    assert env.error == "cutout_volume_mismatch", env.result
    assert env.result["rollback"]["status"] == "verified"
    assert rig.root.bRepBodies.items == [target]
    assert rig.root.sketches.count == rig.root.constructionPlanes.count == rig.root.constructionPoints.count == 0


def test_unlinked_projection_refuses_to_author_a_static_boss(monkeypatch):
    rig = _Rig(monkeypatch)
    project = _Sketch.project2
    monkeypatch.setattr(_Sketch, "project2", lambda sketch, entities, _: project(sketch, entities, False))

    env = p.create_mounting_boss(rig.adapter, "Standoff", "-5 mm", "-7 mm", "3 mm",
                                "6 mm", "2 mm", "4 mm")

    assert env.error == "anchor_projection_failed"
    assert env.result["linked"] is False
    assert env.result["rollback"]["status"] == "verified"
    assert rig.root.bRepBodies.count == rig.root.sketches.count == rig.root.constructionPlanes.count == 0


def test_cutout_refuses_an_unknown_plane_without_submitting_a_script():
    env = p.create_cutout(NoScriptAdapter(), "Port", "Lid", "0 mm", "0 mm", "5 mm", "8 mm", "5 mm",
                          "2 mm", plane="front")

    assert env.error == "invalid_input"


@pytest.mark.parametrize("kind", ["boss", "cutout"])
@pytest.mark.parametrize("change", ["unlink", "move"])
def test_anchor_change_during_recompute_refuses_and_rolls_back(monkeypatch, kind, change):
    rig = _Rig(monkeypatch)
    project = _Sketch.project2
    projected = []
    def capture(sketch, entities, linked):
        result = project(sketch, entities, linked)
        projected.extend(result)
        return result
    monkeypatch.setattr(_Sketch, "project2", capture)
    import adsk.core
    design = adsk.core.Application.get().activeProduct
    def compute():
        if projected:
            if change == "unlink":
                projected[0].isLinked = False
            else:
                old = projected[0].geometry
                projected[0].geometry = _point(old.x + 1, old.y, old.z)
        return True
    design.computeAll = compute
    if kind == "boss":
        env = p.create_mounting_boss(rig.adapter, "Standoff", "-5 mm", "-7 mm", "3 mm",
                                    "6 mm", "2 mm", "4 mm")
    else:
        rig.body("Lid", 15.0, (-2, -2, 0, 2, 2, 1))
        env = p.create_cutout(rig.adapter, "Port", "Lid", "0 mm", "0 mm", "5 mm", "8 mm", "5 mm", "2 mm")
    assert env.error == "anchor_projection_failed"
    assert env.result["linked"] is (change != "unlink")
    assert env.result["rollback"]["status"] == "verified"
