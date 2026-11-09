import contextlib
import io
import sys
import types
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools.validation import build_validate_design, validate_design
from tests._assembly_fakes import Collection, point, root_with


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
        token: NS(objectType="adsk::fusion::BRepFace", body=owner, assemblyContext=None, token=token)
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
    assert result["checks"][0]["evidence"] == {
        "minimum_distance_mm": 10, "closest_point_a_mm": [10, 20, 30],
        "closest_point_b_mm": [10, 20, 40], "frame": "root", "units": "mm",
        "method": "Fusion MeasureManager.measureMinimumDistance",
    }
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
    occurrence = NS(fullPathName="Parent:1+Box:2")
    child_component = NS(name="Box")
    owner = NS(parentComponent=child_component)
    adapter.root.allOccurrences = Collection([occurrence])
    proxies = []
    for i, face in enumerate(native_faces.values()):
        proxy = NS(body=owner, assemblyContext=occurrence, location=point(4 + i, 5, 6))
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
