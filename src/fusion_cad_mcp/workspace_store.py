"""Local immutable blobs and a durable index for workspace coordinators.

Records are claims made by the trusted coordinator, not proof of native geometry.
No transactions span a Fusion call, and no blobs are automatically deleted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fusion_cad_mcp.receipts import _endpoint_lock


class WorkspaceError(ValueError):
    """A workspace invariant was violated."""


class ConflictError(WorkspaceError):
    """A job or pointer changed since the caller observed it."""


class IntegrityError(WorkspaceError):
    """An immutable blob is missing or no longer matches its name."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_key(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _hash_ok(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _eligible(evidence: Any, artifact: str, verifier: str | None = None) -> bool:
    if not isinstance(evidence, dict):
        return False
    required = evidence.get("required_checks")
    checks = evidence.get("checks")
    roundtrip = evidence.get("roundtrip")
    return (
        evidence.get("execution") == "completed"
        and evidence.get("artifact_hash") == artifact
        and _hash_ok(evidence.get("verifier_key"))
        and (verifier is None or evidence["verifier_key"] == verifier)
        and isinstance(required, list) and bool(required)
        and all(isinstance(x, str) and x for x in required)
        and len(set(required)) == len(required)
        and isinstance(checks, dict)
        and all(checks.get(x) == "passed" for x in required)
        and isinstance(roundtrip, dict)
        and roundtrip.get("status") == "passed"
        and roundtrip.get("artifact_hash") == artifact
    )


def _cache_inputs_match(metadata: Any, build_key: str, environment_json: str) -> bool:
    return (
        isinstance(metadata, dict)
        and metadata.get("build_key") == build_key
        and "environment" in metadata
        and canonical_json(metadata["environment"]) == environment_json
    )


_JOBS = """SELECT jobs.*, resolutions.recovery_id AS resolved_by FROM jobs
           LEFT JOIN resolutions ON resolutions.original_id=jobs.operation_id"""


class WorkspaceStore:
    def __init__(self, path: str | Path):
        self.root = Path(path).resolve()
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "workspace.sqlite3"
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    operation_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    metadata TEXT NOT NULL, state TEXT NOT NULL,
                    artifact_hash TEXT, evidence TEXT
                );
                CREATE TABLE IF NOT EXISTS resolutions (
                    original_id TEXT PRIMARY KEY REFERENCES jobs(operation_id),
                    recovery_id TEXT NOT NULL UNIQUE REFERENCES jobs(operation_id)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    branch TEXT NOT NULL, kind TEXT NOT NULL,
                    artifact_hash TEXT NOT NULL, operation_id TEXT,
                    PRIMARY KEY(branch, kind)
                );
                CREATE TABLE IF NOT EXISTS cache (
                    build_key TEXT NOT NULL, verifier_key TEXT NOT NULL,
                    environment TEXT NOT NULL, artifact_hash TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    PRIMARY KEY(build_key, verifier_key, environment)
                );
            """)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=EXTRA")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def writer_lock(self, timeout: float = 2.0):
        """Serialize workspace orchestration across processes, including native calls.

        This lock is not a lease and does not clear unresolved jobs on release.
        """
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 300:
            raise WorkspaceError("lock timeout must be between 0 and 300 seconds")
        return _endpoint_lock(self.root, "workspace-writer", timeout)

    def _put(self, path: str | Path) -> dict[str, Any]:
        """Copy once while hashing; the staged bytes are the consumed bytes."""
        digest = hashlib.sha256()
        size = 0
        fd, tmp = tempfile.mkstemp(prefix=".stage-", dir=self.blobs)
        try:
            with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            name = digest.hexdigest()
            destination = self.blobs / name
            try:
                # The blob name is immutable: never replace a concurrent publisher.
                os.link(tmp, destination)
                if os.name != "nt":
                    dir_fd = os.open(self.blobs, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
            except FileExistsError:
                pass
            self.get_artifact(name)
            return {"hash": name, "size": size, "path": str(destination)}
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def import_artifact(self, path: str | Path) -> dict[str, Any]:
        return self._put(path)

    stage = import_artifact

    def get_artifact(self, artifact_hash: str) -> dict[str, Any]:
        if not _hash_ok(artifact_hash):
            raise IntegrityError("invalid artifact hash")
        path = self.blobs / artifact_hash
        try:
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise IntegrityError("blob missing or unreadable") from exc
        if digest.hexdigest() != artifact_hash:
            raise IntegrityError("blob hash mismatch")
        return {"hash": artifact_hash, "size": size, "path": str(path)}

    def snapshot_inputs(self, declaration: Any, files: Mapping[str, str | Path]) -> dict[str, Any]:
        snapshots = {}
        for name, path in sorted(files.items()):
            if not isinstance(name, str) or not name:
                raise WorkspaceError("input names must be nonempty strings")
            snapshots[name] = self._put(path)
        identity = {"declaration": declaration, "files": {
            name: {"sha256": blob["hash"], "size": blob["size"]}
            for name, blob in snapshots.items()
        }}
        return {"key": content_key(identity), "declaration": declaration, "files": snapshots}

    def start_job(self, operation_id: str, fingerprint: str, metadata: Any) -> dict[str, Any]:
        try:
            valid_id = isinstance(operation_id, str) and str(uuid.UUID(operation_id)) == operation_id
        except (ValueError, AttributeError):
            valid_id = False
        if not valid_id or not _hash_ok(fingerprint):
            raise WorkspaceError("operation id must be a canonical UUID and fingerprint a SHA-256 digest")
        encoded = canonical_json(metadata)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._select_job(conn, operation_id)
            if row is None:
                conn.execute("INSERT INTO jobs VALUES (?, ?, ?, 'running', NULL, NULL)",
                             (operation_id, fingerprint, encoded))
                row = self._select_job(conn, operation_id)
            elif row["fingerprint"] != fingerprint or row["metadata"] != encoded:
                raise ConflictError("operation id already belongs to a different request")
            return self._job(row)

    @staticmethod
    def _select_job(conn: sqlite3.Connection, operation_id: str) -> sqlite3.Row | None:
        return conn.execute(_JOBS + " WHERE jobs.operation_id=?", (operation_id,)).fetchone()

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result["evidence"] = json.loads(result["evidence"]) if result["evidence"] else None
        return result

    def get_job(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = self._select_job(conn, operation_id)
            return self._job(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [self._job(row) for row in conn.execute(_JOBS + " ORDER BY jobs.rowid")]

    def unresolved_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [self._job(row) for row in conn.execute(
                _JOBS + " WHERE jobs.state IN ('running', 'uncertain') "
                "AND resolutions.original_id IS NULL ORDER BY jobs.rowid")]

    def finish_job(self, operation_id: str, expected_state: str, execution: str,
                   evidence: Any = None, artifact_hash: str | None = None) -> dict[str, Any]:
        if expected_state != "running" or execution not in ("completed", "failed", "uncertain"):
            raise WorkspaceError("only running jobs may reach a terminal state")
        if artifact_hash is not None:
            self.get_artifact(artifact_hash)
        if evidence is not None and not isinstance(evidence, dict):
            raise WorkspaceError("evidence must be a mapping")
        encoded = canonical_json(evidence) if evidence is not None else None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("UPDATE jobs SET state=?, artifact_hash=?, evidence=? "
                                   "WHERE operation_id=? AND state=? "
                                   "AND NOT EXISTS (SELECT 1 FROM resolutions WHERE original_id=jobs.operation_id)",
                                   (execution, artifact_hash, encoded, operation_id, expected_state)).rowcount
            if changed != 1:
                raise ConflictError("job missing, resolved or no longer running")
            return self._job(self._select_job(conn, operation_id))

    def record_resolution(self, original_id: str, recovery_id: str) -> dict[str, Any]:
        """Link verified recovery without changing the historical job."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            original = self._select_job(conn, original_id)
            recovery = self._select_job(conn, recovery_id)
            if original is None or original["state"] not in ("running", "uncertain"):
                raise ConflictError("original job is missing or has no unresolved outcome")
            if original["resolved_by"] is not None:
                if original["resolved_by"] != recovery_id:
                    raise ConflictError("original job already has a different resolution")
                return self._job(original)
            if recovery is None or recovery["state"] != "completed":
                raise WorkspaceError("recovery job must be completed")
            original_meta = json.loads(original["metadata"])
            recovery_job = self._job(recovery)
            recovery_meta = recovery_job["metadata"]
            evidence = recovery_job["evidence"]
            target = original_meta.get("target_handle") if isinstance(original_meta, dict) else None
            if (
                not isinstance(target, str)
                or not target.strip()
                or not isinstance(recovery_meta, dict)
                or recovery_meta.get("target_handle") != target
                or recovery_meta.get("resolves") != original_id
                or not isinstance(evidence, dict)
                or evidence.get("terminal_receipt") != original_id
                or evidence.get("recovery_verified") is not True
            ):
                raise WorkspaceError("recovery evidence must match the original job and target")
            conn.execute("INSERT INTO resolutions VALUES (?, ?)", (original_id, recovery_id))
            return self._job(self._select_job(conn, original_id))

    def checkpoint(self, branch: str, artifact_hash: str, kind: str,
                   operation_id: str | None = None, *, expected_previous: str | None) -> dict[str, Any]:
        if (not isinstance(branch, str) or not branch or len(branch) > 256
                or not branch.isprintable() or kind not in ("accepted", "wip")):
            raise WorkspaceError("branch must be printable and at most 256 characters; kind is accepted/wip")
        if expected_previous is not None and not _hash_ok(expected_previous):
            raise WorkspaceError("expected previous must be a SHA-256 digest or None")
        self.get_artifact(artifact_hash)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT artifact_hash FROM checkpoints WHERE branch=? AND kind=?",
                                   (branch, kind)).fetchone()
            if (current["artifact_hash"] if current else None) != expected_previous:
                raise ConflictError("checkpoint changed since it was observed")
            if kind == "accepted":
                row = conn.execute("SELECT * FROM jobs WHERE operation_id=?", (operation_id,)).fetchone()
                if not row or row["state"] != "completed" or row["artifact_hash"] != artifact_hash or not _eligible(json.loads(row["evidence"] or "null"), artifact_hash):
                    raise WorkspaceError("accepted checkpoint requires complete matching evidence")
            conn.execute("INSERT INTO checkpoints VALUES (?, ?, ?, ?) ON CONFLICT(branch, kind) "
                         "DO UPDATE SET artifact_hash=excluded.artifact_hash, operation_id=excluded.operation_id",
                         (branch, kind, artifact_hash, operation_id))
        return self.checkout(branch, kind)

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """List references; checkout verifies the referenced blob before use."""
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM checkpoints ORDER BY branch, kind")]

    def checkout(self, branch: str, kind: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM checkpoints WHERE branch=? AND kind=?", (branch, kind)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["artifact"] = self.get_artifact(result["artifact_hash"])
        return result

    def publish_cache(self, build_key: str, verifier_key: str, environment: Any, operation_id: str) -> dict[str, Any]:
        if not _hash_ok(build_key) or not _hash_ok(verifier_key):
            raise WorkspaceError("build and verifier keys must be SHA-256 digests")
        env = canonical_json(environment)
        job = self.get_job(operation_id)
        if not job or job["state"] != "completed" or not job["artifact_hash"] or not _eligible(job["evidence"], job["artifact_hash"], verifier_key):
            raise WorkspaceError("cache publication requires completed matching evidence")
        # Bind the build and environment to the exact coordinator job, not a caller-selected result.
        if not _cache_inputs_match(job["metadata"], build_key, env):
            raise WorkspaceError("job does not match build key and environment")
        self.get_artifact(job["artifact_hash"])
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT * FROM cache WHERE build_key=? AND verifier_key=? AND environment=?", (build_key, verifier_key, env)).fetchone()
            if existing and (existing["artifact_hash"] != job["artifact_hash"] or existing["operation_id"] != operation_id):
                raise ConflictError("cache key already published by another job")
            conn.execute("INSERT OR IGNORE INTO cache VALUES (?, ?, ?, ?, ?)",
                         (build_key, verifier_key, env, job["artifact_hash"], operation_id))
        return self.lookup_cache(build_key, verifier_key, environment)

    def lookup_cache(self, build_key: str, verifier_key: str, environment: Any) -> dict[str, Any] | None:
        env = canonical_json(environment)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cache WHERE build_key=? AND verifier_key=? AND environment=?", (build_key, verifier_key, env)).fetchone()
        if row is None:
            return None
        job = self.get_job(row["operation_id"])
        if not job or job["state"] != "completed" or job["artifact_hash"] != row["artifact_hash"] or not _eligible(job["evidence"], row["artifact_hash"], verifier_key):
            raise IntegrityError("cache evidence no longer matches")
        if not _cache_inputs_match(job["metadata"], build_key, env):
            raise IntegrityError("cache inputs no longer match")
        return {"build_key": build_key, "verifier_key": verifier_key, "environment": environment,
                "artifact": self.get_artifact(row["artifact_hash"]), "operation_id": row["operation_id"],
                "evidence": job["evidence"]}

    def lookup_geometry(self, build_key: str, environment: Any) -> dict[str, Any] | None:
        """Return a historically verified artifact; changed checks still need fresh verification."""
        with self._connect() as conn:
            row = conn.execute("SELECT verifier_key FROM cache WHERE build_key=? AND environment=? "
                               "ORDER BY verifier_key LIMIT 1",
                               (build_key, canonical_json(environment))).fetchone()
        if row is None:
            return None
        return self.lookup_cache(build_key, row["verifier_key"], environment)
