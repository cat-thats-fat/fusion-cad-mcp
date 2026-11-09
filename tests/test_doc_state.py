"""Document-state script behavior with a local API double."""

from types import SimpleNamespace

import pytest

from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.tools import doc_state
from tests._assembly_fakes import ScriptAdapter

PARAMETRIC = 1
DIRECT = 0


class StubDesign:
    def __init__(self, design_type, *, params_error=None):
        self.designType = design_type
        self.params_error = params_error
        self.unitsManager = SimpleNamespace(defaultLengthUnits="mm")
        self.rootComponent = SimpleNamespace(
            bRepBodies=SimpleNamespace(count=3),
            meshBodies=SimpleNamespace(count=2),
            sketches=SimpleNamespace(count=1),
            allOccurrences=SimpleNamespace(count=2),
            features=SimpleNamespace(count=5),
        )

    @property
    def userParameters(self):
        if self.params_error:
            raise self.params_error
        if self.designType == DIRECT:
            raise RuntimeError("3 : this is not a parametric design")
        return SimpleNamespace(count=4)

    @property
    def timeline(self):
        if self.designType == DIRECT:
            raise RuntimeError("3 : this is not a parametric design")
        return SimpleNamespace(count=7)


def _run(monkeypatch, design):
    app = SimpleNamespace(
        activeDocument=SimpleNamespace(name="Part1", isModified=True),
        userInterface=SimpleNamespace(activeWorkspace=SimpleNamespace(name="FusionSolid")),
    )
    adapter = ScriptAdapter(
        monkeypatch, design, app=app,
        fusion={"DesignTypes": SimpleNamespace(ParametricDesignType=PARAMETRIC)},
    )
    return doc_state.run(adapter)


def test_script_reports_parametric_design_fully(monkeypatch):
    env = _run(monkeypatch, StubDesign(PARAMETRIC))
    assert env.ok is True
    assert env.result == {
        "active_design": True,
        "design_type": "parametric",
        "is_parametric": True,
        "parameters_count": 4,
        "timeline_count": 7,
        "features_count": 5,
        "unavailable": {},
        "bodies_count": 3,
        "mesh_bodies_count": 2,
        "sketches_count": 1,
        "components_count": 2,
        "doc_name": "Part1",
        "is_dirty": True,
        "workspace": "FusionSolid",
        "units": "mm",
    }


def test_script_returns_partial_state_on_direct_design(monkeypatch):
    env = _run(monkeypatch, StubDesign(DIRECT))
    assert env.ok is True
    assert env.result == {
        "active_design": True,
        "design_type": "direct",
        "is_parametric": False,
        "parameters_count": None,
        "timeline_count": None,
        "features_count": 5,
        "unavailable": {
            "parameters_count": "requires a parametric design",
            "timeline_count": "requires a parametric design",
        },
        "bodies_count": 3,
        "mesh_bodies_count": 2,
        "sketches_count": 1,
        "components_count": 2,
        "doc_name": "Part1",
        "is_dirty": True,
        "workspace": "FusionSolid",
        "units": "mm",
    }


def test_script_does_not_swallow_unrelated_errors(monkeypatch):
    design = StubDesign(PARAMETRIC, params_error=RuntimeError("5 : object is invalid"))
    with pytest.raises(RuntimeError, match="object is invalid"):
        _run(monkeypatch, design)


def test_script_reports_no_active_design(monkeypatch):
    env = _run(monkeypatch, None)
    assert env.ok is True
    assert env.result == {"active_design": False}


def test_run_surfaces_adapter_error_unchanged():
    error = Envelope(ok=False, error="fusion_error", traceback="...")

    class FailingAdapter:
        def execute_script(self, script, *, read_only=False):
            assert read_only is True
            return error

    assert doc_state.run(FailingAdapter()) is error


def test_run_handles_unparseable_stdout():
    class MalformedAdapter:
        def execute_script(self, script, *, read_only=False):
            assert read_only is True
            return Envelope(ok=True, message="not json at all")

    env = doc_state.run(MalformedAdapter())
    assert env.ok is False
    assert env.error == "doc_state_parse_failed"
    assert env.message == "not json at all"
