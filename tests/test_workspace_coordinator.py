"""Exercise orchestration policy using synthetic native receipts, never geometry proof."""

import hashlib
import json
import math
import uuid
from pathlib import Path

import pytest

from fusion_cad_mcp import workspace
from fusion_cad_mcp.edit_contract import compare_evidence
from fusion_cad_mcp.envelope import Envelope
from fusion_cad_mcp.workspace_store import ConflictError, WorkspaceStore

CHECK = {
    "id": "width", "metric": "body_extent", "entities": ["root / Shell"], "axis": "x",
    "units": "mm", "frame": "root", "baseline": 60, "baseline_tolerance": 0.01,
    "comparator": "eq", "expected": 70, "tolerance": 0.01,
}
EDIT = {
    "parameter_name": "width", "expression": "70 mm", "expected_expression": "60 mm",
    "expected_value_mm": 60, "value_tolerance_mm": 0.01,
}
HEALTH = {"healthy": 3, "warning": 0, "error": 0, "unknown": 0}
ENV = {"fusion_version": "test", "registry_version": 1}


@pytest.fixture
def project(tmp_path, monkeypatch):
    manifest = {"schema": 1, "seed": "seed.f3d", "edit": EDIT, "requirements": [CHECK]}
    (tmp_path / "design.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "seed.f3d").write_bytes(b"synthetic seed")
    # Only replace script serialization. The coordinator still invokes the
    # typed adapter, validates receipt shape and runs the real local store.
    monkeypatch.setattr(workspace.workspace_native, "build_workspace_script",
                        lambda action, payload: json.dumps([action, payload]))
    return tmp_path, manifest


class NativeDouble:
    def __init__(self, handler):
        self.calls = []
        self.recovery_probes = []
        self.handler = handler

    def execute_typed_script(self, script, *, read_only, operation_id, receipt_context, recovery_probe=False):
        action, payload = json.loads(script)
        self.calls.append((action, payload, operation_id))
        if recovery_probe:
            self.recovery_probes.append(action)
        assert read_only is (action in ("bind", "inspect"))
        assert receipt_context == {"kind": "workspace", "action": action,
                                   "target_handle": payload.get("target_handle", "unbound")}
        response = self.handler(action, payload)
        if isinstance(response, Envelope):
            return response
        return Envelope(
            ok=True, message=json.dumps({"ok": True, **response}),
            state={"operation_id": operation_id, "receipt_status": "completed",
                   "document_creation_id": "not-a-workspace-identity", "receipt_evidence": "verbose"},
        )


def _op():
    return str(uuid.uuid4())


def _parameter(value, expression=None):
    return {"value_mm": value, "units": "mm", "expression": expression or f"{value} mm"}


def _observation(spec, value, **extra):
    return {**{key: spec[key] for key in ("metric", "entities", "frame", "units", "axis") if key in spec},
            "value": value, "classification": "exact", **extra}


def _info(checks, value, *, expression=None):
    return {"parameter": _parameter(value, expression), "health": HEALTH, "environment": ENV,
            "pending": None, "measurements": {spec["id"]: _observation(spec, value) for spec in checks}}


def _export(payload):
    data = b"synthetic candidate"
    Path(payload["path"]).write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _build_responder(action, payload):
    if action == "open":
        assert hashlib.sha256(Path(payload["path"]).read_bytes()).hexdigest() == payload["sha256"]
        suffix = "seed" if Path(payload["path"]).read_bytes() == b"synthetic seed" else "candidate"
        return {"target_handle": suffix, "environment": ENV}
    if action == "inspect":
        value = 60 if payload["target_handle"] == "seed" and "expected_expression" not in payload else 70
        return {**_info(payload["checks"], value), "parameter_matches_expected": True}
    if action == "edit":
        checks = payload["requirements"] + payload["protected"]
        baseline = _info(checks, 60)["measurements"]
        result = _info(checks, 70)["measurements"]
        return {**compare_evidence(payload, baseline, result, execution="completed"),
                "baseline": baseline, "result": result, "parameter": _parameter(70),
                "health": {"result": HEALTH}, "environment": ENV}
    if action == "export":
        return _export(payload)
    pytest.fail(f"Unexpected dispatch {action}")


@pytest.mark.parametrize("problem", ["no-seed", "invalid-schema", "no-requirements", "invalid-recipe", "no-entrypoint"])
def test_invalid_build_request_never_dispatches(project, problem):
    root, manifest = project
    if problem == "no-seed":
        (root / "seed.f3d").unlink()
    elif problem == "invalid-schema":
        manifest["schema"] = True
    elif problem == "no-requirements":
        manifest["requirements"] = []
    else:
        manifest["script"] = "recipe.py"
        (root / "recipe.py").write_text("def run(" if problem == "invalid-recipe" else "value = 1",
                                       encoding="utf-8")
    (root / "design.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = NativeDouble(lambda *_: pytest.fail("native call during invalid preflight"))
    assert workspace.build(adapter, root).error == "invalid_manifest"
    assert adapter.calls == []


def test_build_dispatch_consumes_frozen_bytes_and_conflicts_on_changed_asset(project):
    root, manifest = project
    manifest["script"] = "recipe.py"
    recipe = "def run(_ctx):\n    pass\n"
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    (root / "design.json").write_bytes(manifest_bytes)
    (root / "recipe.py").write_text(recipe, encoding="utf-8", newline="\n")
    op = _op()

    def respond(action, payload):
        if action == "open":
            (root / "seed.f3d").write_bytes(b"changed seed")
            (root / "recipe.py").write_text("def run(_ctx):\n    return 2\n", encoding="utf-8")
            (root / "design.json").write_bytes(b"changed manifest")
            assert Path(payload["path"]).read_bytes() == b"synthetic seed"
        if action == "edit":
            assert payload["custom_script"] == recipe
            return {"execution": "refused", "verification": "not_run"}
        return _build_responder(action, payload)

    adapter = NativeDouble(respond)
    assert workspace.build(adapter, root, operation_id=op).result["execution"] == "refused"
    meta = WorkspaceStore(root / ".fusion").get_job(op)["metadata"]
    assert meta["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert meta["inputs"]["script"]["sha256"] == hashlib.sha256(recipe.encode("utf-8")).hexdigest()
    assert meta["inputs"]["seed"]["sha256"] == hashlib.sha256(b"synthetic seed").hexdigest()
    (root / "design.json").write_bytes(manifest_bytes)
    assert workspace.build(adapter, root, operation_id=op).error == "operation_conflict"
    assert [action for action, _, _ in adapter.calls] == ["open", "inspect", "edit"]


def test_receipted_refusal_replays_history_while_unknown_write_fences_next_job(project):
    root, _ = project
    adapter = NativeDouble(lambda *_: Envelope(
        ok=False, error="target_invalid", result={"ok": False, "error": "target_invalid", "mutation_state": "not_started"}
    ))
    contract = {**EDIT, "target_handle": "bound", "operation_id": _op(), "requirements": [CHECK]}
    out = workspace.edit(adapter, root, contract)
    assert out.error == "target_invalid" and out.result["execution"] == "refused"
    assert workspace.edit(adapter, root, contract).result["historical"] is True
    assert workspace.edit(adapter, root, {**contract, "expression": "71 mm"}).error == "operation_conflict"
    adapter.handler = lambda *_: Envelope(ok=False, error="transport_timeout",
                                         state={"execution_outcome": "unknown"})
    lost = workspace.edit(adapter, root, {**contract, "operation_id": _op()})
    assert lost.result["execution"] == "unknown"
    assert workspace.edit(adapter, root, {**contract, "operation_id": _op()}).error == "workspace_write_blocked"
    assert len(adapter.calls) == 2


@pytest.mark.parametrize("outcome", ["matched", "reverted", "unavailable"])
def test_edit_requires_fresh_parameter_and_geometry_after_callback(project, outcome):
    root, _ = project
    op = _op()
    contract = {**EDIT, "target_handle": "bound", "operation_id": op, "requirements": [CHECK]}

    def respond(action, payload):
        if action == "edit":
            return _build_responder(action, payload)
        assert action == "inspect"
        assert payload["target_handle"] == "bound" and payload["expected_expression"] == "70 mm"
        if outcome == "unavailable":
            return Envelope(ok=False, error="transport_timeout", state={"execution_outcome": "unknown"})
        return {**_info(payload["checks"], 60 if outcome == "reverted" else 70),
                "parameter_matches_expected": outcome == "matched"}

    adapter = NativeDouble(respond)
    result = workspace.edit(adapter, root, contract)
    assert result.result["execution"] == "completed"
    assert result.result["construction"]["verification"] == "verified"
    assert "checks" not in result.result["construction"]
    assert not {"baseline", "result", "environment", "diagnostics"}.intersection(result.result)
    assert result.result["receipt"] == {"operation_id": op, "receipt_status": "completed"}
    if outcome != "matched":
        assert result.result["construction"]["parameter"]["value_mm"] == 70
    assert result.result["verification"] == {
        "matched": "verified", "reverted": "failed", "unavailable": "incomplete"
    }[outcome]
    assert result.ok is (outcome == "matched")
    assert result.result["checks"][0]["result"] == {"matched": 70, "reverted": 60, "unavailable": None}[outcome]
    assert result.result["readback"]["status"] == {
        "matched": "matched", "reverted": "different_or_incomplete", "unavailable": "unavailable"
    }[outcome]
    if outcome == "reverted":
        assert result.result["parameter"]["value_mm"] == 60
        assert result.result["construction"]["different_check_values"] == {"width": 70}
        assert result.result["readback"]["witness_mismatches"] == ["width"]
    if outcome == "unavailable":
        assert result.result["parameter"] is None
        assert result.result["readback"]["phase_failure"]["effect_scope"] == "readback"
    assert [action for action, _, _ in adapter.calls] == ["edit", "inspect"]
    assert adapter.calls[0][2] != adapter.calls[1][2]
    index = WorkspaceStore(root / ".fusion")
    assert index.unresolved_jobs() == []
    evidence = index.get_job(op)["evidence"]["response"]
    assert evidence["baseline"]["width"]["value"] == 60
    assert evidence["construction"]["result"]["width"]["value"] == 70
    assert evidence["construction"]["checks"][0]["result"] == 70
    replay = workspace.edit(adapter, root, contract)
    assert replay.result["historical"] is True and "baseline" not in replay.result
    assert replay.result["construction"] == result.result["construction"]
    assert len(adapter.calls) == 2


def test_build_stops_before_export_when_callback_changes_revert(project):
    root, _ = project

    def respond(action, payload):
        if action == "inspect" and payload.get("expected_expression"):
            return {**_info(payload["checks"], 60), "parameter_matches_expected": False}
        return _build_responder(action, payload)

    adapter = NativeDouble(respond)
    result = workspace.build(adapter, root)
    assert not result.ok and result.result["execution"] == "completed"
    assert result.result["verification"] == "failed" and result.result["artifact_state"] == "unavailable"
    assert result.result["construction"]["parameter"]["value_mm"] == 70
    assert result.result["parameter"]["value_mm"] == 60
    assert [action for action, _, _ in adapter.calls] == ["open", "inspect", "edit", "inspect"]
    assert WorkspaceStore(root / ".fusion").list_checkpoints() == []


def test_failed_checkpoint_promotion_retains_archive_as_wip(project):
    root, _ = project
    adapter = NativeDouble(_build_responder)
    out = workspace.checkpoint(adapter, root, "candidate", "main", [{**CHECK, "expected": 65}],
                               "width", accepted=True)
    assert out.result["verification"] == "failed"
    assert out.result["checkpoint_kind"] == "wip"
    index = WorkspaceStore(root / ".fusion")
    assert index.checkout("main", "accepted") is None
    assert index.checkout("main", "wip")["artifact_hash"] == out.result["artifact_hash"]


@pytest.mark.parametrize("failure", ["refused", "timeout", "integrity"])
def test_build_export_failure_keeps_confirmed_construction(project, failure):
    root, _ = project
    op = _op()

    def respond(action, payload):
        if action != "export":
            return _build_responder(action, payload)
        if failure == "refused":
            return Envelope(ok=False, error="target_not_active",
                            result={"ok": False, "error": "target_not_active", "mutation_state": "not_started"})
        if failure == "integrity":
            return {**_export(payload), "sha256": "0" * 64}
        _export(payload)
        return Envelope(ok=False, error="transport_timeout", state={"execution_outcome": "unknown"})

    adapter = NativeDouble(respond)
    out = workspace.build(adapter, root, operation_id=op)
    assert not out.ok
    assert out.result["execution"] == "completed" and out.result["verification"] == "incomplete"
    assert out.result["construction"]["execution"] == "completed"
    assert out.result["parameter"]["value_mm"] == 70
    assert out.result["checks"][0]["result"] == 70
    assert out.result["target_handle"] == "seed"
    phase = out.result["phase_failure"]
    assert phase["phase"] == "export" and phase["effect_scope"] == "archive_output"
    index = WorkspaceStore(root / ".fusion")
    assert index.get_job(op)["state"] == ("uncertain" if failure == "timeout" else "completed")
    assert index.list_checkpoints() == []
    if failure == "timeout":
        assert out.result["artifact_state"] == "unconfirmed"
        assert Path(phase["staging_output"]).read_bytes() == b"synthetic candidate"
    else:
        assert out.result["artifact_state"] == "unavailable"
        assert "staging_output" not in phase
    replay = workspace.build(adapter, root, operation_id=op)
    assert replay.result["construction"] == out.result["construction"]
    assert [action for action, _, _ in adapter.calls] == ["open", "inspect", "edit", "inspect", "export"]


def test_build_readback_timeout_retains_completed_edit_and_archive_as_wip(project):
    root, _ = project

    def respond(action, payload):
        if action == "inspect" and payload["target_handle"] == "candidate":
            return Envelope(ok=False, error="transport_timeout", state={"execution_outcome": "unknown"})
        return _build_responder(action, payload)

    out = workspace.build(NativeDouble(respond), root)
    assert out.result["execution"] == "completed" and out.result["verification"] == "incomplete"
    assert out.result["construction"]["verification"] == "verified"
    assert out.result["target_handle"] == "candidate"
    assert out.result["roundtrip_details"]["phase_failure"]["effect_scope"] == "readback"
    assert out.result["publication"]["references"] == ["wip"]
    assert WorkspaceStore(root / ".fusion").unresolved_jobs() == []


def test_checkpoint_uncertain_reopen_keeps_archive_without_claiming_source_preservation(project):
    root, _ = project
    op = _op()

    def respond(action, payload):
        if action == "open":
            return Envelope(ok=False, error="transport_timeout", state={"execution_outcome": "unknown"})
        return _build_responder(action, payload)

    result = workspace.checkpoint(NativeDouble(respond), root, "source", "main", [CHECK], "width",
                                  operation_id=op, accepted=True)
    assert not result.ok
    data = result.result
    assert data["execution"] == "completed" and data["verification"] == "incomplete"
    assert data["target_handle"] == "source" and data["artifact_state"] == "available"
    assert data["source_edit_requested"] is False and data["source_close_requested"] is False
    assert "source_preserved" not in data
    assert data["phase_failure"]["effect_scope"] == "working_document_open"
    index = WorkspaceStore(root / ".fusion")
    assert index.get_job(op)["state"] == "uncertain"
    assert index.get_job(op)["artifact_hash"] == data["artifact_hash"]
    assert index.get_artifact(data["artifact_hash"])["size"] > 0
    assert index.list_checkpoints() == []


def test_failed_publication_reports_existing_reference_and_terminal_evidence(project, monkeypatch):
    root, _ = project
    adapter = NativeDouble(_build_responder)
    original = WorkspaceStore.checkpoint

    def fault(self, branch, artifact_hash, kind, *args, **kwargs):
        if kind == "accepted":
            raise ConflictError("injected publication conflict")
        return original(self, branch, artifact_hash, kind, *args, **kwargs)

    monkeypatch.setattr(WorkspaceStore, "checkpoint", fault)
    out = workspace.build(adapter, root)
    assert out.error == "workspace_publication_incomplete"
    assert out.result["execution"] == "completed"
    assert [ref["kind"] for ref in out.result["publication"]["references"]] == ["wip"]
    assert WorkspaceStore(root / ".fusion").get_job(out.result["operation_id"])["state"] == "completed"


def test_cache_reuse_checks_expected_formula_before_promotion(project):
    root, _ = project
    first = workspace.build(NativeDouble(_build_responder), root)
    assert first.ok and first.result["publication"]["cache_published"] is True

    def respond(action, payload):
        if action == "inspect" and payload["target_handle"] == "candidate":
            assert payload["expected_expression"] == "70 mm"
            return {**_info(payload["checks"], 70, expression="68 mm + 2 mm"),
                    "parameter_matches_expected": False}
        return _build_responder(action, payload)

    adapter = NativeDouble(respond)
    hit = workspace.build(adapter, root)
    assert not hit.ok and hit.result["cache_hit"] is True
    assert hit.result["verification"] == "incomplete"
    assert hit.result["publication"]["references"] == ["wip"]
    assert WorkspaceStore(root / ".fusion").checkout("main", "accepted")["operation_id"] == first.result["operation_id"]
    assert [action for action, _, _ in adapter.calls] == ["open", "inspect", "open", "inspect"]


def test_check_only_change_reuses_geometry_and_publishes_fresh_evidence(project):
    root, manifest = project
    first = workspace.build(NativeDouble(_build_responder), root)
    assert first.ok
    assert "diagnostics" not in first.result and "environment" not in first.result
    assert "checks" not in first.result["construction"]
    evidence = WorkspaceStore(root / ".fusion").get_job(first.result["operation_id"])["evidence"]["response"]
    assert evidence["diagnostics"]["archive"]["measurements"]["width"]["value"] == 70
    manifest["requirements"] = [{**CHECK, "id": "another-width-check", "tolerance": 0.02}]
    (root / "design.json").write_text(json.dumps(manifest), encoding="utf-8")
    adapter = NativeDouble(_build_responder)
    reused = workspace.build(adapter, root)
    assert reused.ok and reused.result["cache_hit"] is True
    assert reused.result["build_key"] == first.result["build_key"]
    assert reused.result["verifier_key"] != first.result["verifier_key"]
    assert reused.result["checks"][0]["id"] == "another-width-check"
    assert "edit" not in [action for action, _, _ in adapter.calls]


def test_workspace_keys_change_with_receipt_runtime_or_coordinator_policy(monkeypatch):
    environment = workspace._environment({"environment": ENV})
    verifier = workspace._verifier([CHECK])
    with monkeypatch.context() as patch:
        patch.setattr(workspace.receipts, "_RUNTIME", workspace.receipts._RUNTIME + "\n# revised receipt wrapper")
        assert workspace._environment({"environment": ENV}) != environment
        assert workspace._verifier([CHECK]) != verifier
    getsource = workspace.inspect.getsource

    def revised(source):
        return getsource(source) + ("\n# revised coordinator" if source is workspace else "")

    monkeypatch.setattr(workspace.inspect, "getsource", revised)
    assert workspace._environment({"environment": ENV}) != environment
    assert workspace._verifier([CHECK]) != verifier


def test_fresh_protected_pose_is_compared_directly_with_original_baseline(project):
    root, _ = project
    pose = {key: value for key, value in CHECK.items() if key not in ("comparator", "expected")}
    pose.update(id="pose", metric="occurrence_translation", entities=["Mount:1"], axis="z", baseline=0)

    def read_pose(angle):
        cosine, sine = math.cos(angle), math.sin(angle)
        return _observation(pose, 0, matrix=[cosine, -sine, 0, 0, sine, cosine, 0, 0,
                                             0, 0, 1, 0, 0, 0, 0, 1])

    def respond(action, payload):
        if action == "inspect":
            fresh = _info(payload["checks"], 70)
            fresh["measurements"]["pose"] = read_pose(1.8e-8)
            return {**fresh, "parameter_matches_expected": True}
        prior = {"width": _observation(CHECK, 60), "pose": read_pose(0)}
        after = {"width": _observation(CHECK, 70), "pose": read_pose(9e-9)}
        return {**compare_evidence(payload, prior, after, execution="completed"),
                "baseline": prior, "result": after, "parameter": _parameter(70),
                "health": {"result": HEALTH}, "environment": ENV}

    result = workspace.edit(NativeDouble(respond), root,
                            {**EDIT, "target_handle": "bound", "operation_id": _op(),
                             "requirements": [CHECK], "protected": [pose]})
    assert result.result["readback"]["witness_mismatches"] == []
    assert result.result["verification"] == "failed"
    assert result.result["failed_requirement_ids"] == ["pose"]
    assert result.result["checks"][1]["reason"] == "protected_pose_changed"


def test_roundtrip_rejects_pose_change_even_when_declared_translation_matches(project):
    root, _ = project
    pose = {**CHECK, "id": "pose", "metric": "occurrence_translation", "entities": ["Mount:1"],
            "axis": "z", "expected": 0}
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    quarter_turn = [0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    def respond(action, payload):
        if action == "inspect":
            info = _info(payload["checks"], 70)
            matrix = identity if payload["target_handle"] == "source" else quarter_turn
            info["measurements"]["pose"] = _observation(pose, 0, matrix=matrix)
            return info
        return _build_responder(action, payload)

    result = workspace.checkpoint(NativeDouble(respond), root, "source", "main", [CHECK, pose],
                                  "width", accepted=True)
    assert result.result["verification"] == "incomplete"
    assert result.result["roundtrip_details"]["witness_mismatches"] == ["pose"]
    assert result.result["checkpoint_kind"] == "wip"
    assert WorkspaceStore(root / ".fusion").checkout("main", "accepted") is None


def test_recover_requires_current_bound_agreement_and_preserves_unknown_job(project, monkeypatch):
    root, _ = project
    op = _op()
    adapter = NativeDouble(lambda *_: Envelope(ok=False, error="transport_timeout",
                                               state={"execution_outcome": "unknown"}))
    contract = {**EDIT, "target_handle": "bound", "operation_id": op, "requirements": [CHECK]}
    assert workspace.edit(adapter, root, contract).result["execution"] == "unknown"
    prepared = adapter.calls[0][1]
    declared = _build_responder("edit", prepared)
    receipt = {
        "status": "completed", "request_fingerprint": "native-fingerprint",
        "receipt_context": {"kind": "workspace", "action": "edit", "target_handle": "bound"},
        "declared_result": declared,
    }
    monkeypatch.setattr(workspace, "get_status", lambda *_args, **_kwargs: Envelope(ok=True, result=receipt))

    def observe(action, payload):
        assert action == "inspect"
        assert payload["target_handle"] == "bound"
        assert payload["expected_expression"] == "70 mm"
        return {**_info(payload["checks"], 70), "parameter_matches_expected": True}

    adapter.handler = lambda action, payload: {**observe(action, payload), "parameter": _parameter(70, "69 mm + 1 mm")}
    assert workspace.recover(adapter, root, op).error == "recovery_state_changed"
    assert WorkspaceStore(root / ".fusion").unresolved_jobs()[0]["operation_id"] == op
    adapter.handler = observe
    recovered = workspace.recover(adapter, root, op)
    assert recovered.ok
    job = WorkspaceStore(root / ".fusion").get_job(op)
    assert job["state"] == "uncertain" and job["resolved_by"] == recovered.result["resolved_by"]
    assert [action for action, _, _ in adapter.calls] == ["edit", "inspect", "inspect"]
    assert adapter.recovery_probes == ["inspect", "inspect"]


@pytest.mark.parametrize("case", ["match", "value_drift", "geometry_drift", "read_lost"])
def test_inline_rollback_requires_independent_baseline_confirmation(project, case):
    root, _ = project
    contract = {**EDIT, "target_handle": "bound", "operation_id": _op(), "requirements": [CHECK]}
    def respond(action, payload):
        if action == "edit":
            checks = payload["requirements"] + payload["protected"]
            initial = _info(checks, 60)
            return {"execution": "failed", "verification": "not_run", "environment": ENV,
                    "rollback_baseline": {key: initial[key] for key in ("parameter", "measurements", "health")},
                    "recovery": {"status": "rollback_verified", "details": {"status": "verified"}}}
        if action == "inspect":
            if case == "read_lost":
                return Envelope(ok=False, error="transport_timeout", state={"execution_outcome": "unknown"})
            info = _info(payload["checks"], 61 if case == "geometry_drift" else 60)
            if case == "value_drift":
                info["parameter"]["value_mm"] = 60.1
            return {**info, "parameter_matches_expected": True}
        pytest.fail("unexpected native action")
    adapter = NativeDouble(respond)
    output = workspace.edit(adapter, root, contract)
    assert [call[0] for call in adapter.calls] == ["edit", "inspect"]
    job = WorkspaceStore(root / ".fusion").get_job(contract["operation_id"])
    if case == "match":
        assert output.result["recovery"]["status"] == "rollback_verified"
        assert output.result["rollback_readback"]["status"] == "matched"
        assert job["state"] == "failed"
    else:
        assert output.result["recovery"]["status"] == "unresolved_partial"
        assert output.result["execution"] == "unknown"
        assert job["state"] == "uncertain"
        blocked = workspace.edit(adapter, root, {**contract, "operation_id": _op()})
        assert blocked.error == "workspace_write_blocked"
