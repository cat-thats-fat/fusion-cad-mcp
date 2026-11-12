import contextlib
import io
import sys
import types
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools.validation import build_validate_design, validate_design
from tests._assembly_fakes import Collection, RigidMatrix, point, root_with, transformable_point


class Adapter:
    def __init__(self, monkeypatch, bodies, *, volumes=(), distance=1, fail=False):
        self.root = root_with(bodies=bodies)
        self.results = Collection([NS(interferenceBody=None if v is None else NS(volume=v / 1000)) for v in volumes])
        self.design = NS(rootComponent=self.root, createInterferenceInput=Mock(return_value=NS()),
                         analyzeInterference=Mock(side_effect=RuntimeError("analysis unavailable") if fail else None, return_value=self.results))
        self.design.findEntityByToken = lambda token: [body for body in bodies if getattr(body, "entityToken", None) == token]
        self.doc = NS(creationId="doc", dataFile=None)
        self.app = NS(activeProduct=self.design, activeDocument=self.doc, documents=Collection([self.doc]),
                      measureManager=NS(measureMinimumDistance=Mock(return_value=NS(value=distance / 10, positionOne=point(1, 2, 3), positionTwo=point(1, 2, 3 + distance / 10)))))
        modules = {n: types.ModuleType(n) for n in ("adsk", "adsk.core", "adsk.fusion")}
        modules["adsk"].core = modules["adsk.core"]
        modules["adsk"].fusion = modules["adsk.fusion"]
        modules["adsk.core"].Application = NS(get=lambda: self.app)
        modules["adsk.core"].ObjectCollection = NS(create=Collection)
        modules["adsk.core"].Point3D = NS(create=transformable_point)
        self.app.pointTolerance = 1e-7
        modules["adsk.fusion"].Design = NS(cast=lambda d: d)
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)
        self.read_only = None

    def execute_script(self, script, *, read_only=False):
        self.read_only = read_only
        namespace = {}
        output = io.StringIO()
        self.script_output = output
        with contextlib.redirect_stdout(output):
            exec(script, namespace)
            namespace["run"](None)
        return Envelope(ok=True, message=output.getvalue())


def body(name, *, low=(0, 0, 0), high=(1, 2, 3), solid=True, token=None):
    return NS(name=name, objectType="adsk::fusion::BRepBody", entityToken=token, isValid=True,
              isSolid=solid, boundingBox=NS(minPoint=point(*low), maxPoint=point(*high)))


def test_clearance_checks_containment_even_if_measured_distance_positive(monkeypatch):
    adapter = Adapter(monkeypatch, [body("outer"), body("inner")], volumes=[123], distance=5)
    env = validate_design(adapter, [{"type": "clearance", "body_a": "outer", "body_b": "inner", "min_mm": 2}])
    assert adapter.read_only and env.ok and env.result["passed"] is False
    check = env.result["checks"][0]
    assert check["status"] == "violation" and check["evidence"]["interference_count"] == 1
    assert check["evidence"]["minimum_distance_mm"] == 5
    assert check["evidence"]["closest_point_a_mm"] == [10, 20, 30]


def test_interference_threshold_bounds_and_aggregate(monkeypatch):
    adapter = Adapter(monkeypatch, [body("a"), body("b")], volumes=[2, 3])
    checks = [{"type": "interference", "body_a": "a", "body_b": "b", "max_overlap_mm3": 5},
              {"type": "interference", "body_a": "a", "body_b": "b"},
              {"type": "bounds", "body": "a", "min_mm": [0, 0, 0], "max_mm": [10, 20, 30]}]
    result = validate_design(adapter, checks).result
    assert result["passed"] is False
    assert [x["status"] for x in result["checks"]] == ["pass", "violation", "pass"]
    assert result["checks"][0]["evidence"]["overlap_mm3"] == 5
    assert "not cavity containment" in result["checks"][2]["evidence"]["method"]


def test_unavailable_is_not_pass_and_does_not_stop_other_checks(monkeypatch):
    adapter = Adapter(monkeypatch, [body("a"), body("b")], fail=True)
    checks = [{"type": "clearance", "body_a": "a", "body_b": "b", "min_mm": 0},
              {"type": "bounds", "body": "a", "min_mm": [0, 0, 0], "max_mm": [1, 1, 1]}]
    result = validate_design(adapter, checks).result
    assert not result["passed"]
    assert [x["status"] for x in result["checks"]] == ["unavailable", "violation"]


def test_ambiguity_and_same_body_do_not_measure(monkeypatch):
    adapter = Adapter(monkeypatch, [body("a"), body("a"), body("b")])
    result = validate_design(adapter, [{"type": "clearance", "body_a": "a", "body_b": "b", "min_mm": 0},
                                       {"type": "clearance", "body_a": "b", "body_b": "b", "min_mm": 0}]).result
    assert [x["status"] for x in result["checks"]] == ["unavailable", "unavailable"]
    assert result["checks"][0]["reason"]["error"] == "body_ambiguous"
    adapter.design.analyzeInterference.assert_not_called()


def test_scoped_handle_and_wrong_document(monkeypatch):
    target = body("a", token="xyz")
    adapter = Adapter(monkeypatch, [target])

    def lookup(token):
        assert sys.stdout is adapter.script_output
        return [target] if token == "xyz" else []

    adapter.design.findEntityByToken = lookup
    suffix = "|doc=" + b"doc".hex()
    check = {"type": "bounds", "body": f"body:a{suffix}:xyz", "min_mm": [0, 0, 0], "max_mm": [10, 20, 30]}
    assert validate_design(adapter, [check]).result["passed"]
    check["body"] = f"body:a|doc={b'other'.hex()}:xyz"
    result = validate_design(adapter, [check]).result
    assert not result["passed"] and result["checks"][0]["reason"]["detail"]["reason"] == "document_scope_mismatch"
    check["body"] = f"body:a{suffix}:missing"
    result = validate_design(adapter, [check]).result
    assert not result["passed"] and result["checks"][0]["reason"]["detail"]["reason"] == "not_found_in_active_doc"


@pytest.mark.parametrize("checks", [[], [{}], [{"type": "bounds", "body": "x", "min_mm": [0, 0, 0], "max_mm": [1, 1, float("nan")]}],
                                    [{"type": "interference", "body_a": "a", "body_b": "b", "max_overlap_mm3": -1}],
                                    [{"type": "bounds", "body": "a", "min_mm": [2, 0, 0], "max_mm": [1, 1, 1]}],
                                    [{"type": "interference", "body_a": "a", "body_b": "b", "extra": 1}],
                                    [{"type": "interference", "body_a": "a", "body_b": "b"}] * 51])
def test_input_rejected_before_dispatch(checks):
    with pytest.raises(ValueError):
        build_validate_design(checks)


def test_overlap_is_violation_even_if_distance_unavailable(monkeypatch):
    adapter = Adapter(monkeypatch, [body("a"), body("b")], volumes=[2])
    adapter.app.measureManager.measureMinimumDistance.side_effect = RuntimeError("distance unavailable")
    result = validate_design(adapter, [{"type": "clearance", "body_a": "a", "body_b": "b", "min_mm": 2}]).result
    assert result["checks"][0]["status"] == "violation"
    assert result["checks"][0]["evidence"]["minimum_distance_mm"] is None


def test_nested_occurrence_body_handle_preserves_context(monkeypatch):
    native = body("part", token="nested")
    proxy = body("part", low=(3, 0, 0), high=(4, 2, 3), token="nested")
    occ = NS(fullPathName="Parent:1+Part:1", bRepBodies=Collection([proxy]), component=NS(name="Part"))
    proxy.assemblyContext = occ
    native.createForAssemblyContext = lambda requested: proxy if requested == occ else None
    adapter = Adapter(monkeypatch, [])
    adapter.root.allOccurrences = Collection([occ])
    adapter.design.findEntityByToken = lambda token: [native]
    handle = "body:part|occ=Parent:1+Part:1|doc=646f63:nested"
    check = {"type": "bounds", "body": handle, "min_mm": [30, 0, 0], "max_mm": [40, 20, 30]}
    result = validate_design(adapter, [check]).result
    assert result["passed"] and result["checks"][0]["evidence"]["body_bounds_min_mm"] == [30, 0, 0]


@pytest.mark.parametrize("check", [
    {"type": "clearance", "body_a": "a", "body_b": "b", "min_mm": 10**1000},
    {"type": "interference", "body_a": "a", "body_b": "b", "max_overlap_mm3": 10**1000},
    {"type": "bounds", "body": "a", "min_mm": [-(10**1000), 0, 0], "max_mm": [1, 1, 1]},
])
def test_oversized_integer_returns_invalid_input_without_dispatch(check):
    from tests._assembly_fakes import NoScriptAdapter
    result = validate_design(NoScriptAdapter(), [check])
    assert result.error == "invalid_input"


def face_pair_check(*, a="left", b="right", occurrence=None, **bounds):
    suffix = (f"|occ={occurrence}" if occurrence else "") + "|doc=646f63"
    return {
        "type": "face_distance", "face_a": f"face:box/face[0]{suffix}:{a}",
        "face_b": f"face:box/face[1]{suffix}:{b}", "tolerance_mm": 0,
        **bounds,
    }


def _face_pair_adapter(monkeypatch, *, distance=10):
    adapter = Adapter(monkeypatch, [], distance=distance)
    owner = NS(parentComponent=adapter.root)
    faces = {
        token: NS(objectType="adsk::fusion::BRepFace", body=owner, assemblyContext=None, token=token,
                  isPointOnFace=lambda p, tolerance: True)
        for token in ("left", "right")
    }
    adapter.design.findEntityByToken = lambda token: [faces[token]] if token in faces else []
    return adapter, faces


def test_same_body_face_distance_uses_both_tolerance_boundaries(monkeypatch):
    adapter, faces = _face_pair_adapter(monkeypatch)
    checks = [face_pair_check(min_mm=10.5, tolerance_mm=0.5),
              face_pair_check(max_mm=9.5, tolerance_mm=0.5),
              face_pair_check(min_mm=10.5, max_mm=11, tolerance_mm=0.499),
              face_pair_check(max_mm=9.5, tolerance_mm=0.499)]

    result = validate_design(adapter, checks).result

    assert not result["passed"]
    assert [row["status"] for row in result["checks"]] == ["pass", "pass", "violation", "violation"]
    evidence = result["checks"][0]["evidence"]
    assert {key: evidence[key] for key in ("minimum_distance_mm", "closest_point_a_mm", "closest_point_b_mm", "frame", "units", "method")} == {
        "minimum_distance_mm": 10, "closest_point_a_mm": [10, 20, 30],
        "closest_point_b_mm": [10, 20, 40], "frame": "root", "units": "mm",
        "method": "Fusion MeasureManager.measureMinimumDistance",
    }
    assert evidence["scalar_valid"] and evidence["witness_consistency"]["status"] == "consistent"
    assert all(call.args == (faces["left"], faces["right"])
               for call in adapter.app.measureManager.measureMinimumDistance.call_args_list)
    adapter.design.analyzeInterference.assert_not_called()


def test_face_distance_allows_zero_for_distinct_faces_at_shared_edge(monkeypatch):
    adapter, _ = _face_pair_adapter(monkeypatch, distance=0)

    result = validate_design(adapter, [face_pair_check(min_mm=0, max_mm=0)]).result

    assert result["passed"]
    evidence = result["checks"][0]["evidence"]
    assert evidence["minimum_distance_mm"] == 0
    assert evidence["closest_point_a_mm"] == evidence["closest_point_b_mm"]


def test_face_distance_uses_nested_root_proxies(monkeypatch):
    adapter, native_faces = _face_pair_adapter(monkeypatch)
    occurrence = NS(fullPathName="Parent:1+Box:2", transform2=RigidMatrix())
    child_component = NS(name="Box")
    owner = NS(parentComponent=child_component)
    adapter.root.allOccurrences = Collection([occurrence])
    proxies = []
    for i, face in enumerate(native_faces.values()):
        proxy = NS(body=owner, assemblyContext=occurrence, location=point(4 + i, 5, 6), nativeObject=face)
        face.body = owner
        face.createForAssemblyContext = Mock(return_value=proxy)
        proxies.append(proxy)
    measure = adapter.app.measureManager.measureMinimumDistance
    measure.side_effect = lambda a, b: NS(value=b.location.x - a.location.x,
                                           positionOne=a.location, positionTwo=b.location)

    result = validate_design(adapter, [face_pair_check(occurrence=occurrence.fullPathName, min_mm=10)]).result

    assert result["passed"]
    measure.assert_called_once_with(*proxies)
    for face in native_faces.values():
        face.createForAssemblyContext.assert_called_once_with(occurrence)
    assert result["checks"][0]["evidence"]["closest_point_b_mm"] == [50, 50, 60]

    unscoped_occurrence = validate_design(adapter, [face_pair_check(min_mm=0)]).result
    assert unscoped_occurrence["checks"][0]["reason"]["error"] == "face_root_context_unavailable"
    assert measure.call_count == 1


def test_face_distance_unavailable_for_alias_stale_and_ambiguous_handles(monkeypatch):
    adapter, faces = _face_pair_adapter(monkeypatch)
    candidates = {"left": [faces["left"]], "alias": [faces["left"]], "ambiguous": list(faces.values())}
    adapter.design.findEntityByToken = lambda token: candidates.get(token, [])
    other_doc = face_pair_check(b="alias", min_mm=0)
    other_doc["face_a"] = other_doc["face_a"].replace("646f63", "616263")
    checks = [face_pair_check(b="alias", min_mm=0), face_pair_check(b="gone", min_mm=0),
              face_pair_check(b="ambiguous", min_mm=0), other_doc]

    result = validate_design(adapter, checks).result

    assert not result["passed"]
    assert all(row["status"] == "unavailable" for row in result["checks"])
    assert result["checks"][0]["reason"] == "same_face"
    assert [row["reason"]["detail"]["reason"] for row in result["checks"][1:]] == [
        "not_found_in_active_doc", "ambiguous_token", "document_scope_mismatch",
    ]
    adapter.app.measureManager.measureMinimumDistance.assert_not_called()


@pytest.mark.parametrize("distance", [None, NS(value=float("nan")), NS(value=1, positionOne=None)])
def test_face_distance_unavailable_on_invalid_native_measurement(monkeypatch, distance):
    adapter, _ = _face_pair_adapter(monkeypatch)
    adapter.app.measureManager.measureMinimumDistance.return_value = distance

    env = validate_design(adapter, [face_pair_check(min_mm=0)])

    assert env.ok and not env.result["passed"]
    row = env.result["checks"][0]
    assert row["status"] == "unavailable"
    assert row["reason"]["error"] == "face_distance_unavailable"


@pytest.mark.parametrize("update", [
    {"face_a": "box"}, {"face_a": "face:unscoped:left"}, {"face_b": "body:box|doc=646f63:right"},
    {"min_mm": 11, "max_mm": 10}, {"min_mm": None}, {"tolerance_mm": -1}, {"tolerance_mm": False},
])
def test_face_distance_rejects_invalid_scope_or_numeric_requirements(update):
    with pytest.raises(ValueError):
        build_validate_design([{**face_pair_check(min_mm=0), **update}])


def test_face_distance_requires_explicit_bound_and_tolerance():
    with pytest.raises(ValueError, match="requires min_mm or max_mm"):
        build_validate_design([face_pair_check()])
    missing_tolerance = face_pair_check(min_mm=0)
    del missing_tolerance["tolerance_mm"]
    with pytest.raises(ValueError, match="tolerance_mm"):
        build_validate_design([missing_tolerance])


@pytest.mark.parametrize("overlap,status", [(False, "unavailable"), (True, "violation")])
def test_clearance_preserves_contradictory_measurement_and_independent_overlap(monkeypatch, overlap, status):
    adapter = Adapter(monkeypatch, [body("a"), body("b")], volumes=[1] if overlap else [])
    adapter.app.measureManager.measureMinimumDistance.return_value = NS(
        value=0.0777739, positionOne=point(), positionTwo=point())
    result = validate_design(adapter, [{"type": "clearance", "body_a": "a", "body_b": "b", "min_mm": 0}]).result
    row = result["checks"][0]
    assert not result["passed"] and row["status"] == status
    assert row["evidence"]["minimum_distance_mm"] == pytest.approx(0.777739)
    assert row["evidence"]["witness_consistency"]["status"] == "inconsistent"


def test_face_acceptance_threshold_cannot_mask_witness_inconsistency(monkeypatch):
    adapter, _ = _face_pair_adapter(monkeypatch)
    adapter.app.measureManager.measureMinimumDistance.return_value = NS(
        value=0.0777739, positionOne=point(), positionTwo=point())
    result = validate_design(adapter, [face_pair_check(max_mm=100, tolerance_mm=1000)]).result
    row = result["checks"][0]
    assert row["status"] == "unavailable" and not result["passed"]
    assert row["evidence"]["minimum_distance_mm"] == pytest.approx(0.777739)
    assert row["evidence"]["witness_consistency"]["numerical_tolerance_mm"] < 0.00001


def surface_check(**update):
    return {"type": "surface_distance", "name": "corner", "body": "body:box|doc=646f63:box",
            "point_mm": [15, 25, 35], "frame": "root", "max_mm": 9,
            "tolerance_mm": 0, "max_faces": 6, **update}


def _box_surface_adapter(monkeypatch, *, occurrence_path=None):
    """Six finite rectangles on a 10x20x30 mm box; point projection is analytic."""
    import math

    target = body("box", token="box")
    adapter = Adapter(monkeypatch, [target])
    target.parentComponent = adapter.root
    context = NS(fullPathName=occurrence_path,
                 transform2=RigidMatrix(((0, -1, 0), (1, 0, 0), (0, 0, 1)), (4, 5, 6))) if occurrence_path else None
    target.assemblyContext = context
    if context:
        adapter.root.allOccurrences = Collection([context])
    # Rotation by +90 degrees around Z plus translation, in centimeters.
    def to_local(p):
        return [p.y - 5, 4 - p.x, p.z - 6] if context else [p.x, p.y, p.z]

    def to_root(p):
        return [4 - p[1], 5 + p[0], 6 + p[2]] if context else p

    target.faces = Collection([
        NS(axis=axis, coord=coord, assemblyContext=context, isValid=True)
        for axis, extent in enumerate([1, 2, 3]) for coord in (0, extent)
    ])
    for face in target.faces.items:
        def member(p, tolerance, axis=face.axis, coord=face.coord):
            local = [p.x, p.y, p.z]
            return abs(local[axis] - coord) <= tolerance and all(
                -tolerance <= x <= extent + tolerance for x, extent in zip(local, [1, 2, 3], strict=True))
        if context:
            face.nativeObject = NS(isPointOnFace=Mock(side_effect=member))
            face.isPointOnFace = Mock(side_effect=AssertionError('proxy membership must not be used'))
        else:
            face.isPointOnFace = member
    def measure(p, face):
        local = to_local(p)
        nearest = [min(max(x, 0), extent) for x, extent in zip(local, [1, 2, 3], strict=True)]
        nearest[face.axis] = face.coord
        return NS(value=math.dist(local, nearest), positionOne=p, positionTwo=point(*to_root(nearest)))

    adapter.app.measureManager.measureMinimumDistance.side_effect = measure
    sys.modules["adsk.core"].Point3D = NS(create=transformable_point)
    return adapter, target


def test_surface_probe_uses_finite_face_geometry_and_named_points(monkeypatch):
    import math

    adapter, _ = _box_surface_adapter(monkeypatch)
    checks = [surface_check(max_mm=math.sqrt(75), tolerance_mm=1e-6),
              surface_check(name="inside", point_mm=[5, 5, 5], max_mm=4.5, tolerance_mm=0.500001),
              surface_check(name="far", max_mm=8.6)]
    result = validate_design(adapter, checks).result
    assert [row["status"] for row in result["checks"]] == ["pass", "pass", "violation"]
    assert result["checks"][2]["evidence"]["proof"] == "native_face_minima_above_maximum"
    nearest = result["checks"][0]["evidence"]["minimum_scalar_candidate"]
    assert nearest["minimum_distance_mm"] == pytest.approx(math.sqrt(75))
    assert nearest["closest_point_b_mm"] == [10, 20, 30]
    # Interior point is 5mm from the surface, never zero from a solid-volume interpretation.
    assert result["checks"][1]["evidence"]["minimum_scalar_candidate"]["minimum_distance_mm"] == 5
    assert result["checks"][0]["evidence"]["faces_measured"] == 6
    assert "not Hausdorff" in result["checks"][0]["evidence"]["scope"]
    adapter.design.analyzeInterference.assert_not_called()


def test_surface_probe_transformed_nested_occurrence_and_scope_guard(monkeypatch):
    adapter, target = _box_surface_adapter(monkeypatch, occurrence_path="Parent:1+Box:2")
    target.parentComponent = NS(name="Box")
    scoped = "body:box|occ=Parent:1+Box:2|doc=646f63:box"
    result = validate_design(adapter, [surface_check(body=scoped, point_mm=[15, 65, 95])]).result
    assert result["passed"]
    assert result["checks"][0]["evidence"]["minimum_scalar_candidate"]["closest_point_b_mm"] == [20, 60, 90]
    membership = result["checks"][0]["evidence"]["satisfying_witness"]["surface_membership"]
    assert membership["frame_conversion"] == "inverse_occurrence_transform2"
    assert membership["input_point_mm"] == [20, 60, 90]
    assert membership["membership_point_mm"] == [10, 20, 30]
    assert membership["tolerance_source"] == "explicit Application.pointTolerance"
    assert target.assemblyContext.transform2.apply([1, 2, 3]) == [2, 6, 9]
    for face in target.faces.items:
        face.isPointOnFace.assert_not_called()
        assert face.nativeObject.isPointOnFace.call_args.args[1] == 1e-7
    bad = validate_design(adapter, [surface_check(point_mm=[15, 65, 95])]).result
    assert bad["checks"][0]["reason"] == "body_root_context_unavailable"
    assert adapter.app.measureManager.measureMinimumDistance.call_count == 6


@pytest.mark.parametrize("mode", ["raise", "missing", "contradictory", "wrong_anchor"])
def test_surface_probe_preserves_all_face_failures_and_available_scalar(monkeypatch, mode):
    adapter, target = _box_surface_adapter(monkeypatch)
    original = adapter.app.measureManager.measureMinimumDistance.side_effect
    def measure(p, face):
        if face is target.faces.item(0):
            if mode == "raise":
                raise RuntimeError("kernel failure retained")
            if mode == "missing":
                return NS(value=0.01, positionOne=p)
            if mode == "contradictory":
                return NS(value=0.01, positionOne=p, positionTwo=p)
            return NS(value=0.01, positionOne=point(), positionTwo=point(0.01))
        return original(p, face)
    adapter.app.measureManager.measureMinimumDistance.side_effect = measure
    result = validate_design(adapter, [surface_check(max_mm=999, tolerance_mm=999)]).result
    row = result["checks"][0]
    assert result["passed"] and row["status"] == "pass"
    assert row["evidence"]["proof"] == "validated_witness_upper_bound"
    assert row["evidence"]["satisfying_witness"]["surface_membership"]["status"] == "on_face"
    assert row["evidence"]["faces_attempted"] == 6
    assert row["evidence"]["faces_measured"] == (5 if mode == "raise" else 6)
    assert row["evidence"]["faces_valid"] == 5
    failure, = row["evidence"]["face_failures"]
    assert failure["face_index"] == 0
    if mode == "raise":
        assert failure["detail"] == "kernel failure retained"
    else:
        assert failure["evidence"]["minimum_distance_mm"] == 0.1
        assert row["evidence"]["minimum_scalar_candidate"]["minimum_distance_mm"] == 0.1


def test_surface_probe_face_budget_prevents_native_measurements(monkeypatch):
    adapter, _ = _box_surface_adapter(monkeypatch)
    result = validate_design(adapter, [surface_check(max_faces=5)]).result
    assert result["checks"][0]["reason"] == "face_budget_exceeded"
    assert result["checks"][0]["evidence"]["face_count"] == 6
    adapter.app.measureManager.measureMinimumDistance.assert_not_called()


@pytest.mark.parametrize("mode,reason", [("stale", "not_found_in_active_doc"),
    ("ambiguous", "ambiguous_token"), ("foreign", "document_scope_mismatch")])
def test_surface_probe_refuses_stale_ambiguous_or_foreign_body(monkeypatch, mode, reason):
    adapter, target = _box_surface_adapter(monkeypatch)
    check = surface_check()
    if mode == "stale":
        adapter.design.findEntityByToken = lambda token: []
    elif mode == "ambiguous":
        adapter.design.findEntityByToken = lambda token: [target, target]
    else:
        check["body"] = check["body"].replace("646f63", "666f726569676e")
    result = validate_design(adapter, [check]).result
    assert not result["passed"]
    assert result["checks"][0]["reason"]["detail"]["reason"] == reason
    adapter.app.measureManager.measureMinimumDistance.assert_not_called()


def test_surface_probe_recreates_native_faces_for_nested_root_context(monkeypatch):
    adapter, target = _box_surface_adapter(monkeypatch, occurrence_path="Parent:1+Box:2")
    for face in target.faces.items:
        proxy = NS(**vars(face))
        face.assemblyContext = None
        face.createForAssemblyContext = Mock(return_value=proxy)
    result = validate_design(adapter, [surface_check(
        body="body:box|occ=Parent:1+Box:2|doc=646f63:box", point_mm=[15, 65, 95])]).result
    assert result["passed"]
    for face in target.faces.items:
        face.createForAssemblyContext.assert_called_once_with(target.assemblyContext)


@pytest.mark.parametrize("update", [{"body": "box"}, {"body": "body:box:box"},
    {"frame": "local"}, {"point_mm": [0, 0, True]}, {"point_mm": [0, 0, float("nan")]},
    {"max_faces": True}, {"max_faces": 0}, {"max_faces": 513}, {"max_mm": -1},
    {"tolerance_mm": None}, {"name": " "}, {"name": "x" * 101}, {"extra": 1}])
def test_surface_probe_rejects_unbounded_ambiguous_or_malformed_input(update):
    with pytest.raises(ValueError):
        build_validate_design([surface_check(**update)])


def test_surface_probe_requires_unique_names_and_bounds_total_work():
    with pytest.raises(ValueError, match="unique"):
        build_validate_design([surface_check(), surface_check()])
    with pytest.raises(ValueError, match="4096"):
        build_validate_design([surface_check(name=str(i), max_faces=512) for i in range(9)])


def test_maximum_probe_work_with_long_failures_survives_real_receipt(monkeypatch, tmp_path):
    import json

    from fusion_cad_mcp import receipts

    class ReceiptAdapter(Adapter):
        def execute_typed_script(self, script, **kwargs):
            return receipts.run_typed(self.execute_script, script, "offline-measurement", 1,
                                      journal_dir=tmp_path, **kwargs)

    target = body("box", token="box")
    adapter = ReceiptAdapter(monkeypatch, [target])
    target.parentComponent = adapter.root
    target.faces = Collection([NS(assemblyContext=None, isValid=True) for _ in range(80)])
    dense = body("dense", token="dense")
    dense.parentComponent = adapter.root
    dense.faces = Collection(target.faces.items + [NS(assemblyContext=None, isValid=True) for _ in range(96)])
    adapter.design.findEntityByToken = lambda token: [{"box": target, "dense": dense}[token]]
    sys.modules["adsk.core"].Point3D = NS(create=point)

    def native(p, face):
        if face is target.faces.item(0):
            return NS(value=0.0777739, positionOne=p, positionTwo=p)
        raise RuntimeError("\x00" * 50000 + "é" * 50000)

    adapter.app.measureManager.measureMinimumDistance.side_effect = native
    checks = [surface_check(name=str(i), max_faces=80) for i in range(49)]
    checks.append(surface_check(name="49", max_faces=176, body="body:dense|doc=646f63:dense"))
    env = validate_design(adapter, checks)
    assert env.ok and env.result["passed"] is False
    assert adapter.app.measureManager.measureMinimumDistance.call_count == 4096
    assert len(env.result["checks"]) == 50
    total_failures = 0
    for row in env.result["checks"]:
        evidence = row["evidence"]
        assert row["status"] == "unavailable"
        expected_faces = 176 if evidence["name"] == "49" else 80
        assert evidence["faces_attempted"] == expected_faces and evidence["faces_measured"] == 1
        assert evidence["faces_valid"] == 0 and evidence["face_failure_count"] == expected_faces
        assert evidence["minimum_scalar_candidate"]["minimum_distance_mm"] == pytest.approx(0.777739)
        assert evidence["diagnostics_truncated"]
        assert evidence["face_failures_omitted"] + len(evidence["face_failures"]) == expected_faces
        total_failures += len(evidence["face_failures"])
    assert 0 < total_failures < 150
    status = receipts.get_status(env.state["operation_id"], journal_dir=tmp_path).result
    assert status["status"] == "completed" and len(status["declared_result"]["checks"]) == 50
    assert len(json.dumps(status["declared_result"], ensure_ascii=True).encode()) < 196608


def test_large_probe_input_echo_does_not_discard_candidate_or_status(monkeypatch, tmp_path):
    import json

    from fusion_cad_mcp import receipts

    adapter, _ = _box_surface_adapter(monkeypatch)
    adapter.execute_typed_script = lambda script, **kwargs: receipts.run_typed(
        adapter.execute_script, script, "offline-large-echo", 1, journal_dir=tmp_path, **kwargs)
    # Long human-readable paths are not resolver identity. Their output echo may
    # be omitted, while document/token targeting and numerical evidence remain.
    handle = "body:" + "x" * 10000 + "|doc=646f63:box"
    env = validate_design(adapter, [surface_check(name=str(i), body=handle) for i in range(50)])
    assert env.ok
    result = env.result
    assert len(result["checks"]) == 50
    assert all(row["status"] == "pass" for row in result["checks"])
    assert any(row.get("input_echo_omitted") for row in result["checks"])
    assert all(row["evidence"]["minimum_scalar_candidate"]["minimum_distance_mm"] > 0 for row in result["checks"])
    assert len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode()) < 197632
    assert (tmp_path / (env.state["operation_id"] + ".terminal.json")).stat().st_size < receipts._MAX_RECORD


@pytest.mark.parametrize("maximum,expected_status", [(0.1, "unavailable"), (0.6, "pass")])
def test_notched_box_void_rejects_consistent_zero_native_face_minimum(monkeypatch, maximum, expected_status):
    """Analytic finite planes plus a polygon hole, independent of native output.

    The simulated native minimum ignores the hole in the top face. Its two points
    and scalar are internally consistent, reproducing the live false-pass trigger.
    Membership separately tests the finite patch and excludes the open hole.
    """
    import math

    target = body("box", token="box", high=(2, 3, 1))
    adapter = Adapter(monkeypatch, [target])
    target.parentComponent = adapter.root
    outer = [(0, 2), (0, 3), (0, 1)]
    notch = [(0.95, 1.05), (1.45, 1.55), (0.9, 1)]
    faces = []

    def patch(axis, coord, ranges, *, hole=False):
        face = NS(axis=axis, coord=coord, ranges=ranges, assemblyContext=None, isValid=True)
        def member(p, tolerance):
            values = [p.x, p.y, p.z]
            on_patch = abs(values[axis] - coord) < 1e-7 and all(
                low - 1e-7 <= x <= high + 1e-7
                for x, (low, high) in zip(values, ranges, strict=True))
            in_void = hole and notch[0][0] < p.x < notch[0][1] and notch[1][0] < p.y < notch[1][1]
            return on_patch and not in_void
        face.isPointOnFace = member
        return face

    for axis in (0, 1):
        faces.extend(patch(axis, coord, outer) for coord in outer[axis])
    faces.append(patch(2, 0, outer))
    for axis in (0, 1):
        faces.extend(patch(axis, coord, notch) for coord in notch[axis])
    faces.append(patch(2, 1, outer, hole=True))  # Native face 9: top with a void.
    faces.append(patch(2, 0.9, notch))
    target.faces = Collection(faces)

    def native(p, face):
        values = [p.x, p.y, p.z]
        projected = [min(max(x, low), high) for x, (low, high) in zip(values, face.ranges, strict=True)]
        projected[face.axis] = face.coord
        return NS(value=math.dist(values, projected), positionOne=p, positionTwo=point(*projected))

    adapter.app.measureManager.measureMinimumDistance.side_effect = native
    query = [10, 15, 10]
    result = validate_design(adapter, [surface_check(point_mm=query, max_faces=11, max_mm=maximum)]).result
    row = result["checks"][0]
    evidence = row["evidence"]
    candidate = evidence["minimum_scalar_candidate"]
    assert result["passed"] is (expected_status == "pass") and row["status"] == expected_status
    assert evidence["faces_attempted"] == evidence["faces_measured"] == 11
    assert evidence["faces_valid"] == 10 and evidence["face_failure_count"] == 1
    assert candidate["face_index"] == 9 and candidate["minimum_distance_mm"] == 0
    assert candidate["closest_point_a_mm"] == candidate["closest_point_b_mm"] == query
    assert candidate["witness_consistency"]["status"] == "consistent"
    assert candidate["surface_membership"]["status"] == "off_face"
    # Independent distance to the rectangular notch walls/floor is 0.5mm.
    true_distance_mm = 10 * min(1 - 0.95, 1.05 - 1, 1.5 - 1.45, 1.55 - 1.5, 1 - 0.9)
    assert true_distance_mm == pytest.approx(0.5)
    # The valid alternatives are not silently selected as an established minimum.
    assert candidate["minimum_distance_mm"] != pytest.approx(true_distance_mm)
    if expected_status == "pass":
        assert evidence["proof"] == "validated_witness_upper_bound"
        assert evidence["satisfying_witness"]["input_point_separation_mm"] == pytest.approx(0.5)
        assert evidence["satisfying_witness"]["surface_membership"]["status"] == "on_face"
    else:
        assert evidence["proof"] is None and evidence["satisfying_witness"] is None


@pytest.mark.parametrize("mode,status", [("false", "off_face"), ("missing", "unavailable"),
    ("raises", "unavailable"), ("nonboolean", "unavailable")])
@pytest.mark.parametrize("allow_upper_bound", [False, True])
def test_surface_probe_requires_native_trimmed_face_membership(monkeypatch, mode, status, allow_upper_bound):
    adapter, target = _box_surface_adapter(monkeypatch)
    first = target.faces.item(0)
    if mode == "missing":
        del first.isPointOnFace
    elif mode == "raises":
        first.isPointOnFace = Mock(side_effect=RuntimeError("native membership unavailable"))
    else:
        first.isPointOnFace = lambda p, tolerance: False if mode == "false" else None
    result = validate_design(adapter, [surface_check(max_mm=999 if allow_upper_bound else 0.1)]).result
    row = result["checks"][0]
    assert result["passed"] is allow_upper_bound
    assert row["status"] == ("pass" if allow_upper_bound else "unavailable")
    assert row["evidence"]["proof"] == ("validated_witness_upper_bound" if allow_upper_bound else None)
    failure, = row["evidence"]["face_failures"]
    assert failure["evidence"]["witness_consistency"]["status"] == "consistent"
    assert failure["evidence"]["surface_membership"]["status"] == status
    assert row["evidence"]["minimum_scalar_candidate"]["minimum_distance_mm"] > 0


@pytest.mark.parametrize("member", [False, None])
def test_face_distance_requires_membership_of_both_witnesses(monkeypatch, member):
    adapter, faces = _face_pair_adapter(monkeypatch)
    faces["right"].isPointOnFace = lambda p, tolerance: member
    result = validate_design(adapter, [face_pair_check(max_mm=100)]).result
    row = result["checks"][0]
    assert row["status"] == "unavailable" and not result["passed"]
    assert row["evidence"]["minimum_distance_mm"] == 10
    assert row["evidence"]["witness_consistency"]["status"] == "consistent"
    assert row["evidence"]["face_membership"]["a"]["status"] == "on_face"
    assert row["evidence"]["face_membership"]["b"]["status"] == ("off_face" if member is False else "unavailable")


@pytest.mark.parametrize("reverse", [False, True])
def test_face_distance_assigns_plane_witnesses_using_membership_and_retains_native_order(monkeypatch, reverse):
    adapter, faces = _face_pair_adapter(monkeypatch)
    for token, z in [("left", 1), ("right", 0)]:
        faces[token].isPointOnFace = lambda p, tolerance, z=z: abs(p.z - z) < tolerance and 0 <= p.x <= 2 and 0 <= p.y <= 3
    top, bottom = point(1, 1.5, 1), point(1, 1.5, 0)
    first, second = (bottom, top) if reverse else (top, bottom)
    adapter.app.measureManager.measureMinimumDistance.return_value = NS(
        value=1, positionOne=first, positionTwo=second)
    result = validate_design(adapter, [face_pair_check(min_mm=10, max_mm=10)]).result
    assert result["passed"]
    evidence = result["checks"][0]["evidence"]
    assert evidence["minimum_distance_mm"] == evidence["witness_consistency"]["separation_mm"] == 10
    assert evidence["closest_point_a_mm"] == [10, 15, 10]
    assert evidence["closest_point_b_mm"] == [10, 15, 0]
    assert all(item["status"] == "on_face" for item in evidence["face_membership"].values())
    if reverse:
        assert evidence["witness_order"] == "swapped_to_match_entities"
        native = evidence["native_witnesses"]
        assert native["closest_point_a_mm"] == [10, 15, 0] and native["closest_point_b_mm"] == [10, 15, 10]
        assert all(item["status"] == "off_face" for item in native["face_membership"].values())
    else:
        assert evidence["witness_order"] == "as_returned" and "native_witnesses" not in evidence


def test_upper_bound_uses_actual_input_point_not_slightly_shifted_native_anchor(monkeypatch):
    adapter, target = _box_surface_adapter(monkeypatch)
    native = adapter.app.measureManager.measureMinimumDistance.side_effect
    def shifted(p, face):
        if face.axis == 2 and face.coord == 3:
            a, b = point(p.x, p.y, p.z - 5e-8), point(p.x, p.y, 3)
            return NS(value=a.z - b.z, positionOne=a, positionTwo=b)
        return native(p, face)
    adapter.app.measureManager.measureMinimumDistance.side_effect = shifted
    result = validate_design(adapter, [surface_check(point_mm=[5, 5, 35], max_mm=4.9999996)]).result
    row = result["checks"][0]
    candidate = row["evidence"]["minimum_scalar_candidate"]
    assert candidate["witness_consistency"]["status"] == "consistent"
    assert candidate["minimum_distance_mm"] < 4.9999996 < candidate["input_point_separation_mm"]
    assert row["status"] == "unavailable" and not result["passed"]
    assert row["reason"] == "surface_maximum_boundary_uncertain"
    assert row["evidence"]["satisfying_witness"] is None


@pytest.mark.parametrize("maximum,status", [(0, "unavailable"), (1e-6, "pass")])
def test_upper_bound_includes_native_membership_tolerance(monkeypatch, maximum, status):
    adapter, _ = _box_surface_adapter(monkeypatch)
    native = adapter.app.measureManager.measureMinimumDistance.side_effect
    def within_native_tolerance(p, face):
        if face.axis == 2 and face.coord == 3:
            return NS(value=0, positionOne=p, positionTwo=p)
        return native(p, face)
    adapter.app.measureManager.measureMinimumDistance.side_effect = within_native_tolerance
    # Half a native point tolerance above the top plane; membership accepts it,
    # but a zero-length native witness does not prove an exact zero bound.
    result = validate_design(adapter, [surface_check(point_mm=[5, 5, 30.0000005], max_mm=maximum)]).result
    row = result["checks"][0]
    candidate = row["evidence"]["minimum_scalar_candidate"]
    assert candidate["minimum_distance_mm"] == candidate["input_point_separation_mm"] == 0
    assert candidate["surface_membership"]["status"] == "on_face"
    assert candidate["witness_upper_bound_mm"] == 1e-6
    assert row["status"] == status
    assert result["passed"] is (status == "pass")
    if status == "pass":
        assert row["evidence"]["satisfying_witness"]["witness_upper_bound_mm"] <= maximum


@pytest.mark.parametrize("native_tolerance", [None, float("nan"), float("inf"), True, -1, 0])
def test_unknown_or_invalid_native_tolerance_cannot_prove_upper_bound(monkeypatch, native_tolerance):
    adapter, _ = _box_surface_adapter(monkeypatch)
    adapter.app.pointTolerance = native_tolerance
    result = validate_design(adapter, [surface_check(max_mm=999)]).result
    row = result["checks"][0]
    candidate = row["evidence"]["minimum_scalar_candidate"]
    assert candidate["surface_membership"]["status"] == "unavailable"
    assert candidate["surface_membership"]["tolerance_mm"] is None
    assert candidate["witness_upper_bound_mm"] is None
    assert row["status"] == "unavailable" and not result["passed"]
    assert row["evidence"]["satisfying_witness"] is None


@pytest.mark.parametrize("mode", ["native_missing", "native_invalid", "transform_missing",
    "inverse_false", "inverse_raises", "point_transform_false", "nonfinite_transform"])
def test_proxy_membership_frame_conversion_fails_closed(monkeypatch, mode):
    adapter, target = _box_surface_adapter(monkeypatch, occurrence_path="Parent:1+Box:2")
    context = target.assemblyContext
    if mode in {"native_missing", "native_invalid"}:
        for face in target.faces.items:
            if mode == "native_missing":
                face.nativeObject = None
            else:
                face.nativeObject.isValid = False
    elif mode == "transform_missing":
        del context.transform2
    elif mode in {"inverse_false", "inverse_raises"}:
        inverse = NS(invert=Mock(return_value=False, side_effect=RuntimeError("inverse unavailable") if mode == "inverse_raises" else None))
        context.transform2 = NS(copy=lambda: inverse)
    elif mode == "point_transform_false":
        sys.modules["adsk.core"].Point3D = NS(create=lambda x, y, z: NS(x=x, y=y, z=z, transformBy=lambda matrix: False))
    else:
        context.transform2 = NS(copy=lambda: NS(invert=lambda: True, apply=lambda values: [float("nan"), 0, 0]))
    result = validate_design(adapter, [surface_check(
        body="body:box|occ=Parent:1+Box:2|doc=646f63:box", point_mm=[15, 65, 95])]).result
    row = result["checks"][0]
    assert row["status"] == "unavailable" and not result["passed"]
    evidence = row["evidence"]
    assert evidence["face_failure_count"] == 6 and evidence["faces_valid"] == 0
    assert evidence["satisfying_witness"] is None
    assert evidence["minimum_scalar_candidate"]["surface_membership"]["status"] == "unavailable"
    for face in target.faces.items:
        face.isPointOnFace.assert_not_called()
        if face.nativeObject is not None:
            face.nativeObject.isPointOnFace.assert_not_called()


def test_proxy_membership_context_diagnostic_is_bounded(monkeypatch):
    import json

    path = "Parent:1+" + "界" * 1000 + ":2"
    adapter, _ = _box_surface_adapter(monkeypatch, occurrence_path=path)
    result = validate_design(adapter, [surface_check(
        body=f"body:box|occ={path}|doc=646f63:box", point_mm=[15, 65, 95])]).result
    assert result["passed"]
    membership = result["checks"][0]["evidence"]["satisfying_witness"]["surface_membership"]
    assert membership["occurrence_path_truncated"]
    assert len(json.dumps(membership["occurrence_path"], ensure_ascii=True).encode()) <= 256
