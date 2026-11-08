"""Harness ownership/reporting invariants; no CAD geometry is simulated here."""

import contextlib
import importlib
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from fusion_cad_mcp.envelope import Envelope


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("live_regression")


def _app(monkeypatch, document):
    app = SimpleNamespace(activeDocument=document)
    adsk, core, fusion = ModuleType("adsk"), ModuleType("adsk.core"), ModuleType("adsk.fusion")
    core.Application = SimpleNamespace(get=lambda: app)
    adsk.core, adsk.fusion = core, fusion
    for name, module in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    return app


class _Transport:
    def __init__(self):
        self.mutations = []
        self.read_only = []

    def execute_script(self, script, *, read_only=False):
        self.read_only.append(read_only)
        namespace = {"mutations": self.mutations}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(script, namespace)
            namespace["run"]("")
        return Envelope(ok=True, message=output.getvalue())


@pytest.mark.parametrize("changed", ["identity", "name", "saved", "none", None])
def test_typed_script_ownership_guard_runs_before_mutation(harness, monkeypatch, changed):
    transport = _Transport()
    fixture = harness.Fixture(transport)
    fixture.creation_id = "fixture-id"
    document = SimpleNamespace(creationId="fixture-id", name=fixture.name, isSaved=False)
    if changed == "identity":
        document.creationId = "user-document"
    elif changed == "name":
        document.name = "User renamed fixture"
    elif changed == "saved":
        document.isSaved = True
    app = _app(monkeypatch, document if changed != "none" else None)
    script = 'def run(_ctx):\n    mutations.append("changed")\n    print("done")\n'
    if changed:
        with pytest.raises(RuntimeError, match="not the test fixture"):
            fixture.adapter.execute_script(script, read_only=True)
        assert transport.mutations == []
    else:
        assert fixture.adapter.execute_script(script, read_only=True).message.strip() == "done"
        assert transport.mutations == ["changed"]
    assert transport.read_only == [True]
    assert app.activeDocument is (document if changed != "none" else None)
    with pytest.raises(AttributeError):
        fixture.adapter.execute_document_op("close")


def test_lost_creation_reply_cannot_claim_cleanup(harness):
    class Empty:
        def execute_script(self, script):
            return Envelope(ok=True, message="")

    fixture = harness.Fixture(Empty())
    with pytest.raises(AssertionError, match="no JSON"):
        fixture.start()
    with pytest.raises(RuntimeError, match="creation outcome is unknown"):
        fixture.close()


@pytest.mark.parametrize("saved", [False, True])
def test_cleanup_only_discards_known_unsaved_fixture(harness, monkeypatch, saved):
    transport = _Transport()
    fixture = harness.Fixture(transport)
    fixture.creation_id, fixture.previous_id = "fixture-id", "previous-id"
    events = []
    owned = SimpleNamespace(creationId=fixture.creation_id, name=fixture.name, isSaved=saved,
                            close=lambda save: events.append(("close_fixture", save)) or True)
    previous = SimpleNamespace(creationId=fixture.previous_id, name="Previous", isSaved=True,
                               activate=lambda: events.append("activate_previous"))
    app = _app(monkeypatch, previous)
    app.documents = SimpleNamespace(count=2, item=lambda i: [owned, previous][i])
    if saved:
        with pytest.raises(RuntimeError, match="refusing to discard"):
            fixture.close()
        assert events == []
    else:
        fixture.close()
        assert events == [("close_fixture", False), "activate_previous"]


def test_cleanup_requires_explicit_acknowledgement(harness):
    class EmptyObject:
        def execute_script(self, script):
            return Envelope(ok=True, message="{}")

    fixture = harness.Fixture(EmptyObject())
    fixture.creation_id = "fixture-id"
    with pytest.raises(RuntimeError, match="acknowledgement"):
        fixture.close()


def test_capability_report_cannot_pass_when_cleanup_fails(harness, monkeypatch, tmp_path):
    module = importlib.import_module("live_capabilities")
    report_path = tmp_path / "report.json"
    class Adapter:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Fixture:
        checks = []

        def __init__(self, adapter):
            pass

        def run(self):
            return {"status": "passed", "checks": []}

        def close(self):
            raise RuntimeError("missing acknowledgement")

    monkeypatch.setattr(module, "FusionAdapter", Adapter)
    monkeypatch.setattr(module, "CapabilityFixture", Fixture)
    monkeypatch.setattr(sys, "argv", ["live_capabilities", "--run", "--report", str(report_path)])
    with pytest.raises(RuntimeError, match="acknowledgement"):
        module.main()
    assert json.loads(report_path.read_text()) == {"status": "failed", "checks": [], "cleanup": "unverified"}
