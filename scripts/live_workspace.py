"""Opt-in workspace acceptance checks against owned local Fusion fixtures.

Run with --run. Existing documents are witnessed through native references and
never edited or closed. Reports omit document names, handles and host paths.
The feedback budget is 10 seconds per small-fixture edit (measured, not assumed).
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import tempfile
import textwrap
import time
import uuid
from pathlib import Path

from fusion_cad_mcp import workspace
from fusion_cad_mcp.adapter import FusionAdapter
from fusion_cad_mcp.envelope import parse_status_json
from fusion_cad_mcp.receipts import get_status, run_typed
from fusion_cad_mcp.tools.operations import reconcile_operation
from fusion_cad_mcp.tools.parametric_sketch import _RECTANGLE_SCRIPT
from fusion_cad_mcp.tools.primitives import _PRIMITIVE_SCRIPT
from fusion_cad_mcp.workspace_native import build_workspace_script


def checked(env):
    if not env.ok:
        raise RuntimeError(json.dumps(env.to_dict()))
    result = parse_status_json(env) or env.result
    if not result or result.get("ok") is False:
        raise RuntimeError(json.dumps(env.to_dict()))
    return result


def failed_outcome(env):
    assert not env.ok and isinstance(env.result, dict), env.to_dict()
    return env.result


def native(adapter, body, **kwargs):
    tree = ast.parse(build_workspace_script("bind", {}))
    tree.body.extend(ast.parse(_PRIMITIVE_SCRIPT + _RECTANGLE_SCRIPT.split("\ndef run(_ctx):")[0]).body)
    entry = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run")
    entry.body = ast.parse("app = adsk.core.Application.get()\n" + textwrap.dedent(body)).body
    return checked(adapter.execute_typed_script(ast.unparse(ast.fix_missing_locations(tree)), **kwargs))


def check(identifier, metric, entities, baseline, expected=None, axis=None, tolerance=0.01):
    result = dict(id=identifier, metric=metric, entities=entities, frame="root",
                  units="mm^3" if metric in ("body_volume", "interference") else "mm",
                  baseline=baseline, baseline_tolerance=tolerance, tolerance=tolerance)
    if expected is not None:
        result.update(comparator="eq", expected=expected)
    if axis:
        result["axis"] = axis
    return result


def fixture(adapter):
    return native(adapter, '''
        if app.userInterface.activeCommand != 'SelectCommand': raise RuntimeError('Active command')
        reg = _registry()
        if hasattr(reg, 'test_originals'): raise RuntimeError('Existing fixture suite requires inspection')
        def signature(doc):
            design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
            if design is None: return (doc.isModified, None)
            root = design.rootComponent
            return (doc.isModified, root.bRepBodies.count, root.allOccurrences.count, design.timeline.count,
                    tuple((root.bRepBodies.item(i).volume,
                           tuple(root.bRepBodies.item(i).preciseBoundingBox.minPoint.asArray()),
                           tuple(root.bRepBodies.item(i).preciseBoundingBox.maxPoint.asArray()))
                          for i in range(root.bRepBodies.count)))
        reg.test_signature = signature
        reg.test_originals = [(app.documents.item(i), signature(app.documents.item(i))) for i in range(app.documents.count)]
        reg.test_previous = app.activeDocument
        doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
        root = design.rootComponent
        handle = _binding(app, doc, True)
        reg.test_owned = [handle]
        design.userParameters.add('width', adsk.core.ValueInput.createByString('60 mm'), 'mm', 'Fixture width')
        args = dict(name='Editable Profile', width='width', height='30 mm', x='0 mm', y='0 mm', z='0 mm',
                    plane='xy', centered=False, component='root')
        result = _run_rectangle(design, args)
        if not result.get('ok'): raise RuntimeError(str(result))
        sketch = root.sketches.itemByName(result['sketch'])
        extrusion = root.features.extrudeFeatures.addSimple(sketch.profiles.item(0),
            adsk.core.ValueInput.createByString('5 mm'), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        extrusion.name = 'Editable Extrusion'
        extrusion.bodies.item(0).name = 'Editable'
        args.update(name='Protected Profile', width='10 mm', height='10 mm', x='100 mm')
        result = _run_rectangle(design, args)
        if not result.get('ok'): raise RuntimeError(str(result))
        sketch = root.sketches.itemByName(result['sketch'])
        extrusion = root.features.extrudeFeatures.addSimple(sketch.profiles.item(0),
            adsk.core.ValueInput.createByString('10 mm'), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        extrusion.bodies.item(0).name = 'Protected'
        transform = adsk.core.Matrix3D.create()
        transform.translation = adsk.core.Vector3D.create(2, 4, 1)
        occ = root.occurrences.addNewComponent(transform)
        occ.component.name = 'PCB'
        if not design.computeAll(): raise RuntimeError('Fixture recompute failed')
        print(json.dumps({'ok': True, 'target_handle': handle, 'owned': True, 'prior_document_count': len(reg.test_originals),
                          'environment': _environment(app), 'health': _health(design)}))
    ''')


def adapter_for(folder):
    class IsolatedAdapter(FusionAdapter):
        receipt_journal_dir = str(folder / "receipts")

        def execute_typed_script(self, script, **kwargs):
            envelope = run_typed(self.execute_script, script, self.url, self.timeout,
                                 journal_dir=self.receipt_journal_dir, **kwargs)
            result = parse_status_json(envelope) or {}
            if result.get("owned") is True and result.get("target_handle"):
                path = folder / "private-targets.json"
                handles = json.loads(path.read_text()) if path.exists() else []
                if result["target_handle"] not in handles:
                    handles.append(result["target_handle"])
                    path.write_text(json.dumps(handles), encoding="utf-8")
            return envelope
    return IsolatedAdapter


def edit_contract(handle, before, after, protected, expected_width=None):
    return dict(target_handle=handle, operation_id=str(uuid.uuid4()), parameter_name="width",
                expression=f"{after} mm", expected_expression=f"{before} mm", expected_value_mm=before,
                value_tolerance_mm=0.01,
                requirements=[check("width", "body_extent", ["root / Editable"], before,
                                    after if expected_width is None else expected_width, "x"),
                              check("volume", "body_volume", ["root / Editable"], before * 150, after * 150)],
                protected=protected)


def stage_edits(adapter, folder, state, record):
    root, handle = folder / "project", state["target_handle"]
    replay = checked(workspace.edit(adapter, str(root), state["contract"]))
    assert replay.get("historical") is True, replay
    current = checked(workspace.inspect_target(adapter, handle, "width", state["requirements"]))
    assert current["measurements"]["width"]["value"] == 70
    record("duplicate_operation_returns_history", redispatched=False, current_width_mm=70)

    stale = edit_contract(handle, 60, 75, state["protected"])
    refused = failed_outcome(workspace.edit(adapter, str(root), stale))
    assert refused["execution"] == "refused" and refused["mutation_state"] == "not_started", refused
    record("stale_parameter_refused", mutation_state=refused["mutation_state"])

    missing = edit_contract(handle, 70, 75, state["protected"])
    missing["requirements"][0]["entities"] = ["root / Missing"]
    refused = failed_outcome(workspace.edit(adapter, str(root), missing))
    assert refused["execution"] == "refused", refused
    assert any(row["status"] == "unknown" for row in refused["checks"]), refused
    record("unresolved_entity_refused", mutation_state=refused["mutation_state"])

    invalid = edit_contract(handle, 70, 0, state["protected"])
    rolled_back = failed_outcome(workspace.edit(adapter, str(root), invalid))
    assert rolled_back["execution"] == "failed" and rolled_back["recovery"]["status"] == "rollback_verified", rolled_back
    current = checked(workspace.inspect_target(adapter, handle, "width", state["requirements"]))
    assert current["measurements"]["width"]["value"] == 70 and current["parameter"]["expression"] == "70 mm", current
    record("invalid_geometry_targeted_rollback", recovery=rolled_back["recovery"], resulting_width_mm=70)

    failed = failed_outcome(workspace.edit(adapter, str(root), edit_contract(handle, 70, 75, state["protected"], 80)))
    assert failed["execution"] == "completed" and failed["verification"] == "failed", failed
    assert failed["failed_requirement_ids"] == ["width"], failed
    record("failed_requirement_retains_candidate", checks=failed["checks"])

    script = "def run(ctx):\n    ctx['design'].userParameters.itemByName('width').expression = '70 mm'\n"
    scripted = checked(workspace.edit(adapter, str(root), edit_contract(handle, 75, 70, state["protected"]), script))
    assert scripted["verification"] == "verified", scripted
    record("custom_python_with_trusted_readback", checks=scripted["checks"])

    reset = checked(workspace.edit(adapter, str(root), edit_contract(handle, 70, 60, state["protected"])))
    assert reset["verification"] == "verified", reset
    latencies = []
    for _ in range(10):
        for before, after in ((60, 61), (61, 60)):
            observed = checked(workspace.edit(adapter, str(root), edit_contract(handle, before, after, state["protected"])))
            assert observed["verification"] == "verified", observed
            latencies.append(observed["timings_ms"]["feedback_total"])
    p95 = sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
    assert p95 < 10000, latencies
    record("small_fixture_feedback_latency", samples_ms=latencies, p95_ms=p95, declared_budget_ms=10000,
           setup_and_archive_excluded=True)
    export_path = root / "assets" / "seed.f3d"
    exported = workspace._call(adapter, "export", {"target_handle": handle, "path": str(export_path)}, str(uuid.uuid4()))
    assert exported["bytes"] > 0
    state["seed_hash"] = exported["sha256"]
    (folder / "private-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    record("seed_exported", bytes=exported["bytes"])


def stage_builds(adapter, folder, state, record):
    root, handle = folder / "project", state["target_handle"]
    seed_checks = edit_contract(handle, 60, 60, state["protected"])["requirements"]
    checkpoint = checked(workspace.checkpoint(adapter, str(root), handle, "seed", seed_checks,
                                               "width", accepted=True))
    assert checkpoint["roundtrip_passed"] and checkpoint["checkpoint_kind"] == "accepted", checkpoint
    state["checkpoint_handle"] = checkpoint["target_handle"]
    record("accepted_checkpoint_roundtrip", bytes=checkpoint["bytes"], checks=checkpoint["checks"])
    manifest = {"schema": 1, "seed": "assets/seed.f3d",
                "edit": {key: state["contract"][key] for key in ("parameter_name", "expression", "expected_expression",
                                                               "expected_value_mm", "value_tolerance_mm")},
                "requirements": state["requirements"], "protected": state["protected"]}
    def write_manifest():
        (root / "design.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_manifest()
    started = time.perf_counter()
    first = checked(workspace.build(adapter, str(root)))
    miss_ms = (time.perf_counter() - started) * 1000
    assert not first["cache_hit"] and first["cacheable"] and first["construction_calls"] == 1, first
    started = time.perf_counter()
    second = checked(workspace.build(adapter, str(root), branch="experiment"))
    hit_ms = (time.perf_counter() - started) * 1000
    assert second["cache_hit"] and second["construction_calls"] == 0, second
    assert second["artifact_hash"] == first["artifact_hash"] and second["build_key"] == first["build_key"], second
    record("verified_build_reused", miss_total_ms=miss_ms, hit_total_ms=hit_ms,
           same_archive=True, hit_construction_calls=0, hit_checks=second["checks"])
    state["build_handles"] = [first["target_handle"], second["target_handle"]]
    accepted_before = next(row for row in checked(workspace.status(adapter, str(root)))["checkpoints"]
                           if row["branch"] == "main" and row["kind"] == "accepted")
    manifest["requirements"][0]["expected"] = 80
    write_manifest()
    failed = failed_outcome(workspace.build(adapter, str(root)))
    assert failed["cache_hit"] and failed["construction_calls"] == 0 and failed["verification"] == "failed", failed
    assert failed["build_key"] == first["build_key"] and failed["verifier_key"] != first["verifier_key"], failed
    accepted_after = next(row for row in checked(workspace.status(adapter, str(root)))["checkpoints"]
                          if row["branch"] == "main" and row["kind"] == "accepted")
    assert accepted_before == accepted_after
    record("changed_checks_reverify_cached_geometry", construction_calls=0, verification="failed",
           accepted_pointer_preserved=True, checks=failed["checks"])
    manifest["requirements"][0]["expected"] = 65
    manifest["requirements"][1]["expected"] = 9750
    manifest["edit"]["expression"] = "65 mm"
    write_manifest()
    changed = checked(workspace.build(adapter, str(root), branch="changed-parameter"))
    assert not changed["cache_hit"] and changed["build_key"] != first["build_key"], changed
    record("changed_parameter_rebuilds", construction_calls=changed["construction_calls"], checks=changed["checks"])
    manifest["requirements"][0]["expected"] = 64
    manifest["requirements"][1]["expected"] = 9600
    manifest["edit"]["expression"] = "64 mm"
    manifest["script"] = "recipe.py"
    (root / "recipe.py").write_text("def run(ctx):\n    ctx['design'].userParameters.itemByName('width').expression = '64 mm'\n", encoding="utf-8")
    write_manifest()
    for _ in range(2):
        custom = checked(workspace.build(adapter, str(root), branch="custom-script"))
        assert not custom["cache_hit"] and not custom["cacheable"] and custom["verification"] == "verified", custom
    record("custom_recipe_checked_without_cache_admission", repeated_construction_calls=2,
           verification=custom["verification"], cacheable=custom["cacheable"])
    ongoing = custom["target_handle"]
    restored = checked(workspace.restore(adapter, str(root), "main"))
    assert restored["target_handle"] != ongoing and restored["verification"] == "not_run", restored
    restored_check = checked(workspace.inspect_target(adapter, restored["target_handle"], "width", seed_checks))
    assert restored_check["measurements"]["width"]["value"] == 70
    workspace._call(adapter, "activate", {"target_handle": ongoing}, str(uuid.uuid4()))
    ongoing_check = checked(workspace.inspect_target(adapter, ongoing, "width", seed_checks))
    assert ongoing_check["measurements"]["width"]["value"] == 64
    workspace._call(adapter, "activate", {"target_handle": handle}, str(uuid.uuid4()))
    source_check = checked(workspace.inspect_target(adapter, handle, "width", seed_checks))
    assert source_check["measurements"]["width"]["value"] == 60
    record("restore_preserves_source_and_work_in_progress", restored_width_mm=70, ongoing_width_mm=64,
           original_source_width_mm=60, fresh_documents=True)
    state.update(restored_handle=restored["target_handle"], ongoing_handle=ongoing)
    (folder / "private-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def stage_guards(adapter, folder, state, record):
    root = folder / "project"
    fresh = checked(workspace.restore(adapter, str(root), "seed"))["target_handle"]
    inactive = workspace.edit(adapter, str(root), edit_contract(state["target_handle"], 60, 65, state["protected"]))
    assert not inactive.ok and inactive.error == "target_not_active", inactive.to_dict()
    rebound = checked(workspace.bind(adapter))
    refused = workspace.edit(adapter, str(root), edit_contract(rebound["target_handle"], 60, 65, state["protected"]))
    assert not refused.ok and refused.error == "user_document_edit_refused", refused.to_dict()
    record("wrong_active_target_and_unowned_binding_refused", no_geometry_edit=True)
    native(adapter, f'''
        entry=_resolve(app,{fresh!r})
        _active(app,entry)
        source=_resolve(app,{state['target_handle']!r})
        assert source['owned'] and not source['document'].isSaved and not entry['document'].isSaved
        _registry().test_document_names=[(source['document'],source['document'].name),(entry['document'],entry['document'].name)]
        source['document'].name='Workspace Ambiguous Test'
        entry['document'].name='Workspace Ambiguous Test'
        print(json.dumps({{'ok':True}}))
    ''')
    ambiguous = workspace.bind(adapter, "Workspace Ambiguous Test")
    assert not ambiguous.ok and ambiguous.error == "document_not_unique", ambiguous.to_dict()
    native(adapter, f'''
        entry=_resolve(app,{fresh!r})
        _active(app,entry)
        assert entry['design'].userParameters.itemByName('width').expression=='60 mm'
        for doc,name in _registry().test_document_names: doc.name=name
        print(json.dumps({{'ok':True}}))
    ''')
    record("ambiguous_document_name_refused", no_parameter_change=True)
    workspace._call(adapter, "close", {"target_handle": fresh}, str(uuid.uuid4()))
    stale = workspace.inspect_target(adapter, fresh, "width", state["requirements"])
    assert not stale.ok and stale.error == "target_invalid", stale.to_dict()
    record("closed_native_target_refused", identity_reconstruction=False)
    workspace._call(adapter, "activate", {"target_handle": state["target_handle"]}, str(uuid.uuid4()))


def stage_timeout(folder, state, record):
    root = folder / "project"
    with adapter_for(folder)() as adapter:
        # Warm discovery/handshake before imposing the mutation's real deadline.
        checked(workspace.inspect_target(adapter, state["target_handle"], "width", state["requirements"]))
        adapter.timeout = 0.2
        contract = edit_contract(state["target_handle"], 60, 61, state["protected"])
        custom = "import time\ndef run(ctx):\n    time.sleep(1.2)\n    ctx['design'].userParameters.itemByName('width').expression='61 mm'\n"
        started = time.perf_counter()
        result = workspace.edit(adapter, str(root), contract, custom)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert not result.ok and (result.result or {}).get("execution") == "unknown", result.to_dict()
        blocked = workspace.edit(adapter, str(root), edit_contract(state["target_handle"], 60, 62, state["protected"]))
        assert not blocked.ok and blocked.error == "workspace_write_blocked", blocked.to_dict()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        receipt = get_status(contract["operation_id"], journal_dir=folder / "receipts")
        if receipt.ok and receipt.result["status"] == "completed":
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Timed mutation has no clean terminal receipt; retain fixture and inspect")
    with adapter_for(folder)() as adapter:
        recovered = checked(workspace.recover(adapter, str(root), contract["operation_id"]))
        assert recovered["verification"] == "verified" and recovered["replayed"] is False, recovered
        job = checked(workspace.status(adapter, str(root), contract["operation_id"]))["job"]
        assert job["state"] == "uncertain" and job["resolved_by"], job
        followup = checked(workspace.edit(adapter, str(root), edit_contract(state["target_handle"], 61, 60, state["protected"])))
        assert followup["verification"] == "verified", followup
        record("real_timeout_late_completion_recovered", bounded_response_ms=elapsed_ms,
               retry_blocked=True, recovered_without_replay=True, original_uncertainty_retained=True,
               subsequent_edit_verified=True)


def stage_partial_failure(adapter, folder, state, record):
    artifact = workspace._store(folder / "project").import_artifact(folder / "project/assets/seed.f3d")
    opened = workspace._open(adapter, artifact, str(uuid.uuid4()))
    handle = opened["target_handle"]
    fault_folder = folder / "partial-failure"
    fault_folder.mkdir()
    checked(workspace.initialize(str(fault_folder / "project")))
    contract = edit_contract(handle, 60, 65, state["protected"])
    # Deliberately synchronous, fixture-only partial edit; no queued callbacks.
    script = """def run(ctx):
    ctx['design'].userParameters.itemByName('width').expression='65 mm'
    ctx['root'].bRepBodies.itemByName('Protected').name='Protected partial'
    raise RuntimeError('intentional fixture partial failure')
"""
    with adapter_for(fault_folder)() as fault_adapter:
        outcome = workspace.edit(fault_adapter, str(fault_folder / "project"), contract, script)
        assert not outcome.ok and outcome.result["execution"] == "unknown", outcome.to_dict()
        terminal = checked(get_status(contract["operation_id"], journal_dir=fault_folder / "receipts"))
        assert terminal["status"] == "declared_failure_unreconciled", terminal
        assert terminal["declared_result"]["recovery"]["status"] == "unresolved_partial", terminal
        native_blocked = fault_adapter.execute_typed_script("import json\ndef run(ctx):\n    print(json.dumps({'ok':True}))\n")
        assert not native_blocked.ok and native_blocked.error == "receipt_barrier", native_blocked.to_dict()
        blocked = workspace.edit(fault_adapter, str(fault_folder / "project"), edit_contract(handle, 60, 66, state["protected"]))
        assert not blocked.ok and blocked.error == "workspace_write_blocked", blocked.to_dict()
        recovery = workspace.recover(fault_adapter, str(fault_folder / "project"), contract["operation_id"])
        assert not recovery.ok and recovery.error == "execution_still_uncertain", recovery.to_dict()
        generic = reconcile_operation(fault_adapter, contract["operation_id"], "not-a-document-identity", True,
                                      "This fixture intentionally tests refusal of generic managed reconciliation")
        assert not generic.ok and generic.error == "workspace_reconciliation_required", generic.to_dict()
        current = checked(workspace.inspect_target(fault_adapter, handle, "width", state["requirements"]))
        assert current["parameter"]["expression"] == "60 mm", current
        status = checked(workspace.status(fault_adapter, str(fault_folder / "project")))
        assert not status["checkpoints"] and status["jobs"][0]["state"] == "uncertain", status
    witness = native(adapter, f'''
        entry=_resolve(app,{handle!r})
        assert entry['owned'] and entry['pending']=={contract['operation_id']!r}
        assert entry['root'].bRepBodies.itemByName('Protected partial') is not None
        assert entry['root'].bRepBodies.itemByName('Protected') is None
        assert all(doc.isValid and _registry().test_signature(doc)==sig for doc,sig in _registry().test_originals)
        print(json.dumps({{'ok':True,'width_restored_mm':entry['design'].userParameters.itemByName('width').value*10,
                          'partial_name_change_remains':True}}))
    ''', read_only=True)
    record("partial_custom_failure_is_fenced", width_restored_mm=witness["width_restored_mm"],
           partial_change_remains=True, recovery="unresolved_partial", retries_blocked=True,
           generic_reconciliation_refused=True, receipt_barrier=True, terminal_failure_confirmed=True,
           checkpoint_count=0)
    # Test harness cleanup of this deliberately damaged disposable copy only.
    # This does not resolve the fault ledger, advertise rollback, or unblock it.
    native(adapter, f'''
        entry=_resolve(app,{handle!r})
        assert entry['owned'] and not entry['document'].isSaved and entry['pending']=={contract['operation_id']!r}
        assert all(entry['document']!=doc for doc,sig in _registry().test_originals)
        assert entry['root'].bRepBodies.itemByName('Protected partial') is not None
        assert entry['document'].close(False)
        print(json.dumps({{'ok':True}}))
    ''')
    with adapter_for(fault_folder)() as fault_adapter:
        remaining = checked(workspace.status(fault_adapter, str(fault_folder / "project"), contract["operation_id"]))
        assert remaining["job"]["state"] == "uncertain" and not remaining["job"]["resolved_by"], remaining
        assert remaining["receipt"]["status"] == "declared_failure_unreconciled", remaining
    record("partial_fixture_disposed_by_test_harness", automatic_recovery=False, uncertain_ledger_retained=True)


def cleanup(adapter, folder, record):
    handles = json.loads((folder / "private-targets.json").read_text())
    result = native(adapter, f'''
        reg=_registry()
        assert all(doc.isValid and reg.test_signature(doc)==sig for doc,sig in reg.test_originals)
        closed=0
        for handle in {handles!r}:
            epoch,token=handle.split(':',1)
            entry=reg.entries.get(token)
            if epoch!=reg.epoch or entry is None or not _live(entry): continue
            assert entry['owned'] and not entry['document'].isSaved and entry['pending'] is None
            assert all(entry['document']!=doc for doc,sig in reg.test_originals)
            assert entry['document'].close(False)
            closed+=1
        assert all(doc.isValid and reg.test_signature(doc)==sig for doc,sig in reg.test_originals)
        assert app.documents.count==len(reg.test_originals)
        assert reg.test_previous.activate()
        for key in ('test_owned','test_originals','test_previous','test_signature'):
            delattr(reg,key)
        print(json.dumps({{'ok':True,'owned_documents_closed':closed,'original_document_count':app.documents.count,
                          'originals_intact':True,'original_active_restored':True}}))
    ''')
    record("original_documents_preserved", **{key: value for key, value in result.items() if key != "ok"})


def run(folder):
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "report.json").exists() or (folder / "private-targets.json").exists():
        raise ValueError("Use a fresh folder; inspect any retained fixture before another run")
    evidence = []
    def record(name, **data):
        evidence.append(dict(name=name, status="passed", evidence=data))
        (folder / "report.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence[-1]), flush=True)

    with adapter_for(folder)() as adapter:
        created = fixture(adapter)
        handle = created["target_handle"]
        (folder / "private-state.json").write_text(json.dumps({"target_handle": handle}), encoding="utf-8")
        record("fixture_created", prior_document_count=created["prior_document_count"],
               environment=created["environment"], health=created["health"])
        root = folder / "project"
        checked(workspace.initialize(str(root)))
        requirements = [check("width", "body_extent", ["root / Editable"], 60, 70, "x"),
                        check("volume", "body_volume", ["root / Editable"], 9000, 10500)]
        protected = [check("canary", "body_volume", ["root / Protected"], 1000),
                     check("pose", "occurrence_translation", ["PCB:1"], 20, axis="x")]
        observed = checked(workspace.inspect_target(adapter, handle, "width", requirements))
        assert abs(observed["measurements"]["width"]["value"] - 60) < 0.01, observed
        record("baseline_brep", width_mm=observed["measurements"]["width"]["value"],
               volume_mm3=observed["measurements"]["volume"]["value"], parameter=observed["parameter"])
        contract = dict(target_handle=handle, operation_id=str(uuid.uuid4()), parameter_name="width",
                        expression="70 mm", expected_expression="60 mm", expected_value_mm=60,
                        value_tolerance_mm=0.01, requirements=requirements, protected=protected)
        edited = checked(workspace.edit(adapter, str(root), contract))
        assert edited["verification"] == "verified", edited
        record("parameter_propagation", checks=edited["checks"], timings_ms=edited["timings_ms"])
        state = dict(target_handle=handle, contract=contract, requirements=requirements, protected=protected)
        (folder / "private-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        stage_edits(adapter, folder, state, record)
        stage_builds(adapter, folder, state, record)
        stage_guards(adapter, folder, state, record)
    stage_timeout(folder, state, record)
    with adapter_for(folder)() as adapter:
        stage_partial_failure(adapter, folder, state, record)
        cleanup(adapter, folder, record)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--folder", type=Path)
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run to create disposable live Fusion fixtures")
    run(args.folder or Path(tempfile.mkdtemp(prefix="fusion-workspace-live-")))
