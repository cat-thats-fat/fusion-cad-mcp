"""Local orchestration of guarded edits and immutable native checkpoints.

Fusion is the only model evaluator. Git tracks source; the local index tracks
attempts, native artifacts and explicitly scoped evidence. No implicit Undo,
retry, user-document close, cloud save, or Git commit occurs here.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import math
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import edit_contract, receipts, workspace_native
from .edit_contract import (
    _contract_finite,
    _contract_measurement,
    _prepare_contract_check,
    compare_baseline,
    compare_evidence,
    prepare_edit_contract,
    within_tolerance,
)
from .envelope import Envelope, parse_status_json
from .execution import execute_typed_script
from .receipts import get_status
from .scoped_evidence import compare_scoped_evidence, prepare_evidence_scope
from .workspace_store import WorkspaceError, WorkspaceStore, content_key

SCHEMA_VERSION = 1
_SOURCE_ACTIONS = {"source_edit_requested": False, "source_close_requested": False}


class NativeFailure(RuntimeError):
    def __init__(self, envelope, *, read_only=False, operation_id=None, local=False):
        self.envelope = envelope
        self.read_only = read_only
        self.operation_id = operation_id
        self.local = local
        self.phase = None
        self.effect_scope = None
        self.confirmed = {}
        self.artifact_hash = None
        self.staging_output = None
        super().__init__(envelope.error or "native operation failed")

    @property
    def not_started(self):
        return ((self.envelope.result or {}).get("mutation_state") == "not_started"
                or (self.envelope.state or {}).get("execution_outcome") == "not_started"
                or self.envelope.error == "receipts_unavailable")

    def phase_failure(self):
        outcome = "failed" if self.local else "not_started" if self.not_started else "unknown"
        result = {"phase": self.phase, "effect_scope": self.effect_scope, "outcome": outcome,
                  "operation_id": self.operation_id, "error": self.envelope.error}
        if self.staging_output:
            result["staging_output"] = self.staging_output
        return result


@contextmanager
def _phase(name, effect_scope, *, confirmed=None, artifact_hash=None):
    try:
        yield
    except NativeFailure as exc:
        exc.phase = name
        exc.effect_scope = effect_scope
        exc.confirmed = confirmed or {}
        exc.artifact_hash = artifact_hash
        raise


def _error(code, message, **result):
    return Envelope(ok=False, error=code, message=str(message)[:400], result=result or None)


def _root(path):
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("workspace_path must be absolute on the Fusion host")
    candidate = candidate.resolve()
    if not (candidate / "design.json").is_file():
        raise ValueError("Initialize a design workspace first")
    return candidate


def _store(path):
    return WorkspaceStore(_root(path) / ".fusion")


def _op(value=None):
    result = str(uuid.uuid4()) if value is None else value
    if not isinstance(result, str) or str(uuid.UUID(result)) != result:
        raise ValueError("operation_id must be a canonical UUID")
    return result


def _child(operation_id, phase):
    return str(uuid.uuid5(uuid.UUID(operation_id), phase))


def _call(adapter, action, payload, operation_id=None, *, recovery_probe=False):
    script = workspace_native.build_workspace_script(action, payload)
    read_only = action in ("bind", "inspect")
    env = execute_typed_script(
        adapter, script, read_only=read_only,
        operation_id=operation_id, recovery_probe=recovery_probe,
        receipt_context={"kind": "workspace", "target_handle": payload.get("target_handle", "unbound"),
                         "action": action},
    )
    data = parse_status_json(env)
    if data is None and isinstance(env.result, dict) and env.result.get("ok") is False:
        # A terminal refusal from the journal has ok=False on the envelope too.
        data = env.result
    if data is None:
        raise NativeFailure(Envelope(ok=False, error=env.error or "workspace_result_unknown",
                                     message=env.message, state=env.state), read_only=read_only,
                            operation_id=operation_id)
    if not data.get("ok"):
        raise NativeFailure(Envelope(ok=False, error=data.get("error", "workspace_native_failed"),
                                     message=data.get("detail", ""), state=env.state, result=data),
                            read_only=read_only, operation_id=operation_id)
    data["receipt"] = env.state or {}
    return data


def _checks(checks, *, inspection=False):
    if not isinstance(checks, list) or len(checks) > 32:
        raise ValueError("checks must be a list of at most 32 measurements")
    result = []
    for index, value in enumerate(checks):
        if not isinstance(value, dict):
            raise ValueError("Each check must be an object")
        if inspection:
            value = {"baseline": 0, "baseline_tolerance": 0, "tolerance": 0,
                     "comparator": "eq", "expected": 0, **value}
        result.append(_prepare_contract_check(value, f"checks[{index}]", protected=False))
    if len({row["id"] for row in result}) != len(result):
        raise ValueError("Check IDs must be unique")
    return result


def _inspect(adapter, handle, parameter, checks, operation_id=None, *, expected_expression=None,
             recovery_probe=False, scope=None):
    payload = {"target_handle": handle, "parameter_name": parameter, "checks": checks}
    if expected_expression is not None:
        payload["expected_expression"] = expected_expression
    if scope is not None:
        payload["scope"] = scope
    return _call(adapter, "inspect", payload, operation_id, recovery_probe=recovery_probe)


def _snapshot_checks(checks, observations):
    """Evaluate snapshot requirements using a freshly measured baseline."""
    normalized = []
    interference = set()
    for row in checks:
        value, _, _, count = _contract_measurement(row, observations)
        normalized.append({**row, "baseline": value if value is not None else row["baseline"]})
        if count:
            interference.add(row["id"])
    result = compare_evidence({"requirements": normalized, "protected": []}, observations, observations,
                              execution="completed")
    if interference:
        for row in result["checks"]:
            if row["id"] in interference:
                row.update(status="failed", reason="interference_detected")
        result["failed_requirement_ids"] = [row["id"] for row in result["checks"] if row["status"] == "failed"]
        result["unknown_requirement_ids"] = [row["id"] for row in result["checks"] if row["status"] == "unknown"]
        result["verification"] = "failed"
    return result


def _healthy(info):
    health = info.get("health", {})
    return isinstance(health, dict) and all(
        type(health.get(key)) is int and health[key] == 0 for key in ("warning", "error", "unknown")
    )


def _same_parameter(before, after, tolerance_mm=1e-7):
    return (
        isinstance(before, dict) and isinstance(after, dict)
        and all(isinstance(before.get(key), str) and before[key] and before[key] == after.get(key)
                for key in ("expression", "units"))
        and _contract_finite(before.get("value_mm")) and _contract_finite(after.get("value_mm"))
        and within_tolerance(after["value_mm"], before["value_mm"], tolerance_mm)
    )


def _pose_cells_available(matrix):
    return isinstance(matrix, list) and len(matrix) == 16 and all(_contract_finite(cell) for cell in matrix)


def _same_pose(before, after):
    # Match the native protected-pose guard on all 16 matrix cells, in Fusion's
    # native units. The reported scalar still uses its declared tolerance in mm.
    return (
        _pose_cells_available(before) and _pose_cells_available(after)
        and all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8)
                for a, b in zip(before, after, strict=True))
    )


def _pose_mismatches(checks, before, after):
    return [row["id"] for row in checks if row["metric"] == "occurrence_translation"
            and not _same_pose(before.get(row["id"], {}).get("matrix"),
                               after.get(row["id"], {}).get("matrix"))]


def _compare_workspace_evidence(contract, baseline, observed):
    """Compare every protected pose directly to its original bound baseline."""
    comparison = compare_evidence(contract, baseline, observed, execution="completed")
    protected_poses = {spec["id"] for spec in contract["protected"]
                       if spec["metric"] == "occurrence_translation"}
    if not protected_poses:
        return comparison
    for row in comparison["checks"]:
        key = row["id"]
        if key not in protected_poses or row["status"] != "passed":
            continue
        before = (baseline or {}).get(key, {}).get("matrix")
        after = (observed or {}).get(key, {}).get("matrix")
        if not _pose_cells_available(before) or not _pose_cells_available(after):
            row.update(status="unknown", reason="protected_pose_unavailable")
        elif not _same_pose(before, after):
            row.update(status="failed", reason="protected_pose_changed")
    failed = [row["id"] for row in comparison["checks"] if row["status"] == "failed"]
    unknown = [row["id"] for row in comparison["checks"] if row["status"] == "unknown"]
    return {**comparison, "failed_requirement_ids": failed, "unknown_requirement_ids": unknown,
            "verification": "failed" if failed else "incomplete" if unknown else "verified"}


def _roundtrip(checks, before, after):
    first, second = before["measurements"], after["measurements"]
    mismatches = set(_pose_mismatches(checks, first, second))
    for row in checks:
        a, _, _, count_a = _contract_measurement(row, first)
        b, _, _, count_b = _contract_measurement(row, second)
        if a is None or b is None or count_a != count_b or not within_tolerance(b, a, row["tolerance"]):
            mismatches.add(row["id"])
    result = {
        "parameter_matches": _same_parameter(before["parameter"], after["parameter"]),
        "witness_mismatches": sorted(mismatches),
        "healthy": _healthy(before) and _healthy(after),
    }
    if "recompute" in after:
        result["recompute"] = after["recompute"]
    return result


def _roundtrip_passed(evidence):
    return evidence["parameter_matches"] and evidence["healthy"] and not evidence["witness_mismatches"]


def _verification(*outcomes, complete):
    if "failed" in outcomes:
        return "failed"
    return "verified" if complete and all(value == "verified" for value in outcomes) else "incomplete"


def _executor_digest():
    return content_key({
        "native": workspace_native.runtime_digest(),
        "coordinator_source": inspect.getsource(sys.modules[__name__]),
        "receipt_source": inspect.getsource(receipts),
        "receipt_runtime": receipts._RUNTIME,
    })


def _verifier(checks, protected=None):
    return content_key({"schema": SCHEMA_VERSION, "requirements": checks, "protected": protected or [],
                        "executor": _executor_digest()})


def _environment(native):
    return {**native["environment"], "workspace_schema": SCHEMA_VERSION,
            "executor_sha256": _executor_digest()}


def initialize(workspace_path):
    try:
        root = Path(workspace_path)
        if not root.is_absolute():
            raise ValueError("workspace_path must be absolute")
        root.mkdir(parents=True, exist_ok=True)
        manifest = root / "design.json"
        if not manifest.exists():
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump({"schema": SCHEMA_VERSION, "seed": "assets/seed.f3d", "edit": {},
                           "requirements": [], "protected": []}, stream, indent=2)
                stream.write("\n")
        (root / "assets").mkdir(exist_ok=True)
        ignore = root / ".gitignore"
        text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        if ".fusion/" not in text.splitlines():
            with ignore.open("a", encoding="utf-8") as stream:
                stream.write(("\n" if text and not text.endswith("\n") else "") + ".fusion/\n")
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
        WorkspaceStore(root / ".fusion")
        return Envelope(ok=True, result={"initialized": True, "schema": SCHEMA_VERSION,
                                        "manifest": "design.json", "git_committed": False})
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return _error("workspace_initialization_failed", exc)


def bind(adapter, document_name=None):
    try:
        if document_name is not None and (not isinstance(document_name, str) or not document_name.strip()):
            raise ValueError("document_name must be an exact nonempty name")
        return Envelope(ok=True, result=_call(adapter, "bind", {"document_name": document_name}))
    except NativeFailure as exc:
        return exc.envelope
    except (ValueError, OSError) as exc:
        return _error("invalid_input", exc)


def inspect_target(adapter, target_handle, parameter_name, checks, scope=None, baseline=None):
    try:
        if scope is not None:
            scope = prepare_evidence_scope(scope)
        elif baseline is not None:
            raise ValueError("baseline requires scope")
        result = _inspect(adapter, target_handle, parameter_name,
                          _checks(checks, inspection=True), scope=scope)
        if scope is not None:
            result["evidence_status"] = compare_scoped_evidence(scope, result.get("evidence"), result.get("evidence"))
        if baseline is not None:
            result["evidence_comparison"] = compare_scoped_evidence(scope, baseline, result.get("evidence"))
        return Envelope(ok=True, result=result)
    except NativeFailure as exc:
        return exc.envelope
    except (ValueError, OSError, TypeError) as exc:
        return _error("invalid_input", exc)


def _public_receipt(receipt):
    return {key: receipt[key] for key in ("operation_id", "receipt_status", "barrier_operation_id")
            if receipt and key in receipt}


def _public_response(result):
    """Project scoped feedback from the fuller diagnostic record kept in the job ledger."""
    public = {key: value for key, value in result.items()
              if key not in {"ok", "baseline", "result", "environment", "native",
                             "receipt", "construction", "diagnostics", "rollback_baseline"}}
    native = result.get("native") or {}
    if "mutation_state" in native and "mutation_state" not in public:
        public["mutation_state"] = native["mutation_state"]
    receipt = _public_receipt(result.get("receipt"))
    if receipt:
        public["receipt"] = receipt
    for key in ("readback", "edit_readback"):
        if isinstance(public.get(key), dict):
            readback = {name: value for name, value in public[key].items() if name != "receipt"}
            receipt = _public_receipt(public[key].get("receipt"))
            if receipt:
                readback["receipt"] = receipt
            public[key] = readback
    inline = result.get("construction")
    if isinstance(inline, dict):
        construction = {key: inline[key] for key in ("execution", "verification") if key in inline}
        if "parameter" in inline and inline["parameter"] != public.get("parameter"):
            construction["parameter"] = inline["parameter"]
        current = {row["id"]: row.get("result") for row in public.get("checks", [])}
        different = {
            row["id"]: row["result"] for row in inline.get("checks", [])
            if current.get(row["id"]) is not None and row.get("result") != current[row["id"]]
        }
        if different:
            construction["different_check_values"] = different
        public["construction"] = construction
    health = public.get("health")
    if isinstance(health, dict) and "transaction" in health:
        public["health"] = {key: health[key] for key in ("baseline", "result") if key in health}
    recovery = public.get("recovery") or native.get("recovery")
    if isinstance(recovery, dict):
        summary = {"status": recovery.get("status")}
        details = recovery.get("details") or {}
        if "status" in details:
            summary["rollback_status"] = details["status"]
        if "witnesses" in details:
            summary["witness_status"] = details["witnesses"].get("status")
        if details.get("problems"):
            summary["problem_count"] = len(details["problems"])
        public["recovery"] = summary
    return public


def _recorded(job):
    result = (job.get("evidence") or {}).get("response", {})
    if job["state"] in ("running", "uncertain"):
        return _error("workspace_execution_uncertain", "Inspect status and the phase receipt before further writes",
                      **{**_public_response(result), "historical": True, "operation_id": job["operation_id"],
                         "state": job["state"], "resolved_by": job.get("resolved_by")})
    successful = job["state"] == "completed" and result.get("verification") not in ("failed", "incomplete")
    return Envelope(ok=successful, error=None if successful else result.get("error", "workspace_check_failed"),
                    result={**_public_response(result), "historical": True, "operation_id": job["operation_id"],
                            "publication": "Inspect current checkpoint pointers; receipt replay does not republish"})


def _managed(adapter, workspace_path, action, request, operation_id, worker):
    """One process-safe coordinator lane; never re-dispatch an existing job."""
    try:
        store, operation_id = _store(workspace_path), _op(operation_id)
        fingerprint = content_key({"action": action, "request": request})
        with store.writer_lock():
            previous = store.get_job(operation_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    return _error("operation_conflict", "Operation ID belongs to another request")
                return _recorded(previous)
            unresolved = store.unresolved_jobs()
            if unresolved:
                return _error("workspace_write_blocked", "A prior job is unresolved; inspect its receipt before further writes",
                              operation_ids=[job["operation_id"] for job in unresolved][:32])
            metadata = {"action": action, **request}
            store.start_job(operation_id, fingerprint, metadata)
            try:
                result, artifact, proof, publication = worker(store, operation_id)
                execution = result.get("execution", "completed")
                state = "completed" if execution == "completed" else "failed" if execution in ("refused", "failed") else "uncertain"
                proof = {**(proof or {}), "response": result}
                store.finish_job(operation_id, "running", state, proof, artifact)
                publication_info = {}
                if publication and state == "completed":
                    try:
                        publication_info = {"publication": publication(store, operation_id)}
                    except Exception as exc:
                        try:
                            pointers = [row for row in store.list_checkpoints() if row["operation_id"] == operation_id]
                        except Exception:
                            pointers = None
                        return _error(
                            "workspace_publication_incomplete", type(exc).__name__, **_public_response(result),
                            operation_id=operation_id,
                            publication={"status": "incomplete", "references": pointers},
                        )
                successful = state == "completed" and result.get("verification") not in ("failed", "incomplete")
                return Envelope(ok=successful, error=None if successful else result.get("error", "workspace_check_failed"),
                                result={**_public_response(result), **publication_info, "operation_id": operation_id})
            except NativeFailure as exc:
                env = exc.envelope
                known = exc.not_started or exc.read_only or exc.local
                completed = exc.confirmed.get("execution") == "completed"
                state = ("completed" if completed else "failed") if known else "uncertain"
                execution = ("completed" if completed else "unknown" if not known
                             else "refused" if exc.not_started else "failed")
                response = {**exc.confirmed, "execution": execution,
                            "verification": "incomplete" if completed or not known else "not_run",
                            "error": env.error, "native": env.result, "receipt": env.state,
                            "phase_failure": exc.phase_failure()}
                if not known and exc.effect_scope == "archive_output":
                    response["artifact_state"] = "unconfirmed"
                store.finish_job(operation_id, "running", state, {"response": response}, exc.artifact_hash)
                return Envelope(ok=False, error=env.error, message=env.message, state=env.state,
                                result={**_public_response(response), "operation_id": operation_id})
            except Exception as exc:
                job = store.get_job(operation_id)
                if job and job["state"] == "running":
                    store.finish_job(operation_id, "running", "uncertain",
                                     {"response": {"execution": "unknown", "verification": "incomplete",
                                                   "reason": type(exc).__name__}})
                return _error("workspace_job_incomplete", type(exc).__name__, operation_id=operation_id,
                              next_step="Inspect job and receipt state; do not replay the mutation")
    except (OSError, ValueError, TypeError, TimeoutError) as exc:
        return _error("workspace_refused", exc)


def _compile_recipe(source, label):
    if not isinstance(source, str) or len(source.encode("utf-8")) > 65536:
        raise ValueError("custom_script must be Python text of at most 64 KiB")
    compile(source, label, "exec")
    runs = [node for node in ast.parse(source, label).body
            if isinstance(node, ast.FunctionDef) and node.name == "run"]
    if len(runs) != 1:
        raise ValueError("Custom recipe must define one top-level run(_ctx)")
    args = runs[0].args
    if len(args.posonlyargs) + len(args.args) != 1 or args.kwonlyargs or args.vararg or args.kwarg:
        raise ValueError("Custom recipe run(_ctx) must accept one positional argument")


def _confirm_edit(adapter, contract, declared, operation_id):
    """Require the callback's model witnesses to survive a separate native call."""
    handle = contract["target_handle"]
    witnesses = contract["requirements"] + contract["protected"]
    construction = {key: declared[key] for key in (
        "execution", "verification", "parameter", "checks", "receipt", "result", "health",
    )}
    baseline = declared["baseline"]
    observed = None
    failure = None
    try:
        with _phase("edit-readback", "readback"):
            observed = _inspect(adapter, handle, contract["parameter_name"], witnesses,
                                _child(operation_id, "edit-readback"), expected_expression=contract["expression"])
    except NativeFailure as exc:
        failure = exc.phase_failure()
    measurements = observed.get("measurements") if observed else None
    fresh = _compare_workspace_evidence(contract, baseline, measurements)
    if not isinstance(measurements, dict):
        readback = {"status": "unavailable", "phase_failure": failure}
        verification = "incomplete"
    else:
        details = _roundtrip(
            witnesses,
            {"parameter": declared["parameter"], "measurements": declared["result"],
             "health": declared["health"]["result"]},
            {**observed, "parameter": observed.get("parameter")},
        )
        expected = observed.get("parameter_matches_expected")
        pending_clear = "pending" in observed and observed["pending"] is None
        environment = declared.get("environment")
        same_environment = bool(environment) and observed.get("environment") == environment
        matched = _roundtrip_passed(details) and expected is True and pending_clear and same_environment
        readback = {"status": "matched" if matched else "different_or_incomplete", **details,
                    "parameter_matches_expected": expected, "pending_clear": pending_clear,
                    "same_environment": same_environment, "receipt": observed.get("receipt")}
        verification = _verification(declared["verification"], fresh["verification"], complete=matched)
        if expected is False:
            verification = "failed"
    return {
        **declared, **fresh, "verification": verification,
        "parameter": observed.get("parameter") if observed else None,
        "result": measurements,
        "health": {**declared["health"], "result": observed.get("health") if observed else None},
        "target_handle": handle, "construction": construction, "readback": readback,
    }



def _confirm_rollback(adapter, contract, declared, operation_id):
    """Confirm inline rollback witnesses survive the native callback boundary."""
    if declared.get("execution") != "failed" or (declared.get("recovery") or {}).get("status") != "rollback_verified":
        return declared
    checks = contract["requirements"] + contract["protected"]
    baseline = declared.get("rollback_baseline")
    observed = None
    failure = None
    try:
        with _phase("rollback-readback", "readback"):
            observed = _inspect(adapter, contract["target_handle"], contract["parameter_name"], checks,
                                _child(operation_id, "rollback-readback"),
                                expected_expression=contract["expected_expression"])
    except NativeFailure as exc:
        failure = exc.phase_failure()
    matched = False
    details = {"status": "unavailable", "phase_failure": failure}
    if isinstance(baseline, dict) and isinstance(baseline.get("measurements"), dict) and observed:
        initial = baseline["measurements"]
        fresh = observed.get("measurements") or {}
        baseline_comparison = compare_baseline(contract, fresh)
        # The original-baseline tolerance applies to rollback, not the desired-result tolerance.
        rollback_checks = [{**spec, "tolerance": spec["baseline_tolerance"]} for spec in checks]
        details = _roundtrip(rollback_checks, baseline, observed)
        count_mismatches = []
        for spec in checks:
            if spec["metric"] == "interference":
                before = initial.get(spec["id"], {}).get("interference_count")
                after = fresh.get(spec["id"], {}).get("interference_count")
                if type(before) is not int or type(after) is not int or before < 0 or before != after:
                    count_mismatches.append(spec["id"])
        before_parameter = baseline.get("parameter") or {}
        after_parameter = observed.get("parameter") or {}
        values_match = (_contract_finite(before_parameter.get("value_mm"))
                        and _contract_finite(after_parameter.get("value_mm"))
                        and within_tolerance(after_parameter["value_mm"], before_parameter["value_mm"],
                                             contract["value_tolerance_mm"])
                        and within_tolerance(after_parameter["value_mm"], contract["expected_value_mm"],
                                             contract["value_tolerance_mm"]))
        matched = (baseline_comparison["passed"] and _roundtrip_passed(details) and values_match
                   and not count_mismatches and observed.get("health") == baseline.get("health")
                   and observed.get("parameter_matches_expected") is True
                   and "pending" in observed and observed["pending"] is None
                   and bool(declared.get("environment"))
                   and observed.get("environment") == declared["environment"])
        details.update(status="matched" if matched else "different_or_incomplete",
                       interference_count_mismatches=count_mismatches,
                       baseline_checks_passed=baseline_comparison["passed"],
                       receipt=observed.get("receipt"))
    if matched:
        return {**declared, "rollback_readback": details}
    return {**declared, "execution": "unknown", "verification": "incomplete",
            "recovery": {"status": "unresolved_partial", "inline": declared.get("recovery")},
            "rollback_readback": details}


def edit(adapter, workspace_path, contract, custom_script=None):
    try:
        if not isinstance(contract, dict):
            raise ValueError("contract must be an object")
        if "version" in contract and (type(contract["version"]) is not int or contract["version"] != edit_contract.EDIT_CONTRACT_VERSION):
            raise ValueError("Unsupported edit contract version")
        prepared = prepare_edit_contract(**{key: value for key, value in contract.items() if key != "version"})
        if custom_script is not None:
            _compile_recipe(custom_script, "<workspace-custom>")
    except (ValueError, TypeError, SyntaxError) as exc:
        return _error("invalid_edit_contract", exc)
    request = {"target_handle": prepared["target_handle"], "contract": prepared,
               "custom_script_sha256": hashlib.sha256(custom_script.encode("utf-8")).hexdigest()
               if custom_script is not None else None}
    def worker(store, operation_id):
        start = time.perf_counter()
        payload = {**prepared, **({"custom_script": custom_script} if custom_script is not None else {})}
        with _phase("edit", "model_edit", confirmed={"target_handle": prepared["target_handle"]}):
            result = _call(adapter, "edit", payload, operation_id)
        if result.get("execution") == "completed":
            result = _confirm_edit(adapter, prepared, result, operation_id)
        else:
            result = _confirm_rollback(adapter, prepared, result, operation_id)
        result.setdefault("timings_ms", {})["feedback_total"] = (time.perf_counter() - start) * 1000
        result["scope"] = "Declared checks and protected witnesses; uninspected properties remain unknown"
        result["next_step"] = ("Retain or checkpoint the candidate" if result.get("verification") == "verified"
                               else "Inspect the current bound readback and failed or unknown requirements")
        return result, None, None, None
    return _managed(adapter, workspace_path, "edit", request, prepared["operation_id"], worker)


def _export(adapter, store, handle, operation_id):
    try:
        staging = Path(tempfile.mkdtemp(prefix="export-", dir=store.root))
    except OSError as exc:
        raise NativeFailure(Envelope(ok=False, error="archive_staging_failed", message=str(exc)[:160]),
                            operation_id=operation_id, local=True) from exc
    path = staging / "candidate.f3d"
    preserve_output = False
    try:
        reported = _call(adapter, "export", {"target_handle": handle, "path": str(path)}, operation_id)
        try:
            artifact = store.import_artifact(path)
            if artifact["hash"] != reported["sha256"] or artifact["size"] != reported["bytes"]:
                raise ValueError("Export bytes differ from native receipt")
        except (OSError, ValueError, KeyError) as exc:
            raise NativeFailure(Envelope(ok=False, error="archive_integrity_failed", message=str(exc)[:160]),
                                operation_id=operation_id, local=True) from exc
        return artifact
    except NativeFailure as exc:
        preserve_output = not exc.not_started and not exc.local
        if preserve_output:
            exc.staging_output = str(path)
        raise
    finally:
        if not preserve_output:
            try:
                path.unlink(missing_ok=True)
                staging.rmdir()
            except OSError:
                pass


def _open(adapter, artifact, operation_id):
    return _call(adapter, "open", {"path": artifact["path"], "sha256": artifact["hash"]}, operation_id)


def _reopen_and_inspect(adapter, artifact, operation_id, parameter, checks, *, phase="roundtrip",
                        expected_expression=None, confirmed=None, scope=None):
    opened = None
    stage = "open"
    archive_context = {**(confirmed or {}), "artifact_hash": artifact["hash"], "artifact_state": "available"}
    try:
        with _phase(f"{phase}-open", "working_document_open", confirmed=archive_context,
                    artifact_hash=artifact["hash"]):
            opened = _open(adapter, artifact, _child(operation_id, f"{phase}-open"))
        stage = "recompute"
        with _phase(f"{phase}-recompute", "working_document_mutation",
                    confirmed={**archive_context, "target_handle": opened["target_handle"]},
                    artifact_hash=artifact["hash"]):
            recomputed = _call(adapter, "recompute", {"target_handle": opened["target_handle"]},
                               _child(operation_id, f"{phase}-recompute"))
            if recomputed.get("recomputed") is not True:
                raise NativeFailure(Envelope(ok=False, error="recompute_result_unknown"),
                                    operation_id=_child(operation_id, f"{phase}-recompute"))
        stage = "check"
        with _phase(f"{phase}-check", "readback",
                    confirmed={**archive_context, "target_handle": opened["target_handle"]},
                    artifact_hash=artifact["hash"]):
            observed = _inspect(adapter, opened["target_handle"], parameter, checks,
                                _child(operation_id, f"{phase}-check"), expected_expression=expected_expression,
                                scope=scope)
        observed["recompute"] = {"status": "completed", "target_handle": opened["target_handle"],
                                 "receipt": _public_receipt(recomputed.get("receipt"))}
        return opened, observed, None
    except NativeFailure as exc:
        if (opened is None or stage == "recompute") and not exc.not_started:
            raise
        # A confirmed open or preflight refusal leaves a known archive. A lost
        # read-only probe can withhold verification without fencing writes.
        return opened, None, {"error": exc.envelope.error, "phase_failure": exc.phase_failure()}


def _proof(artifact, verifier, comparison, roundtrip, observed=None):
    rows = comparison["checks"]
    return {"execution": "completed", "artifact_hash": artifact["hash"], "verifier_key": verifier,
            "required_checks": [row["id"] for row in rows],
            "checks": {row["id"]: row["status"] for row in rows},
            "roundtrip": {"status": "passed" if roundtrip else "failed", "artifact_hash": artifact["hash"],
                          "parameter": observed["parameter"] if observed else None}}


def _branch(branch):
    if not isinstance(branch, str) or not 1 <= len(branch) <= 128 or not branch.strip() or not branch.isprintable():
        raise ValueError("branch must be a nonempty printable name of at most 128 characters")


def _pointer(store, branch, kind):
    # Reading the identity of a reference does not consume its archive bytes.
    # Repairing a broken pointer requires a new verified artifact and the same CAS.
    return next((row["artifact_hash"] for row in store.list_checkpoints()
                 if row["branch"] == branch and row["kind"] == kind), None)


def checkpoint(adapter, workspace_path, target_handle, branch, requirements, parameter_name,
               operation_id=None, expected_previous=None, accepted=False, evidence_scope=None):
    try:
        checks = _checks(requirements)
        if evidence_scope is not None:
            evidence_scope = prepare_evidence_scope(evidence_scope)
        _branch(branch)
        if type(accepted) is not bool:
            raise ValueError("accepted must be boolean")
        if not isinstance(target_handle, str) or not target_handle.strip() or not isinstance(parameter_name, str) or not parameter_name.strip():
            raise ValueError("Checkpoint requires a bound target and named parameter")
        if expected_previous is not None and (
            not isinstance(expected_previous, str) or not re.fullmatch("[0-9a-f]{64}", expected_previous)
        ):
            raise ValueError("expected_previous must be a SHA-256 digest or null")
        if accepted and not checks:
            raise ValueError("Accepted checkpoints require explicit requirements")
        operation_id = _op(operation_id)
    except (ValueError, TypeError) as exc:
        return _error("invalid_checkpoint", exc)
    request = dict(target_handle=target_handle, branch=branch, requirements=checks,
                   parameter_name=parameter_name, expected_previous=expected_previous, accepted=accepted)
    if evidence_scope is not None:
        request["evidence_scope"] = evidence_scope
    def worker(store, op):
        requested_kind = "accepted" if accepted else "wip"
        prior_wip = _pointer(store, branch, "wip")
        prior = _pointer(store, branch, "accepted") if accepted else prior_wip
        if prior != expected_previous:
            return {"execution": "refused", "verification": "not_run", "error": "checkpoint_conflict",
                    "expected_previous": expected_previous, "current": prior}, None, None, None
        source = {"target_handle": target_handle, **_SOURCE_ACTIONS}
        with _phase("before", "readback", confirmed=source):
            before = _inspect(adapter, target_handle, parameter_name, checks, _child(op, "before"), scope=evidence_scope)
        initial = _snapshot_checks(checks, before["measurements"])
        source["source_verification"] = initial["verification"]
        with _phase("export", "archive_output", confirmed={**source, "artifact_state": "unavailable"}):
            artifact = _export(adapter, store, target_handle, _child(op, "export"))
        opened, after, failure = _reopen_and_inspect(
            adapter, artifact, op, parameter_name, checks, confirmed={**source, "execution": "completed"},
            scope=evidence_scope,
        )
        result = _snapshot_checks(checks, after["measurements"] if after else {})
        details = _roundtrip(checks, before, after) if after else failure
        if evidence_scope is not None:
            comparison = compare_scoped_evidence(evidence_scope, before.get("evidence"),
                                                 after.get("evidence") if after else None,
                                                 allow_rebind=True)
            details["scoped_evidence"] = comparison
        details.update(source_verification=initial["verification"], archive_verification=result["verification"])
        scope_outcome = ("verified" if evidence_scope is None or comparison["status"] == "passed"
                         else comparison["status"])
        verification = _verification(initial["verification"], result["verification"], scope_outcome,
                                     complete=after is not None and _roundtrip_passed(details))
        passed = verification == "verified"
        verifier = _verifier(checks)
        if evidence_scope is not None:
            verifier = content_key({"verifier": verifier, "evidence_scope": evidence_scope})
        proof = _proof(artifact, verifier, result, passed, after)
        kind = "accepted" if accepted and passed else "wip"
        response = {"execution": "completed", "verification": verification,
                    "artifact_hash": artifact["hash"], "artifact_state": "available",
                    "bytes": artifact["size"], "checks": result["checks"],
                    "roundtrip_passed": passed, "roundtrip_details": details,
                    "target_handle": opened["target_handle"] if opened else target_handle,
                    **_SOURCE_ACTIONS, "checkpoint_kind": kind, "requested_checkpoint_kind": requested_kind,
                    "next_step": ("Inspect the returned new working copy before editing" if opened
                                  else "Inspect the retained archive and source document before further edits")}
        def publish(index, job):
            index.checkpoint(branch, artifact["hash"], kind, job,
                             expected_previous=prior if kind == "accepted" else prior_wip)
            return {"status": "completed", "references": [kind]}
        return response, artifact["hash"], proof, publish
    return _managed(adapter, workspace_path, "checkpoint", request, operation_id, worker)


def restore(adapter, workspace_path, branch, kind="accepted", operation_id=None):
    try:
        _branch(branch)
        if kind not in ("accepted", "wip"):
            raise ValueError("kind must be 'accepted' or 'wip'")
    except ValueError as exc:
        return _error("invalid_checkpoint", exc)
    request = {"branch": branch, "kind": kind}
    def worker(store, op):
        try:
            saved = store.checkout(branch, kind)
        except WorkspaceError as exc:
            return {"execution": "refused", "verification": "not_run", "error": "checkpoint_artifact_invalid",
                    "detail": str(exc)}, None, None, None
        if saved is None:
            return {"execution": "refused", "verification": "not_run", "error": "checkpoint_not_found"}, None, None, None
        confirmed = {"artifact_hash": saved["artifact_hash"], "artifact_state": "available", "historical_kind": kind}
        with _phase("open", "working_document_open", confirmed=confirmed, artifact_hash=saved["artifact_hash"]):
            opened = _open(adapter, saved["artifact"], op)
        target_context = {**confirmed, "target_handle": opened["target_handle"], "archive_open": "completed"}
        with _phase("restore-recompute", "working_document_mutation", confirmed=target_context,
                    artifact_hash=saved["artifact_hash"]):
            recomputed = _call(adapter, "recompute", {"target_handle": opened["target_handle"]},
                               _child(op, "restore-recompute"))
            if recomputed.get("recomputed") is not True:
                raise NativeFailure(Envelope(ok=False, error="recompute_result_unknown"),
                                    operation_id=_child(op, "restore-recompute"))
        return {"execution": "completed", "verification": "not_run", "target_handle": opened["target_handle"],
                "recompute": {"status": "completed", "receipt": _public_receipt(recomputed.get("receipt"))},
                **confirmed, "next_step": "Inspect and check the new working copy before editing"}, saved["artifact_hash"], None, None
    return _managed(adapter, workspace_path, "restore", request, operation_id, worker)


def _read_bounded(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{path.name} exceeds {limit} bytes")
    return data


def _manifest_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate manifest key {key}")
        result[key] = value
    return result


def _workspace_file(root, name, suffix):
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Expected a workspace-local {suffix} file")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != suffix or not path.is_file():
        raise ValueError(f"Expected an existing {suffix} file within the workspace")
    return path


def build(adapter, workspace_path, manifest_path="design.json", branch="main", operation_id=None):
    try:
        root = _root(workspace_path)
        _branch(branch)
        path = _workspace_file(root, manifest_path, ".json")
        manifest_bytes = _read_bounded(path, 262144)
        manifest = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_manifest_object)
        if not isinstance(manifest, dict) or type(manifest.get("schema")) is not int or manifest["schema"] != SCHEMA_VERSION:
            raise ValueError("Manifest schema must be 1")
        if set(manifest) - {"schema", "seed", "edit", "requirements", "protected", "script"}:
            raise ValueError("Manifest has unsupported fields")
        seed = _workspace_file(root, manifest["seed"], ".f3d")
        script_path = _workspace_file(root, manifest["script"], ".py") if "script" in manifest else None
        checks = _checks(manifest["requirements"])
        operation_id = _op(operation_id)
        # Validate the complete request before opening or editing any native document.
        template = prepare_edit_contract(target_handle="pending", operation_id=operation_id,
                                          requirements=checks, protected=manifest.get("protected", []), **manifest["edit"])
        store = _store(root)
        files = {"seed": seed}
        if script_path:
            if script_path.stat().st_size > 65536:
                raise ValueError("Custom recipe exceeds 64 KiB")
            files["script"] = script_path
        snapshots = store.snapshot_inputs({"edit": manifest["edit"]}, files)
        seed_artifact = snapshots["files"]["seed"]
        if not seed_artifact["size"]:
            raise ValueError("Seed archive is empty")
        custom = None
        if script_path:
            script_blob = snapshots["files"]["script"]
            raw = _read_bounded(Path(script_blob["path"]), 65536)
            if hashlib.sha256(raw).hexdigest() != script_blob["hash"]:
                raise ValueError("Staged recipe bytes do not match their recorded hash")
            custom = raw.decode("utf-8")
            _compile_recipe(custom, "<workspace-recipe>")
    except (OSError, ValueError, TypeError, KeyError, SyntaxError) as exc:
        return _error("invalid_manifest", exc)
    request = {"manifest": manifest, "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
               "inputs": {name: {"sha256": item["hash"], "size": item["size"]}
                          for name, item in snapshots["files"].items()}, "branch": branch}

    def worker(store, op):
        prior_wip, prior_accepted = (_pointer(store, branch, kind) for kind in ("wip", "accepted"))
        with _phase("seed-open", "working_document_open"):
            source = _open(adapter, seed_artifact, _child(op, "seed-open"))
        environment = _environment(source)
        build_key = content_key({"inputs": snapshots["key"], "environment": environment})
        verifier = _verifier(checks, template["protected"])
        candidate = source["target_handle"]
        seed_context = {"seed_target_handle": candidate, "target_handle": candidate, "construction_calls": 0}
        with _phase("seed-recompute", "working_document_mutation", confirmed=seed_context):
            recomputed = _call(adapter, "recompute", {"target_handle": candidate}, _child(op, "seed-recompute"))
            if recomputed.get("recomputed") is not True:
                raise NativeFailure(Envelope(ok=False, error="recompute_result_unknown"),
                                    operation_id=_child(op, "seed-recompute"))
        seed_context["seed_recompute"] = {"status": "completed", "receipt": _public_receipt(recomputed.get("receipt"))}
        contract = {**template, "target_handle": candidate, "operation_id": _child(op, "edit")}
        witnesses = checks + contract["protected"]
        with _phase("seed-check", "readback", confirmed=seed_context):
            baseline = _inspect(adapter, candidate, contract["parameter_name"], witnesses, _child(op, "seed-check"))
        parameter = baseline["parameter"]
        guards = compare_baseline(contract, baseline["measurements"])
        if (not guards["passed"] or not _healthy(baseline) or parameter["expression"] != contract["expected_expression"]
                or not _contract_finite(parameter["value_mm"])
                or not within_tolerance(parameter["value_mm"], contract["expected_value_mm"], contract["value_tolerance_mm"])):
            return {"execution": "refused", "verification": "not_run", "checks": guards["checks"],
                    **seed_context, "error": "seed_precondition_mismatch"}, None, None, None
        try:
            previous = store.lookup_geometry(build_key, environment) if custom is None else None
        except WorkspaceError as exc:
            return {"execution": "refused", "verification": "not_run", **seed_context,
                    "error": "cache_artifact_invalid", "detail": str(exc)}, None, None, None
        cache_hit = previous is not None
        construction = None
        post_edit = None
        if cache_hit:
            artifact = previous["artifact"]
            opened, result, failure = _reopen_and_inspect(
                adapter, artifact, op, contract["parameter_name"], witnesses, phase="cache",
                expected_expression=contract["expression"], confirmed={**seed_context, "cache_hit": True},
            )
            comparison = _compare_workspace_evidence(contract, baseline["measurements"],
                                                     result["measurements"] if result else None)
            expected_parameter = previous["evidence"]["roundtrip"].get("parameter")
            details = {
                "parameter_matches": bool(result and result.get("parameter_matches_expected") is True
                                          and _same_parameter(expected_parameter, result["parameter"])),
                "witness_mismatches": _pose_mismatches(contract["protected"], baseline["measurements"],
                                                       result["measurements"] if result else {}),
                "healthy": bool(result and _healthy(result)),
            }
            if result and "recompute" in result:
                details["recompute"] = result["recompute"]
            if failure:
                details.update(failure)
            complete = result is not None and _environment(result) == environment and _roundtrip_passed(details)
            verification = _verification(comparison["verification"], complete=complete)
        else:
            with _phase("edit", "model_edit", confirmed={**seed_context, "construction_calls": 1}):
                comparison = _call(adapter, "edit", {**contract, **({"custom_script": custom} if custom is not None else {})}, contract["operation_id"])
            if comparison.get("execution") != "completed":
                comparison = _confirm_rollback(adapter, contract, comparison, op)
                return {**seed_context, **comparison, "construction_calls": 1}, None, None, None
            comparison = _confirm_edit(adapter, contract, comparison, op)
            post_edit = comparison
            construction = comparison["construction"]
            confirmed = {**seed_context, "execution": "completed", "construction": construction,
                         "construction_calls": 1, "edit_readback": comparison["readback"],
                         "checks": comparison["checks"], "parameter": comparison["parameter"],
                         "health": comparison["health"], "timings_ms": comparison.get("timings_ms"),
                         "diagnostics": {"seed": baseline, "post_edit": comparison["result"]}}
            if comparison["readback"]["status"] != "matched":
                return {**comparison, **confirmed, "artifact_state": "unavailable"}, None, None, None
            with _phase("export", "archive_output", confirmed={**confirmed, "artifact_state": "unavailable"}):
                artifact = _export(adapter, store, candidate, _child(op, "export"))
            opened, result, failure = _reopen_and_inspect(
                adapter, artifact, op, contract["parameter_name"], witnesses, confirmed=confirmed,
            )
            rechecked = _compare_workspace_evidence(contract, comparison["baseline"],
                                                   result["measurements"] if result else None)
            details = _roundtrip(
                witnesses,
                {"parameter": comparison["parameter"], "measurements": comparison["result"],
                 "health": comparison["health"]["result"]},
                result,
            ) if result else failure
            complete = result is not None and _environment(result) == environment and _roundtrip_passed(details)
            verification = _verification(comparison["verification"], rechecked["verification"], complete=complete)
            if comparison["verification"] != "failed" or rechecked["verification"] == "failed":
                comparison = rechecked
        if opened:
            candidate = opened["target_handle"]
        elif cache_hit:
            candidate = None
        roundtrip = verification == "verified"
        proof = _proof(artifact, verifier, comparison, roundtrip, result)
        response = {"execution": "completed", "verification": verification, "checks": comparison["checks"],
                    "failed_requirement_ids": comparison["failed_requirement_ids"],
                    "unknown_requirement_ids": comparison["unknown_requirement_ids"],
                    "target_handle": candidate, "seed_target_handle": seed_context["seed_target_handle"],
                    "artifact_hash": artifact["hash"], "artifact_state": "available", "build_key": build_key,
                    "verifier_key": verifier, "cache_hit": cache_hit, "construction_calls": 0 if cache_hit else 1,
                    "construction": construction,
                    "cacheable": custom is None and roundtrip, "roundtrip_passed": roundtrip, "roundtrip_details": details,
                    "parameter": result["parameter"] if result else None, "health": result["health"] if result else None,
                    "diagnostics": {"seed": baseline, "archive": result,
                                    "post_edit": post_edit["result"] if post_edit else None},
                    "environment": environment, "scope": "Selected requirements, parameter expression and protected witnesses"}
        if not cache_hit:
            response["edit_readback"] = confirmed["edit_readback"]
            if "timings_ms" in post_edit:
                response["timings_ms"] = post_edit["timings_ms"]
        def publish(index, job):
            index.checkpoint(branch, artifact["hash"], "wip", job, expected_previous=prior_wip)
            references = ["wip"]
            cache_published = False
            if roundtrip:
                index.checkpoint(branch, artifact["hash"], "accepted", job, expected_previous=prior_accepted)
                references.append("accepted")
                if custom is None and index.lookup_cache(build_key, verifier, environment) is None:
                    # Cache proof is attached to a separate immutable job with exact B/environment provenance.
                    cache_job = _child(op, "cache-publication")
                    cache_meta = {"action": "cache_publication", "build_key": build_key, "environment": environment}
                    index.start_job(cache_job, content_key({**cache_meta, "verifier_key": verifier, "artifact": artifact["hash"]}),
                                    cache_meta)
                    index.finish_job(cache_job, "running", "completed", proof, artifact["hash"])
                    index.publish_cache(build_key, verifier, environment, cache_job)
                    cache_published = True
            return {"status": "completed", "references": references, "cache_published": cache_published}
        return response, artifact["hash"], proof, publish
    return _managed(adapter, workspace_path, "build", request, operation_id, worker)


def status(adapter, workspace_path, operation_id=None):
    try:
        store = _store(workspace_path)
        if operation_id is not None:
            job = store.get_job(_op(operation_id))
            if job is None:
                return _error("job_not_found", "No workspace job has this ID")
            journal = getattr(adapter, "receipt_journal_dir", None)
            receipt = get_status(operation_id, journal_dir=journal)
            action = job["metadata"].get("action")
            phases = {
                "edit": ("edit-readback", "rollback-readback"),
                "checkpoint": ("before", "export", "roundtrip-open", "roundtrip-recompute", "roundtrip-check"),
                "restore": ("restore-recompute",),
                "build": ("seed-open", "seed-recompute", "seed-check", "edit", "edit-readback", "rollback-readback", "export", "roundtrip-open",
                          "roundtrip-recompute", "roundtrip-check", "cache-open", "cache-recompute", "cache-check"),
            }.get(action, ())
            phase_receipts = {}
            for phase in phases:
                recorded = get_status(_child(operation_id, phase), journal_dir=journal)
                if recorded.ok:
                    phase_receipts[phase] = {key: recorded.result[key] for key in ("operation_id", "status")}
            return Envelope(ok=True, result={"job": job, "receipt": receipt.result, "phase_receipts": phase_receipts,
                                             "meaning": "Historical records; not current model verification"})
        return Envelope(ok=True, result={"jobs": [{"operation_id": job["operation_id"], "state": job["state"],
                                                   "action": job["metadata"].get("action"), "resolved_by": job.get("resolved_by")}
                                                  for job in store.list_jobs()][-100:],
                                         "checkpoints": store.list_checkpoints(), "automatic_eviction": False})
    except (OSError, ValueError) as exc:
        return _error("workspace_status_failed", exc)


def recover(adapter, workspace_path, operation_id):
    """A late completed edit can be rechecked. No acknowledgement releases an unknown write."""
    try:
        store, operation_id = _store(workspace_path), _op(operation_id)
        with store.writer_lock():
            job = store.get_job(operation_id)
            if not job or job["metadata"].get("action") != "edit":
                return _error("recovery_unsupported", "Only late confirmed edit results can be recovered automatically; retain other uncertain candidates")
            if job.get("resolved_by"):
                return Envelope(ok=True, result={"resolved_by": job["resolved_by"], "historical": True})
            if job["state"] not in ("running", "uncertain"):
                return _error("recovery_not_required", "This edit already has a known execution outcome")
            receipt = get_status(operation_id, journal_dir=getattr(adapter, "receipt_journal_dir", None))
            data = receipt.result or {}
            if not receipt.ok or data.get("status") != "completed":
                return _error("execution_still_uncertain", "A completed native receipt is required; no mutation was replayed")
            context = data.get("receipt_context") or {}
            contract = job["metadata"]["contract"]
            if (context.get("kind") != "workspace" or context.get("action") != "edit"
                    or context.get("target_handle") != contract["target_handle"]):
                return _error("recovery_target_mismatch", "Receipt does not match the bound target")
            declared = data.get("declared_result") or {}
            if declared.get("execution") != "completed" or not declared.get("baseline") or not declared.get("result"):
                return _error("recovery_requires_inspection", "Receipt does not establish a completed checked edit")
            witnesses = contract["requirements"] + contract["protected"]
            observed = _inspect(adapter, contract["target_handle"], contract["parameter_name"], witnesses,
                                expected_expression=contract["expression"], recovery_probe=True)
            comparison = _compare_workspace_evidence(contract, declared["baseline"], observed["measurements"])
            details = _roundtrip(
                witnesses, {"parameter": declared.get("parameter"), "measurements": declared["result"],
                            "health": (declared.get("health") or {}).get("result")},
                observed,
            )
            if (observed["pending"] is not None or observed.get("parameter_matches_expected") is not True
                    or not _roundtrip_passed(details) or observed["health"] != declared["health"]["result"]
                    or comparison["verification"] != declared.get("verification")):
                return _error("recovery_state_changed", "Current target differs from the late recorded result")
            recovery_id = str(uuid.uuid4())
            metadata = {"action": "recovery", "resolves": operation_id, "target_handle": contract["target_handle"],
                        "receipt": data["request_fingerprint"]}
            store.start_job(recovery_id, content_key(metadata), metadata)
            store.finish_job(recovery_id, "running", "completed", {"terminal_receipt": operation_id,
                              "recovery_verified": True, "response": comparison})
            store.record_resolution(operation_id, recovery_id)
            return Envelope(ok=True, result={**comparison, "resolved_by": recovery_id, "replayed": False,
                                             "recovery": "Confirmed late completion and fresh scoped measurements; no rollback"})
    except NativeFailure as exc:
        return exc.envelope
    except (OSError, ValueError, TypeError) as exc:
        return _error("workspace_recovery_refused", exc)
