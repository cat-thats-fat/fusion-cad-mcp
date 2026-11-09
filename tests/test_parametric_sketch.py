import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import parametric_sketch as ps


@pytest.mark.parametrize("kwargs", [
    {"name": ""}, {"name": " bad "}, {"width": " "}, {"height": 3},
    {"plane": "bad"}, {"centered": 1}, {"component": ""},
])
def test_invalid_input_refused_without_dispatch(kwargs):
    args = dict(name="rectangle", width="10 mm", height="20 mm")
    args.update(kwargs)
    adapter = SimpleNamespace(execute_script=Mock())
    assert ps.create_parametric_rectangle(adapter, **args).error == "invalid_input"
    adapter.execute_script.assert_not_called()


def test_status_and_unknown_outcome_are_preserved():
    adapter = SimpleNamespace(execute_script=Mock(return_value=Envelope(ok=True, message="")))
    result = ps.create_parametric_rectangle(adapter, "rectangle", "10 mm", "20 mm")
    assert not result.ok and result.error == "parametric_rectangle_parse_failed"
    adapter.execute_script.return_value = Envelope(ok=True, message='{"ok":true,"profile_count":1}')
    assert ps.create_parametric_rectangle(adapter, "rectangle", "10 mm", "20 mm").result["profile_count"] == 1


def generated_namespace(monkeypatch):
    adsk, core, fusion = (types.ModuleType(name) for name in ("adsk", "adsk.core", "adsk.fusion"))
    adsk.core, adsk.fusion = core, fusion
    core.Point3D = SimpleNamespace(create=lambda *args: args)
    core.ValueInput = SimpleNamespace(createByString=lambda value: value)
    fusion.FeatureOperations = fusion.DimensionOrientations = fusion.ExtentDirections = object()
    fusion.DesignTypes = SimpleNamespace(ParametricDesignType=1)
    for name, mod in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, mod)
    ns = {}
    exec(ps.build_create_parametric_rectangle("rect", "W", "H", centered=True), ns)
    return ns


def test_generated_orchestration_verifies_editable_profile(monkeypatch):
    ns = generated_namespace(monkeypatch)
    owner = SimpleNamespace(**{key: [] for key in ("features", "sketches", "bRepBodies", "constructionPlanes", "constructionPoints")})
    design = SimpleNamespace(rootComponent=owner, designType=1, unitsManager=object())
    tx = SimpleNamespace(verify=Mock(return_value={"healthy": True}))
    point = SimpleNamespace(asArray=lambda: [1, 2, 3])
    sketch = SimpleNamespace(name="rect - Profile", isFullyConstrained=True,
                             profiles=SimpleNamespace(count=1), sketchToModelSpace=lambda p: point)
    ns.update(_select_component=lambda *a, **k: (owner, None, None), _mutation_items=lambda values: values,
              _lengths=lambda *a: {"x": 0, "y": 0, "z": 0, "width": 1, "height": 2},
              _Mutation=lambda *a: tx, _anchored_sketch=lambda *a: (sketch, object(), ["anchor"], ["datum"]),
              _rectangle=Mock(return_value=(object(), ["dimension"], [object()])),
              _verify_anchor=Mock(), _verify_bindings=Mock())
    result = ns["_run_rectangle"](design, ns["ARGS"])
    assert result["ok"] and result["fully_constrained"] and result["profile_count"] == 1
    assert result["profile_corners_component_mm"] == [[10, 20, 30]]
    assert ns["_rectangle"].call_args.args[-1] is True
    ns["_verify_bindings"].assert_called_once_with(design.unitsManager, ["anchor", "dimension"])
    tx.verify.assert_called_once()


def test_duplicate_name_refused_before_transaction(monkeypatch):
    ns = generated_namespace(monkeypatch)
    owner = SimpleNamespace(features=[SimpleNamespace(name="rect - Old")], sketches=[], bRepBodies=[], constructionPlanes=[], constructionPoints=[])
    design = SimpleNamespace(rootComponent=owner, designType=1)
    transaction = Mock()
    ns.update(_select_component=lambda *a, **k: (owner, None, None), _mutation_items=lambda values: values,
              _Mutation=transaction, _mutation_failure=lambda tx, exc, *a, **k: {"ok": False, "error": exc.code})
    assert ns["_run_rectangle"](design, ns["ARGS"])["error"] == "primitive_name_exists"
    transaction.assert_not_called()


def test_generated_failure_delegates_targeted_cleanup(monkeypatch):
    ns = generated_namespace(monkeypatch)
    owner = SimpleNamespace(features=[], sketches=[], bRepBodies=[], constructionPlanes=[], constructionPoints=[])
    design = SimpleNamespace(rootComponent=owner, designType=1, unitsManager=object())
    tx = object()
    failure = Mock(return_value={"ok": False, "error": "rectangle_failed", "rollback": "verified"})
    ns.update(_select_component=lambda *a, **k: (owner, None, None), _mutation_items=lambda values: values,
              _lengths=lambda *a: {"x": 0, "y": 0, "z": 0, "width": 1, "height": 2},
              _Mutation=lambda *a: tx, _anchored_sketch=Mock(side_effect=RuntimeError("failed")),
              _mutation_failure=failure)
    result = ns["_run_rectangle"](design, ns["ARGS"])
    assert result["rollback"] == "verified"
    assert failure.call_args.args[0] is tx
