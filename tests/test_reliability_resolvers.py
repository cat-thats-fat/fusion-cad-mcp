from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from fusion_cad_mcp.resolver import RESOLVER_SCRIPT
from fusion_cad_mcp.tools import construction, features, sketch
from tests._assembly_fakes import Collection, ScriptAdapter, occurrence, point, root_with


def test_scoped_component_and_feature_reject_ambiguous_shared_instances():
    feature = NS(name="Cut")
    component = NS(name="Part", features=Collection([feature]))
    one = occurrence("Part:1", component=component)
    two = occurrence("Part:2", component=component)
    root = root_with(occurrences=[one, two], features=Collection())
    ns = {}
    exec(RESOLVER_SCRIPT, ns)
    assert ns["_select_component"](root, None, True) == (root, None, None)
    assert ns["_select_component"](root, "Part", True)[2]["error"] == "occurrence_ambiguous"
    assert ns["_select_component"](root, "Part:1", True)[2]["error"] == "shared_component_edit_unsupported"
    assert ns["_select_feature"](root, "Cut")[2]["error"] == "feature_ambiguous"
    assert ns["_select_feature"](root, "Part:2 / Cut", True)[2]["error"] == "shared_component_edit_unsupported"


def test_dimension_component_hint_does_not_fall_back():
    sk = NS(name="outline")
    child = NS(name="Correct", sketches=Collection([sk]), occurrences=Collection())
    root = NS(name="Root", sketches=Collection(), occurrences=Collection([NS(component=child)]))
    ns = {}
    exec(sketch._SKETCH_RESOLVER, ns)
    assert ns["_resolve_sketch"](root, "outline", "Typo") == (None, None, None)


def test_edge_direction_filters_curves_and_uses_actual_length(monkeypatch):
    linear = NS(geometry=NS(linear=True), length=2, startVertex=NS(geometry=point()), endVertex=NS(geometry=point(0, 0, 2)))
    curved = NS(geometry=NS(linear=False), length=5, startVertex=NS(geometry=point()), endVertex=NS(geometry=point(0, 0, 2)))
    short = NS(geometry=NS(linear=True), length=0.5, startVertex=NS(geometry=point()), endVertex=NS(geometry=point(0, 0, 2)))
    body = NS(name="body", edges=Collection([linear, curved, short]))
    edge_set = Mock()
    fillets = NS(createInput=lambda: NS(addConstantRadiusEdgeSet=edge_set), add=lambda _: NS(name="f", healthState=0))
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root_with(bodies=[body], features=NS(filletFeatures=fillets))),
        core={"Line3D": NS(cast=lambda g: g if g.linear else None), "ObjectCollection": NS(create=Collection), "ValueInput": NS(createByString=lambda x: x)})
    result = features.fillet_edges_by_geometry(adapter, "body", "1 mm", min_length_mm=10)
    assert result.ok
    assert edge_set.call_args.args[0].items == [linear]


def test_delete_construction_ambiguous_never_deletes(monkeypatch):
    first, second = NS(name="same", deleteMe=Mock()), NS(name="same", deleteMe=Mock())
    root = NS(constructionPlanes=Collection([first]), constructionAxes=Collection([second]), constructionPoints=Collection())
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root), core={"Point3D": NS(create=point)})
    result = construction.delete_construction(adapter, "same")
    assert result.error == "construction_ambiguous"
    first.deleteMe.assert_not_called()
    second.deleteMe.assert_not_called()


def test_failed_helper_creation_reports_cleanup(monkeypatch):
    helper = NS(name="", isValid=True, isComputeDeferred=False,
                sketchPoints=NS(add=Mock(side_effect=RuntimeError("failed"))))
    def delete_helper():
        helper.isValid = False
        return True
    helper.deleteMe = Mock(side_effect=delete_helper)
    root = NS(xYConstructionPlane=object(), sketches=NS(add=lambda _: helper))
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root), core={"Point3D": NS(create=point)})
    result = construction.create_construction_point(adapter, [0, 0, 0])
    assert result.error == "construction_creation_failed"
    assert result.result["mutation_state"] == "rolled_back"
    helper.deleteMe.assert_called_once()


def test_unhealthy_feature_reports_changed_model(monkeypatch):
    edge = NS(geometry=NS(linear=True), length=2, startVertex=NS(geometry=point()), endVertex=NS(geometry=point(0, 0, 2)))
    body = NS(name="body", edges=Collection([edge]))
    feat = NS(name="Fillet1", healthState=1, errorOrWarningMessage="")
    fillets = NS(createInput=lambda: NS(addConstantRadiusEdgeSet=Mock()), add=lambda _: feat)
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root_with(bodies=[body], features=NS(filletFeatures=fillets))),
        core={"Line3D": NS(cast=lambda g: g), "ObjectCollection": NS(create=Collection), "ValueInput": NS(createByString=lambda x: x)},
        fusion={"FeatureHealthStates": NS(HealthyFeatureHealthState=0)})
    result = features.fillet_edges_by_geometry(adapter, "body", "1 mm")
    assert result.error == "feature_unhealthy"
    assert result.result["model_may_have_changed"] is True


def test_duplicate_body_selection_reports_ambiguity(monkeypatch):
    a, b = NS(name="plate"), NS(name="plate")
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root_with(bodies=[a, b])))
    result = features.fillet_edges_by_geometry(adapter, "plate", "1 mm")
    assert result.error == "body_ambiguous"


@pytest.mark.parametrize("mode", ["none", "raises"])
def test_unreadable_created_feature_health_reports_uncertainty(monkeypatch, mode):
    class Feature:
        name = "Created Fillet"

        @property
        def healthState(self):
            if mode == "raises":
                raise RuntimeError("unavailable")
            return None

    edge = NS(geometry=NS(linear=True), length=2,
              startVertex=NS(geometry=point()), endVertex=NS(geometry=point(0, 0, 2)))
    body = NS(name="body", edges=Collection([edge]))
    fillets = NS(createInput=lambda: NS(addConstantRadiusEdgeSet=Mock()), add=lambda _: Feature())
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root_with(bodies=[body], features=NS(filletFeatures=fillets))),
        core={"Line3D": NS(cast=lambda g: g), "ObjectCollection": NS(create=Collection),
              "ValueInput": NS(createByString=lambda x: x)})
    result = features.fillet_edges_by_geometry(adapter, "body", "1 mm")
    assert result.error == "feature_health_unavailable"
    assert result.result["feature_name"] == "Created Fillet"
    assert result.result["model_may_have_changed"] is True


def test_revolve_ambiguous_participant_refuses_before_feature_add(monkeypatch):
    sketch_obj = NS(name="profile", profiles=Collection([object()]))
    body_a, body_b = NS(name="plate"), NS(name="plate")
    feature_add = Mock()
    rev_input = NS(setAngleExtent=Mock())
    root = root_with(bodies=[body_a, body_b], sketches=Collection([sketch_obj]),
                     features=NS(revolveFeatures=NS(createInput=lambda *_: rev_input, add=feature_add)),
                     zConstructionAxis=object())
    adapter = ScriptAdapter(monkeypatch, NS(rootComponent=root),
        core={"ValueInput": NS(createByString=lambda value: value)},
        fusion={"FeatureOperations": NS(CutFeatureOperation=1)})
    result = features.revolve(adapter, "profile", operation="cut", participants=["plate"])
    assert result.error == "body_ambiguous"
    feature_add.assert_not_called()
