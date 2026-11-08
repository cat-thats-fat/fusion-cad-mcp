import contextlib
import io
import json
import sys
import types
from unittest.mock import MagicMock

import pytest

from fusion_cad_mcp.tools import features, sketch
from tests._assembly_fakes import ScriptAdapter, occurrence, root_with


def execute(script, monkeypatch, design):
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = core, fusion
    monkeypatch.setitem(sys.modules, "adsk", adsk)
    monkeypatch.setitem(sys.modules, "adsk.core", core)
    monkeypatch.setitem(sys.modules, "adsk.fusion", fusion)
    core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(activeProduct=design))
    core.ValueInput = types.SimpleNamespace(createByString=lambda s: s)
    fusion.Design = types.SimpleNamespace(cast=lambda p: p)
    fusion.FeatureHealthStates = types.SimpleNamespace(HealthyFeatureHealthState=0)
    fusion.FeatureOperations = types.SimpleNamespace(NewBodyFeatureOperation=1)
    fusion.ExtentDirections = types.SimpleNamespace(PositiveExtentDirection=1, NegativeExtentDirection=-1)
    fusion.DistanceExtentDefinition = types.SimpleNamespace(create=lambda value: ("distance", value))
    ns = {}
    exec(script, ns)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    return json.loads(output.getvalue())


@pytest.mark.parametrize("direction,expected", [("positive", 1), ("negative", -1)])
def test_distance_extrude_direction_is_one_sided(monkeypatch, direction, expected):
    profile = object()
    sketch_obj = types.SimpleNamespace(name="outline", profiles=types.SimpleNamespace(count=1, item=lambda i: profile))
    inp = MagicMock()
    feat = types.SimpleNamespace(name="Extrude", healthState=0, bodies=types.SimpleNamespace(count=0))
    extrudes = types.SimpleNamespace(createInput=lambda p, op: inp, add=lambda i: feat)
    root = types.SimpleNamespace(sketches=types.SimpleNamespace(count=1, item=lambda i: sketch_obj),
                                 features=types.SimpleNamespace(extrudeFeatures=extrudes),
                                 allOccurrences=types.SimpleNamespace(count=0))
    result = execute(features.build_extrude("outline", expression="5 mm", direction=direction), monkeypatch,
                     types.SimpleNamespace(rootComponent=root))
    assert result["ok"]
    inp.setOneSideExtent.assert_called_once_with(("distance", "5 mm"), expected)
    inp.setDistanceExtent.assert_not_called()


def test_rebuild_refuses_without_touching_design(capsys):
    ns = {}
    exec(features.build_rebuild_feature("cut", "housing"), ns)
    ns["run"](None)
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "safe_rebuild_unavailable"
    assert result["feature_name"] == "cut"
    assert result["component"] == "housing"


def test_hole_rejects_wrong_handle_kind():
    with pytest.raises(ValueError, match="face handle"):
        features.build_add_hole("body", [0, 0, 0], "5 mm", face_handle="edge:body:abc")


def test_rib_requires_target_and_rejects_unsupported_mode():
    with pytest.raises(ValueError, match="target_body"):
        features.build_rib("profile", "2 mm")
    with pytest.raises(ValueError, match="symmetric"):
        features.build_rib("profile", "2 mm", side="left", target_body="body")


def test_hole_uses_requested_face_and_only_selected_body(monkeypatch):
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = core, fusion
    for name, module in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    point = types.SimpleNamespace(x=1.0, y=2.0, z=3.0)
    core.Point3D = types.SimpleNamespace(create=lambda x, y, z: point)
    core.Plane = types.SimpleNamespace(cast=lambda geometry: geometry)
    core.ValueInput = types.SimpleNamespace(createByString=lambda s: s)
    fusion.FeatureHealthStates = types.SimpleNamespace(HealthyFeatureHealthState=0)
    fusion.BRepFace = types.SimpleNamespace(cast=lambda f: f)
    fusion.ExtentDirections = types.SimpleNamespace(PositiveExtentDirection=1, NegativeExtentDirection=-1)
    inp = MagicMock()
    inp.setPositionByPoint.return_value = True
    feature = types.SimpleNamespace(name="Hole", healthState=0)
    hole_features = types.SimpleNamespace(createSimpleInput=lambda diameter: inp, add=lambda i: feature)
    comp = types.SimpleNamespace(features=types.SimpleNamespace(holeFeatures=hole_features))
    body = types.SimpleNamespace(name="target", parentComponent=comp)
    face = types.SimpleNamespace(body=body, geometry=object(), objectType="adsk::fusion::BRepFace", isPointOnFace=lambda p: p is point)
    root = types.SimpleNamespace(bRepBodies=types.SimpleNamespace(count=1, item=lambda i: body),
                                 allOccurrences=types.SimpleNamespace(count=0))
    body.parentComponent = root
    root.features = comp.features
    design = types.SimpleNamespace(rootComponent=root, findEntityByToken=lambda token: [face])
    core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(activeProduct=design))
    fusion.Design = types.SimpleNamespace(cast=lambda product: product)
    ns = {}
    exec(features.build_add_hole("target", [10, 20, 30], "5 mm", face_handle="face:target:abc"), ns)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    assert json.loads(output.getvalue())["ok"]
    inp.setPositionByPoint.assert_called_once_with(face, point)
    inp.setAllExtent.assert_called_once_with(1)
    assert inp.participantBodies == [body]


@pytest.mark.parametrize("is_flipped", [False, True])
def test_rib_capability_gate_and_current_api_signature(monkeypatch, is_flipped):
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = core, fusion
    for name, module in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    body = types.SimpleNamespace(name="target")
    curves = [object(), object()]
    sk = types.SimpleNamespace(name="profile", sketchCurves=types.SimpleNamespace(count=2, item=lambda i: curves[i]))
    inp = types.SimpleNamespace(isSymmetric=False, isFlipped=None, setToNextExtent=MagicMock(return_value=True))
    create = MagicMock(return_value=inp)
    feature = types.SimpleNamespace(name="Rib", healthState=0, errorOrWarningMessage="")
    ribs = types.SimpleNamespace(createInput=create, add=MagicMock(return_value=feature))
    root = types.SimpleNamespace(features=types.SimpleNamespace(ribFeatures=ribs),
        sketches=Collection(sk),
        bRepBodies=types.SimpleNamespace(count=1, item=lambda i: body),
        allOccurrences=types.SimpleNamespace(count=0))
    body.parentComponent = root
    design = types.SimpleNamespace(rootComponent=root)
    core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(activeProduct=design))
    core.ValueInput = types.SimpleNamespace(createByString=lambda value: value)
    fusion.Design = types.SimpleNamespace(cast=lambda product: product)
    fusion.FeatureHealthStates = types.SimpleNamespace(HealthyFeatureHealthState=0)
    ns = {}
    exec(features.build_rib("profile", "2 mm", target_body="target", is_flipped=is_flipped), ns)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    assert json.loads(output.getvalue())["ok"]
    create.assert_called_once_with(curves, "2 mm", body)
    assert inp.isSymmetric and inp.isFlipped is is_flipped
    inp.setToNextExtent.assert_called_once_with()
    feature.healthState = 1
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    warning = json.loads(output.getvalue())
    assert warning == {"ok": False, "error": "rib_feature_unhealthy", "feature_name": "Rib",
                       "health_state": 1, "health_message": "", "model_may_have_changed": True}
    inp.setToNextExtent.return_value = False
    ribs.add.reset_mock()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    assert json.loads(output.getvalue())["error"] == "rib_extent_failed"
    ribs.add.assert_not_called()
    root.features.ribFeatures = object()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    assert json.loads(output.getvalue())["error"] == "rib_not_scriptable"


class Collection:
    def __init__(self, *items):
        self.items = items
        self.count = len(items)

    def item(self, index):
        return self.items[index]


def test_sketch_resolver_uses_occurrence_path_and_refuses_shared_definition():
    ns = {}
    exec(sketch.SKETCH_RESOLVER_SCRIPT, ns)
    selected = types.SimpleNamespace(name="outline")
    component = types.SimpleNamespace(sketches=Collection(selected))
    other_component = types.SimpleNamespace(sketches=Collection(types.SimpleNamespace(name="outline")))
    occurrence = types.SimpleNamespace(fullPathName="Housing:1", component=component)
    other = types.SimpleNamespace(fullPathName="Lid:1", component=other_component)
    root = types.SimpleNamespace(sketches=Collection(), allOccurrences=Collection(occurrence, other))
    entity, owner, error = ns["_select_sketch"](root, "outline")
    assert error["error"] == "sketch_ambiguous"
    entity, owner, error = ns["_select_sketch"](root, "Housing:1 / outline")
    assert entity is selected and owner is component and error is None
    root.allOccurrences = Collection(occurrence, types.SimpleNamespace(fullPathName="Housing:2", component=component))
    entity, owner, error = ns["_select_sketch"](root, "Housing:1 / outline")
    assert entity is None and error["error"] == "shared_component_edit_unsupported"


def test_create_sketch_uses_component_plane_and_emits_document_scope(monkeypatch):
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = core, fusion
    for name, module in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    created = types.SimpleNamespace(name="", entityToken="abc")
    plane = object()
    sketches = types.SimpleNamespace(itemByName=lambda name: None, add=MagicMock(return_value=created))
    component = types.SimpleNamespace(name="Housing", sketches=sketches, xYConstructionPlane=plane)
    occurrence = types.SimpleNamespace(name="Housing:1", fullPathName="Housing:1", component=component)
    root = types.SimpleNamespace(allOccurrences=Collection(occurrence))
    design = types.SimpleNamespace(rootComponent=root)
    document = types.SimpleNamespace(creationId="doc1", dataFile=None)
    core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(activeProduct=design, activeDocument=document))
    fusion.Design = types.SimpleNamespace(cast=lambda product: product)
    ns = {}
    exec(sketch.build_create_sketch("xy", "outline", component="Housing:1"), ns)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    result = json.loads(output.getvalue())
    assert result["ok"]
    assert result["handle"] == "sketch:outline|doc=646f6331:abc"
    sketches.add.assert_called_once_with(plane)


def test_create_sketch_uses_native_construction_plane_in_selected_component(monkeypatch):
    owner = types.SimpleNamespace(name="Housing 🧰")
    occ = occurrence("Housing 🧰:1", component=owner)
    native = types.SimpleNamespace(component=owner)
    proxy = types.SimpleNamespace(
        component=owner, assemblyContext=occ, nativeObject=native,
        objectType="adsk::fusion::ConstructionPlane",
    )
    created = types.SimpleNamespace(name="", entityToken="new")
    add = MagicMock(return_value=created)
    owner.sketches = types.SimpleNamespace(itemByName=lambda _: None, add=add)
    design = types.SimpleNamespace(rootComponent=root_with(occurrences=[occ]), findEntityByToken=lambda _: [proxy])
    doc = types.SimpleNamespace(creationId="doc", dataFile=None)
    adapter = ScriptAdapter(
        monkeypatch, design, app=types.SimpleNamespace(activeDocument=doc),
        fusion={
            "BRepFace": types.SimpleNamespace(cast=lambda _: None),
            "ConstructionPlane": types.SimpleNamespace(cast=lambda plane: plane),
        },
    )

    env = sketch.create_sketch(adapter, "plane:Offset|occ=Housing 🧰:1:plane", "Outline 🧰", occ.fullPathName)

    assert env.ok and env.result["handle"] == "sketch:Outline 🧰|doc=646f63:new"
    add.assert_called_once_with(native)


@pytest.mark.parametrize(
    "primary,secondary,count,distance,expected_axis,expected_count,expected_distance",
    [
        ("x", None, 1, None, "y", 1, "0 mm"),
        ("y", None, 1, None, "z", 1, "0 mm"),
        ("x", "y", 2, "20 mm", "y", 2, "20 mm"),
    ],
)
def test_rectangular_pattern_executes_direction_two(
    monkeypatch, primary, secondary, count, distance,
    expected_axis, expected_count, expected_distance,
):
    """Exercise generated calls; doubles do not establish Fusion geometry."""
    core = types.ModuleType("adsk.core")
    fusion = types.ModuleType("adsk.fusion")
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = core, fusion
    for name, module in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    target = types.SimpleNamespace(name="rib")
    inp = types.SimpleNamespace(setDirectionTwo=MagicMock())
    create = MagicMock(return_value=inp)
    patterns = types.SimpleNamespace(createInput=create, add=lambda value: types.SimpleNamespace(name="Pattern", healthState=0))
    feature_collection = types.SimpleNamespace(count=1, item=lambda index: target, rectangularPatternFeatures=patterns)
    axes = {name: object() for name in "xyz"}
    root = types.SimpleNamespace(features=feature_collection, bRepBodies=Collection(), allOccurrences=Collection(), **{f"{name}ConstructionAxis": axis for name, axis in axes.items()})
    core.Application = types.SimpleNamespace(get=lambda: types.SimpleNamespace(activeProduct=types.SimpleNamespace(rootComponent=root)))
    core.ValueInput = types.SimpleNamespace(createByReal=lambda n: n, createByString=lambda s: s)
    core.ObjectCollection = types.SimpleNamespace(create=lambda: types.SimpleNamespace(add=lambda obj: None))
    fusion.Design = types.SimpleNamespace(cast=lambda product: product)
    fusion.PatternDistanceType = types.SimpleNamespace(ExtentPatternDistanceType="extent")
    fusion.FeatureHealthStates = types.SimpleNamespace(HealthyFeatureHealthState=0)
    script = features.build_pattern_rectangular("rib", x_axis=primary, x_count=3, x_distance="30 mm", y_axis=secondary, y_count=count, y_distance=distance)
    ns = {}
    exec(script, ns)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        ns["run"](None)
    result = json.loads(output.getvalue())
    assert result["ok"] and result["y_count"] == expected_count
    assert create.call_args.args[1:] == (axes[primary], 3, "30 mm", "extent")
    inp.setDirectionTwo.assert_called_once_with(axes[expected_axis], expected_count, expected_distance)
