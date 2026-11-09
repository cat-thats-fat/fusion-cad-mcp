from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from fusion_cad_mcp.workspace_store import (
    ConflictError,
    IntegrityError,
    WorkspaceError,
    WorkspaceStore,
    content_key,
)

OP = "12345678-1234-4234-8234-123456789abc"
SECOND = "12345678-1234-4234-8234-123456789abd"
THIRD = "12345678-1234-4234-8234-123456789abe"


def evidence(artifact, verifier):
    return {"execution": "completed", "artifact_hash": artifact, "verifier_key": verifier,
            "required_checks": ["width", "protected"],
            "checks": {"width": "passed", "protected": "passed"},
            "roundtrip": {"status": "passed", "artifact_hash": artifact}}


def setup_job(tmp_path, environment=None):
    store = WorkspaceStore(tmp_path / "store")
    source = tmp_path / "model.f3d"
    source.write_bytes(b"native artifact")
    artifact = store.import_artifact(source)["hash"]
    build, verifier = content_key({"build": 1}), content_key({"checks": 1})
    env = {"fusion": "test"} if environment is None else environment
    store.start_job(OP, build, {"build_key": build, "environment": env})
    return store, artifact, build, verifier, env


def test_input_snapshots_capture_consumed_bytes_and_missing_asset(tmp_path):
    store = WorkspaceStore(tmp_path / "store")
    source = tmp_path / "recipe.py"
    source.write_bytes(b"first")
    first = store.snapshot_inputs({"parameters": {"width": 2}}, {"recipe": source})
    source.write_bytes(b"second")
    second = store.snapshot_inputs({"parameters": {"width": 2}}, {"recipe": source})
    assert Path(first["files"]["recipe"]["path"]).read_bytes() == b"first"
    assert first["key"] != second["key"]
    assert store.snapshot_inputs({"parameters": {"width": 2}}, {"recipe": source})["key"] == second["key"]
    with pytest.raises(FileNotFoundError):
        store.snapshot_inputs({}, {"missing": tmp_path / "absent"})


def test_cache_requires_evidence_and_changes_in_verifier_miss(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    with pytest.raises(WorkspaceError):
        store.publish_cache(build, verifier, env, OP)
    store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)
    store.publish_cache(build, verifier, env, OP)
    assert store.lookup_cache(build, verifier, env)["artifact"]["hash"] == artifact
    assert store.lookup_cache(build, content_key({"checks": 2}), env) is None
    assert store.lookup_cache(build, verifier, {"fusion": "other"}) is None


def test_cache_environment_preserves_canonical_value_types(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path, {"mode": True, "fusion": "test"})
    store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)
    with pytest.raises(WorkspaceError, match="environment"):
        store.publish_cache(build, verifier, {**env, "mode": 1}, OP)
    reversed_env = dict(reversed(list(env.items())))
    assert store.publish_cache(build, verifier, reversed_env, OP)["environment"] == env


@pytest.mark.parametrize("change", ["uncertain", "failed_check", "missing_check", "roundtrip", "artifact", "verifier"])
def test_bad_evidence_never_promotes(tmp_path, change):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    item = evidence(artifact, verifier)
    state = "completed"
    if change == "uncertain":
        state = "uncertain"
    elif change == "failed_check":
        item["checks"]["width"] = "failed"
    elif change == "missing_check":
        del item["checks"]["width"]
    elif change == "roundtrip":
        item["roundtrip"]["status"] = "unknown"
    elif change == "artifact":
        item["artifact_hash"] = content_key("different")
    else:
        item["verifier_key"] = content_key("different")
    store.finish_job(OP, "running", state, item, artifact)
    with pytest.raises(WorkspaceError):
        store.publish_cache(build, verifier, env, OP)
    if change != "verifier":
        with pytest.raises(WorkspaceError):
            store.checkpoint("main", artifact, "accepted", OP, expected_previous=None)
    with pytest.raises(ConflictError):
        store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)


def test_checkpoints_keep_wip_and_accepted_separate(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)
    store.checkpoint("main", artifact, "accepted", OP, expected_previous=None)
    source = tmp_path / "wip.f3d"
    source.write_bytes(b"unfinished")
    wip = store.import_artifact(source)["hash"]
    store.checkpoint("main", wip, "wip", expected_previous=None)
    assert store.checkout("main", "accepted")["artifact_hash"] == artifact
    assert store.checkout("main", "wip")["artifact_hash"] == wip
    assert store.checkout("other", "wip") is None


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_lookup_and_checkout_reject_damaged_artifacts(tmp_path, fault):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)
    store.checkpoint("main", artifact, "accepted", OP, expected_previous=None)
    store.publish_cache(build, verifier, env, OP)
    path = Path(store.get_artifact(artifact)["path"])
    if fault == "missing":
        path.unlink()
    else:
        path.write_bytes(b"damaged")
    with pytest.raises(IntegrityError):
        store.lookup_cache(build, verifier, env)
    with pytest.raises(IntegrityError):
        store.checkout("main", "accepted")


def test_duplicate_operation_and_concurrent_completion(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    with pytest.raises(ConflictError):
        store.start_job(OP, content_key("other"), {})
    with pytest.raises(ConflictError):
        store.start_job(OP, build, {})
    assert store.start_job(OP, build, {"environment": env, "build_key": build})["state"] == "running"
    def finish(_):
        try:
            return store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)["state"]
        except ConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(finish, range(8)))
    assert results.count("completed") == 1
    assert results.count("conflict") == 7


def test_racing_same_key_publications_do_not_replace_result(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.publish_cache(build, verifier, env, OP), range(8)))
    assert all(item["artifact"]["hash"] == artifact for item in results)
    store.start_job(SECOND, build, {"build_key": build, "environment": env})
    store.finish_job(SECOND, "running", "completed", evidence(artifact, verifier), artifact)
    with pytest.raises(ConflictError):
        store.publish_cache(build, verifier, env, SECOND)


def test_checkpoint_compare_and_swap_and_reference_listing(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    store.checkpoint("main", artifact, "wip", expected_previous=None)
    with pytest.raises(ConflictError):
        store.checkpoint("main", artifact, "wip", expected_previous=None)
    second_path = tmp_path / "second.f3d"
    second_path.write_bytes(b"new content")
    second = store.import_artifact(second_path)["hash"]
    store.checkpoint("main", second, "wip", expected_previous=artifact)
    with pytest.raises(ConflictError):
        store.checkpoint("main", artifact, "wip", expected_previous=artifact)
    assert store.list_checkpoints()[0]["artifact_hash"] == second
    with pytest.raises(WorkspaceError):
        store.checkpoint("x" * 257, second, "wip", expected_previous=None)


def test_geometry_reuse_and_unresolved_jobs(tmp_path):
    store, artifact, build, verifier, env = setup_job(tmp_path)
    assert [job["operation_id"] for job in store.unresolved_jobs()] == [OP]
    store.finish_job(OP, "running", "completed", evidence(artifact, verifier), artifact)
    store.publish_cache(build, verifier, env, OP)
    result = store.lookup_geometry(build, env)
    assert result["artifact"]["hash"] == artifact
    assert result["verifier_key"] == verifier
    assert store.lookup_geometry(build, {"fusion": "different"}) is None
    store.start_job(SECOND, build, {})
    store.finish_job(SECOND, "running", "uncertain")
    assert [job["operation_id"] for job in store.unresolved_jobs()] == [SECOND]
    assert len(store.list_jobs()) == 2


@pytest.mark.parametrize("state", ["running", "uncertain"])
def test_recovery_links_original_without_rewriting_history(tmp_path, state):
    store = WorkspaceStore(tmp_path / "store")
    original = store.start_job(OP, content_key("edit"), {"target_handle": "owned-branch"})
    if state == "uncertain":
        original = store.finish_job(OP, "running", state, {"execution": "unknown"})
    store.start_job(SECOND, content_key("recover"), {"target_handle": "owned-branch", "resolves": OP})
    store.finish_job(
        SECOND, "running", "completed", {"terminal_receipt": OP, "recovery_verified": True}
    )
    assert [job["operation_id"] for job in store.unresolved_jobs()] == [OP]

    resolved = {**original, "resolved_by": SECOND}
    assert store.record_resolution(OP, SECOND) == resolved
    assert store.record_resolution(OP, SECOND) == resolved
    assert store.get_job(OP) == resolved
    assert store.list_jobs()[0] == resolved
    assert store.unresolved_jobs() == []
    with pytest.raises(ConflictError):
        store.finish_job(OP, "running", "completed")
    with pytest.raises(ConflictError):
        store.record_resolution(OP, THIRD)


@pytest.mark.parametrize(
    ("metadata", "evidence", "recovery_state"),
    [
        ({"target_handle": "other"}, {}, "completed"),
        ({"resolves": THIRD}, {}, "completed"),
        ({}, {"terminal_receipt": THIRD}, "completed"),
        ({}, {"recovery_verified": 1}, "completed"),
        ({}, {}, "running"),
        ({}, {}, "failed"),
    ],
)
def test_recovery_requires_completed_evidence_for_same_target_and_receipt(
    tmp_path, metadata, evidence, recovery_state
):
    store = WorkspaceStore(tmp_path / "store")
    store.start_job(OP, content_key("edit"), {"target_handle": "owned-branch"})
    store.start_job(
        SECOND,
        content_key("recover"),
        {"target_handle": "owned-branch", "resolves": OP, **metadata},
    )
    if recovery_state != "running":
        store.finish_job(
            SECOND,
            "running",
            recovery_state,
            {"terminal_receipt": OP, "recovery_verified": True, **evidence},
        )
    with pytest.raises(WorkspaceError):
        store.record_resolution(OP, SECOND)
    assert store.get_job(OP)["resolved_by"] is None
    assert store.unresolved_jobs()[0]["operation_id"] == OP


def test_invalid_identity_and_immutable_blob_collision(tmp_path):
    store, artifact, build, _, _ = setup_job(tmp_path)
    with pytest.raises(WorkspaceError):
        store.start_job("not-a-uuid", build, {})
    with pytest.raises(WorkspaceError):
        store.start_job(SECOND, "not-a-hash", {})
    path = Path(store.get_artifact(artifact)["path"])
    path.write_bytes(b"corrupted")
    original = tmp_path / "model.f3d"
    with pytest.raises(IntegrityError):
        store.import_artifact(original)
    assert path.read_bytes() == b"corrupted"


def test_writer_lock_excludes_other_process(tmp_path):
    import subprocess
    import sys

    store = WorkspaceStore(tmp_path / "store")
    code = """import sys
sys.path.insert(0, sys.argv[2])
from fusion_cad_mcp.workspace_store import WorkspaceStore
try:
    with WorkspaceStore(sys.argv[1]).writer_lock(timeout=0.1):
        print('acquired')
except TimeoutError:
    print('blocked')
"""
    with store.writer_lock():
        child = subprocess.run([sys.executable, "-c", code, str(store.root), str(Path(__file__).resolve().parents[1] / "src")],
                               capture_output=True, text=True, check=True)
        assert child.stdout.strip() == "blocked"
    child = subprocess.run([sys.executable, "-c", code, str(store.root), str(Path(__file__).resolve().parents[1] / "src")],
                           capture_output=True, text=True, check=True)
    assert child.stdout.strip() == "acquired"
