import ast
import contextlib
import io
import json
import sys
import types
from copy import deepcopy

import pytest

from fusion_cad_mcp.edit_contract import prepare_edit_contract
from fusion_cad_mcp.receipts import _instrument
from fusion_cad_mcp.workspace_native import build_workspace_script


def test_generator_is_deterministic_and_compilable(tmp_path):
    for action in ("bind", "inspect", "edit", "export", "open", "activate", "close"):
        script = build_workspace_script(action, {"z": 1, "a": "some 'quoted' input"})
        assert script == build_workspace_script(action, {"a": "some 'quoted' input", "z": 1})
        compile(script, "<native>", "exec")
        assert len([node for node in ast.parse(script).body
                    if isinstance(node, ast.FunctionDef) and node.name == "run"]) == 1
        instrumented = _instrument(script, tmp_path, "12345678-1234-4234-8234-123456789abc", "a" * 64, "b" * 64)
        compile(instrumented, "<instrumented>", "exec")
    with pytest.raises(ValueError):
        build_workspace_script("unknown", {})
    with pytest.raises(ValueError):
        build_workspace_script("inspect", {"value": float("nan")})


class Collection:
    def __init__(self, *items):
        self.items = items
        self.count = len(items)

    def item(self, index):
        return self.items[index]


class FakeDocument:
    isValid = True
    name = "fixture"

    def __init__(self):
        self.products = types.SimpleNamespace(itemByProductType=lambda _: self.design)


def test_registry_binds_live_references_and_rejects_stale_or_ambiguous(monkeypatch):
    """Test control flow only; these stand-ins do not claim native geometry behavior."""
    doc = FakeDocument()
    root = types.SimpleNamespace(isValid=True)
    design = types.SimpleNamespace(isValid=True, parentDocument=doc, rootComponent=root)
    root.parentDesign = design
    doc.design = design
    app = types.SimpleNamespace(documents=Collection(doc), activeDocument=doc, version="fixture",
                                userInterface=types.SimpleNamespace(activeCommand="SelectCommand"))
    core = types.ModuleType("adsk.core")
    core.Application = types.SimpleNamespace(get=lambda: app)
    fusion = types.ModuleType("adsk.fusion")
    fusion.Design = types.SimpleNamespace(cast=lambda value: value)
    adsk = types.ModuleType("adsk")
    adsk.core, adsk.fusion = core, fusion
    monkeypatch.setitem(sys.modules, "adsk", adsk)
    monkeypatch.setitem(sys.modules, "adsk.core", core)
    monkeypatch.setitem(sys.modules, "adsk.fusion", fusion)
    monkeypatch.delitem(sys.modules, "_fusion_cad_workspace_v1", raising=False)

    def run(action, args):
        namespace = {}
        exec(build_workspace_script(action, args), namespace)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            namespace["run"](None)
        return json.loads(output.getvalue())

    bound = run("bind", {})
    assert bound["ok"] is True
    handle = bound["target_handle"]
    denied = run("close", {"target_handle": handle})
    assert denied["error"] == "user_document_close_refused"
    assert denied["mutation_state"] == "not_started"
    app.userInterface.activeCommand = "ExtrudeCommand"
    assert run("activate", {"target_handle": handle})["error"] == "command_active"
    assert run("close", {"target_handle": handle})["error"] == "command_active"
    assert run("open", {"path": "unread", "sha256": "a" * 64})["error"] == "command_active"
    app.userInterface.activeCommand = "SelectCommand"
    registry = sys.modules["_fusion_cad_workspace_v1"]
    token = handle.split(":", 1)[1]
    registry.entries[token]["pending"] = "operation"
    assert run("activate", {"target_handle": handle})["error"] == "operation_pending"
    registry.entries[token]["pending"] = None
    registry.entries[token]["owned"] = True
    doc.isSaved = False
    doc.close = lambda _: False
    failed_close = run("close", {"target_handle": handle})
    assert failed_close["error"] == "close_failed"
    assert failed_close["mutation_state"] == "unknown"
    app.documents = Collection(doc, doc)
    assert run("activate", {"target_handle": handle})["error"] == "target_binding_changed"
    assert run("bind", {"document_name": "fixture"})["error"] == "document_not_unique"
    app.documents = Collection(doc)
    sys.modules.pop("_fusion_cad_workspace_v1")
    stale = run("activate", {"target_handle": handle})
    assert stale["error"] == "target_invalid"
    assert stale["mutation_state"] == "not_started"
    assert "_fusion_cad_workspace_v1" not in sys.modules


def test_inspect_expression_readback_control_flow(monkeypatch):
    """Readback decisions are exercised with stand-ins, not geometry assertions."""
    adsk = types.ModuleType("adsk")
    core, fusion = types.ModuleType("adsk.core"), types.ModuleType("adsk.fusion")
    adsk.core, adsk.fusion = core, fusion
    monkeypatch.setitem(sys.modules, "adsk", adsk)
    monkeypatch.setitem(sys.modules, "adsk.core", core)
    monkeypatch.setitem(sys.modules, "adsk.fusion", fusion)
    parameter = types.SimpleNamespace(unit="mm")
    design, root, doc = object(), object(), object()
    entry = {"document": doc, "design": design, "root": root, "pending": None}
    namespace = {}
    exec(build_workspace_script("inspect", {"target_handle": "h", "parameter_name": "width",
                                               "expected_expression": "12 mm", "checks": []}), namespace)
    namespace.update({"_environment": lambda _: {}, "_resolve": lambda *_: entry,
                      "_active": lambda *_: None, "_no_external": lambda *_: None,
                      "_timeline_end": lambda *_: None,
                      "_parameter": lambda *_: (parameter, {"expression": "12 mm"}),
                      "_measure": lambda *_: {}, "_health": lambda *_: {},
                      "_parameter_expectation": lambda *args: args[1]})
    def readback(_design, _parameter, _units, expected):
        if expected != "12 mm":
            raise namespace["_MutationError"]("mismatch", "mismatch")
    namespace["_parameter_readback"] = readback
    assert namespace["_dispatch"](None)["parameter_matches_expected"] is True
    namespace["_ARGS"]["expected_expression"] = "13 mm"
    result = namespace["_dispatch"](None)
    assert result["ok"] is True and result["parameter_matches_expected"] is False
    del namespace["_ARGS"]["parameter_name"]
    with pytest.raises(namespace["_WorkspaceError"], match="parameter_name_required"):
        namespace["_dispatch"](None)


def test_custom_script_replaces_parameter_setter(monkeypatch):
    adsk = types.ModuleType("adsk")
    core, fusion = types.ModuleType("adsk.core"), types.ModuleType("adsk.fusion")
    adsk.core, adsk.fusion = core, fusion
    for name, module in (("adsk", adsk), ("adsk.core", core), ("adsk.fusion", fusion)):
        monkeypatch.setitem(sys.modules, name, module)
    payload = {"version": 1, "target_handle": "handle", "operation_id": "op", "parameter_name": "p",
               "expected_expression": "1 mm", "expected_value_mm": 1.0, "value_tolerance_mm": 0.0,
               "expression": "2 mm", "requirements": [], "protected": [],
               "custom_script": "def run(ctx):\n    ctx['design'].parameter.expression = '2 mm'"}
    parameter = types.SimpleNamespace(unit="mm", expression="1 mm")
    design = types.SimpleNamespace(parameter=parameter)
    entry = {"document": object(), "design": design, "root": object(), "pending": None, "owned": True}
    namespace = {}
    exec(build_workspace_script("edit", payload), namespace)
    class Tx:
        def __init__(self, *_): pass
        def record_expression(self, _): pass
        def verify(self): return {}
    clean = {"healthy": 0, "warning": 0, "error": 0, "unknown": 0}
    namespace.update({"_environment": lambda _: {}, "_resolve": lambda *_: entry,
                      "_active": lambda *_: None, "_available": lambda *_: None,
                      "_no_external": lambda *_: None, "_timeline_end": lambda *_: None,
                      "prepare_edit_contract": lambda **k: {"version": 1, **k},
                      "_parameter": lambda *_: (parameter, {"expression": parameter.expression, "value_mm": 1.0}),
                      "_measure": lambda *_: {}, "_health": lambda *_: clean,
                      "compare_baseline": lambda *_: {"passed": True},
                      "_parameter_expectation": lambda *_: None, "_Mutation": Tx,
                      "_parameter_readback": lambda *_: None,
                      "compare_evidence": lambda *_args, **_kwargs: {"execution": "completed", "verification": "verified", "checks": []}})
    result = namespace["_dispatch"](None)
    assert result["ok"] is True and parameter.expression == "2 mm"
    # A script observing the original expression proves no automatic setter ran first.
    parameter.expression = "1 mm"
    namespace["_ARGS"]["custom_script"] = "def run(ctx):\n    assert ctx['design'].parameter.expression == '1 mm'\n    ctx['design'].parameter.expression = '2 mm'"
    assert namespace["_dispatch"](None)["ok"] is True


@pytest.mark.parametrize(
    "pose_readback,status,reason,verification",
    [
        ("matched", "passed", None, "verified"),
        ("changed", "failed", "protected_pose_changed", "failed"),
        ("missing", "unknown", "protected_pose_unavailable", "incomplete"),
        ("invalid", "unknown", "protected_pose_unavailable", "incomplete"),
    ],
)
def test_protected_pose_distinguishes_measured_change_from_unavailable_matrix(
    monkeypatch, pose_readback, status, reason, verification,
):
    adsk = types.ModuleType("adsk")
    adsk.core = types.ModuleType("adsk.core")
    adsk.fusion = types.ModuleType("adsk.fusion")
    for name, module in (("adsk", adsk), ("adsk.core", adsk.core), ("adsk.fusion", adsk.fusion)):
        monkeypatch.setitem(sys.modules, name, module)

    common = {"frame": "root", "units": "mm", "baseline_tolerance": 0, "tolerance": 0}
    contract = prepare_edit_contract(
        target_handle="handle", operation_id="12345678-1234-4234-8234-123456789abc",
        parameter_name="width", expression="70 mm", expected_expression="60 mm",
        expected_value_mm=60, value_tolerance_mm=0,
        requirements=[
            {**common, "id": "width", "metric": "body_extent", "entities": ["root / Blank"],
             "axis": "x", "baseline": 60, "comparator": "eq", "expected": 70},
        ],
        protected=[
            {**common, "id": "pose", "metric": "occurrence_translation", "entities": ["Mount:1"],
             "axis": "z", "baseline": 0},
        ],
    )
    checks = contract["requirements"] + contract["protected"]
    baseline = {
        spec["id"]: {
            **{key: spec[key] for key in ("metric", "entities", "units", "frame", "axis")},
            "classification": "exact", "value": spec["baseline"],
        }
        for spec in checks
    }
    baseline["pose"]["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    after = deepcopy(baseline)
    after["width"]["value"] = 70
    if pose_readback == "changed":
        after["pose"]["matrix"] = [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    elif pose_readback == "missing":
        del after["pose"]["matrix"]
        del after["pose"]["value"]
        after["pose"].update(classification="unknown", reason="invalid transform")
    elif pose_readback == "invalid":
        after["pose"]["matrix"][0] = True

    parameter = types.SimpleNamespace(unit="mm", expression="60 mm")
    design = object()
    entry = {"document": object(), "design": design, "root": object(), "pending": None, "owned": True}

    class Tx:
        def __init__(self, *_):
            pass

        def record_expression(self, _):
            pass

        def verify(self):
            return {}

    def parameter_readback(*_):
        expression = parameter.expression
        return parameter, {"expression": expression, "units": "mm", "value_mm": float(expression.split()[0])}

    clean = {"healthy": 1, "warning": 0, "error": 0, "unknown": 0}
    namespace = {}
    exec(build_workspace_script("edit", contract), namespace)
    namespace.update({"_environment": lambda _: {}, "_resolve": lambda *_: entry,
                      "_active": lambda *_: None, "_no_external": lambda *_: None,
                      "_timeline_end": lambda *_: None, "_parameter": parameter_readback,
                      "_measure": lambda *_: baseline if parameter.expression == "60 mm" else after,
                      "_health": lambda _: clean, "_parameter_expectation": lambda *_: None,
                      "_Mutation": Tx, "_parameter_readback": lambda *_: None})

    result = namespace["_dispatch"](None)
    assert (result["ok"], result["execution"], result["verification"]) == (True, "completed", verification)
    pose = result["checks"][1]
    assert (pose["status"], pose["reason"]) == (status, reason)
    assert pose["classification"] == ("unknown" if status == "unknown" else "exact")
    assert result["failed_requirement_ids"] == (["pose"] if status == "failed" else [])
    assert result["unknown_requirement_ids"] == (["pose"] if status == "unknown" else [])
    assert entry["pending"] is None


@pytest.mark.parametrize(
    "problem,check_id,status",
    [
        (None, None, "passed"),
        ("extent_drift", "width", "failed"),
        ("clearance_drift", "gap", "failed"),
        ("overlap_count_drift", "overlap", "failed"),
        ("pose_drift", "pose", "failed"),
        ("pose_unavailable", "pose", "unknown"),
        ("clearance_unavailable", "gap", "unknown"),
        ("parameter_drift", None, "failed"),
        ("health_drift", None, "failed"),
        ("binding_changed", None, "unknown"),
        ("target_inactive", None, "unknown"),
        ("cleanup_failed", None, None),
        ("custom_failure", None, None),
    ],
)
def test_rollback_requires_fresh_exact_witnesses(monkeypatch, problem, check_id, status):
    """Exercise generated rollback control flow with supplied native observation rows."""
    adsk = types.ModuleType("adsk")
    adsk.core = types.ModuleType("adsk.core")
    adsk.fusion = types.ModuleType("adsk.fusion")
    for name, module in (("adsk", adsk), ("adsk.core", adsk.core), ("adsk.fusion", adsk.fusion)):
        monkeypatch.setitem(sys.modules, name, module)

    common = {"frame": "root", "baseline_tolerance": 0.04, "tolerance": 0.04}
    contract = prepare_edit_contract(
        target_handle="handle", operation_id="12345678-1234-4234-8234-123456789abc",
        parameter_name="width", expression="70 mm", expected_expression="60 mm",
        expected_value_mm=60, value_tolerance_mm=0.04,
        requirements=[
            {**common, "id": "width", "metric": "body_extent", "entities": ["root / Blank"],
             "axis": "x", "units": "mm", "baseline": 60, "comparator": "eq", "expected": 70},
            {**common, "id": "gap", "metric": "clearance", "entities": ["root / Blank", "root / Bracket"],
             "units": "mm", "baseline": 3, "comparator": "ge", "expected": 3},
            {**common, "id": "overlap", "metric": "interference", "entities": ["root / Blank", "root / Insert"],
             "units": "mm^3", "baseline": 1, "comparator": "le", "expected": 1},
        ],
        protected=[
            {**common, "id": "pose", "metric": "occurrence_translation", "entities": ["Mount:1"],
             "axis": "z", "units": "mm", "baseline": 0},
        ],
    )
    checks = contract["requirements"] + contract["protected"]
    baseline = {
        spec["id"]: {
            **{key: spec[key] for key in ("metric", "entities", "units", "frame", "axis") if key in spec},
            "classification": "exact", "value": spec["baseline"],
        }
        for spec in checks
    }
    baseline["width"]["value"] = 59.97
    baseline["gap"]["interference_count"] = 0
    baseline["overlap"]["interference_count"] = 1
    baseline["pose"]["matrix"] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    restored = deepcopy(baseline)
    if problem == "extent_drift":
        # Within tolerance of the caller's expected baseline, outside tolerance
        # of the actual measurement that preceded the edit.
        restored["width"]["value"] = 60.03
    elif problem == "clearance_drift":
        restored["gap"]["interference_count"] = 1
    elif problem == "overlap_count_drift":
        restored["overlap"]["interference_count"] = 2
    elif problem == "pose_drift":
        restored["pose"]["matrix"] = [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    elif problem == "pose_unavailable":
        del restored["pose"]["matrix"]
    elif problem == "clearance_unavailable":
        restored["gap"]["classification"] = "heuristic"

    payload = dict(contract)
    if problem == "custom_failure":
        payload["custom_script"] = (
            "def run(ctx):\n    ctx['design'].parameter.expression = '70 mm'\n"
            "    raise RuntimeError('injected custom failure')"
        )
    parameter = types.SimpleNamespace(unit="mm", expression="60 mm")
    design = types.SimpleNamespace(parameter=parameter)
    entry = {"document": object(), "design": design, "root": object(), "pending": None, "owned": True}
    rolled_back = False
    measurement_calls = []

    class Tx:
        def __init__(self, *_):
            pass

        def record_expression(self, _):
            pass

        def verify(self):
            assert parameter.expression == "70 mm"
            raise RuntimeError("injected failure after assignment")

        def rollback(self):
            nonlocal rolled_back
            assert parameter.expression == "70 mm"
            if problem == "cleanup_failed":
                raise RuntimeError("injected cleanup failure")
            parameter.expression = "60 mm"
            rolled_back = True
            return {"status": "verified"}

    def resolve(*_):
        return entry.copy() if rolled_back and problem == "binding_changed" else entry

    def active(*_):
        if rolled_back and problem == "target_inactive":
            raise RuntimeError("target changed during rollback")

    def read_parameter(*_):
        return parameter, {
            "expression": parameter.expression, "units": "mm",
            "value_mm": 60.1 if rolled_back and problem == "parameter_drift" else 60.0,
        }

    def measure(_, current_entry, current_checks):
        assert current_entry is entry and current_checks == checks
        measurement_calls.append(rolled_back)
        return restored if rolled_back else baseline

    def health(_):
        return {"healthy": 1, "warning": int(rolled_back and problem == "health_drift"),
                "error": 0, "unknown": 0}

    namespace = {}
    exec(build_workspace_script("edit", payload), namespace)
    namespace.update({"_environment": lambda _: {}, "_resolve": resolve,
                      "_active": active, "_no_external": lambda *_: None,
                      "_timeline_end": lambda *_: None, "_parameter": read_parameter,
                      "_measure": measure, "_health": health,
                      "_parameter_expectation": lambda *_: None, "_Mutation": Tx})

    result = namespace["_dispatch"](None)
    if problem is None:
        assert (result["ok"], result["execution"], result["verification"]) == (True, "failed", "not_run")
        assert result["recovery"]["status"] == "rollback_verified"
        assert result["rollback_baseline"]["parameter"]["expression"] == contract["expected_expression"]
        assert set(result["rollback_baseline"]["measurements"]) == {row["id"] for row in checks}
        assert result["rollback_baseline"]["health"]["warning"] == 0
        assert entry["pending"] is None
    else:
        assert (result["ok"], result["execution"], result["verification"]) == (False, "unknown", "incomplete")
        assert result["recovery"]["status"] == "unresolved_partial"
        assert entry["pending"] == contract["operation_id"]
    details = result["recovery"]["details"]
    if status is None:
        assert "witnesses" not in details
    else:
        witnesses = details["witnesses"]
        assert witnesses["status"] == status
        assert witnesses["classification"] == ("unknown" if status == "unknown" else "exact")
        if check_id:
            key = "mismatch_ids" if status == "failed" else "unavailable_ids"
            assert witnesses[key] == [check_id]
    assert measurement_calls == ([False] if problem in {"binding_changed", "target_inactive", "cleanup_failed", "custom_failure"}
                                 else [False, True])
