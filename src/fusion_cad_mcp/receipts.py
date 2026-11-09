"""Local, bounded receipts for trusted typed Fusion scripts.

Only cooperating callers are coordinated. A started record is historical evidence,
not evidence that Fusion is still running. Read-only probes ignore unresolved write
barriers; after a lost response they can overlap a still-running Fusion request.
Raw scripts and external clients are outside this module. Reconciliation relies on
caller attestation that execution ended and a fresh same-document read-only probe;
it does not establish success, rollback or quiescence.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import symtable
import tempfile
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from .envelope import Envelope

_MAX_RECORD = 256 * 1024
_MAX_ENTRIES = 2000
_RECOVERY_RESERVE = 32
_VERSION = 1


def _root(journal_dir: str | os.PathLike[str] | None) -> Path:
    if journal_dir is None:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        return Path(base) / "fusion-cad-mcp" / "receipts-v1"
    return Path(journal_dir)


def _id(value: str | None) -> str:
    if value is None:
        return str(uuid.uuid4())
    if not isinstance(value, str):
        raise ValueError("operation_id must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("operation_id must be a canonical UUID") from exc
    if str(parsed) != value:
        raise ValueError("operation_id must be a canonical UUID")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace_context(context: dict[str, Any]) -> dict[str, str]:
    if (
        not isinstance(context, dict)
        or context.get("kind") != "workspace"
        or context.keys() - {"kind", "target_handle", "action"}
    ):
        raise ValueError("receipt_context must describe a workspace target")
    normalized = {"kind": "workspace"}
    for key, limit in (("target_handle", 512), ("action", 64)):
        if key == "action" and key not in context:
            continue
        value = context.get(key)
        if (
            not isinstance(value, str)
            or len(value) > limit
            or not value.strip()
            or len(value.encode("utf-8")) > limit
        ):
            raise ValueError(f"receipt_context {key} must contain 1 to {limit} UTF-8 bytes")
        normalized[key] = value
    return normalized


def _fingerprint(
    endpoint: str, script: str, read_only: bool, receipt_context: dict[str, str] | None = None
) -> str:
    request = [endpoint, script, read_only]
    if receipt_context is not None:
        request.append(receipt_context)
    return _hash(
        json.dumps(request, ensure_ascii=True, separators=(",", ":"))
    )


def _read(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            data = f.read(_MAX_RECORD + 1)
    except FileNotFoundError:
        return None
    if len(data) > _MAX_RECORD:
        raise ValueError("receipt exceeds size limit")
    value = json.loads(data)
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != _VERSION
    ):
        raise ValueError("invalid receipt")
    return value


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(data) > _MAX_RECORD:
        raise ValueError("receipt exceeds size limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    if len(data) > _MAX_RECORD:
        raise ValueError("receipt exceeds size limit")
    fd, tmp = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(tmp)


@contextmanager
def _endpoint_lock(root: Path, endpoint_hash: str, timeout: float):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (root / ("lock-" + endpoint_hash)).open("a+b")
    if lock.tell() == 0:
        lock.write(b"0")
        lock.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("endpoint lock wait expired") from None
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


class _Instrument(ast.NodeTransformer):
    def __init__(self, script: str):
        self.count = 0
        self.scope = [symtable.symtable(script, "<typed-script>", "exec")]
        try:
            symbol = self.scope[0].lookup("print")
        except KeyError:
            symbol = None
        if symbol and (symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()):
            raise ValueError("module-level print shadowing is unsupported")

    def _scoped(self, node, symbol_name):
        # No receipt transformation is needed in a scope without any print call.
        # This also avoids relying on symbol-table identity for same-line generators.
        if not any(
            isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "print"
            for x in ast.walk(node)
        ):
            return node
        matches = [
            x
            for x in self.scope[-1].get_children()
            if x.get_name() == symbol_name and x.get_lineno() == node.lineno
        ]
        if not matches and symbol_name in {"listcomp", "setcomp", "dictcomp"}:
            # Python 3.12 may inline comprehensions into the containing symbol table.
            if any(
                isinstance(x, ast.Name) and x.id == "print" and isinstance(x.ctx, ast.Store)
                for x in ast.walk(node)
            ):
                raise ValueError("print shadowing in an inlined comprehension is unsupported")
            return self.generic_visit(node)
        if len(matches) != 1:
            raise ValueError("unsupported lexical scope in typed script")
        self.scope.append(matches[0])
        try:
            return self.generic_visit(node)
        finally:
            self.scope.pop()

    def visit_FunctionDef(self, node):
        return self._scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node):
        return self._scoped(node, node.name)

    def visit_ClassDef(self, node):
        return self._scoped(node, node.name)

    def visit_Lambda(self, node):
        return self._scoped(node, "lambda")

    def visit_ListComp(self, node):
        return self._scoped(node, "listcomp")

    def visit_SetComp(self, node):
        return self._scoped(node, "setcomp")

    def visit_DictComp(self, node):
        return self._scoped(node, "dictcomp")

    def visit_GeneratorExp(self, node):
        return self._scoped(node, "genexpr")

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            if self.scope[-1].get_type() == "module":
                raise ValueError("top-level print is unsupported")
            symbol = self.scope[-1].lookup("print")
            if not symbol.is_global():
                return node
            if symbol.is_declared_global() and symbol.is_assigned():
                raise ValueError("global print reassignment is unsupported")
            if (
                len(node.args) != 1
                or node.keywords
                or not isinstance(node.args[0], ast.Call)
                or not isinstance(node.args[0].func, ast.Attribute)
                or not isinstance(node.args[0].func.value, ast.Name)
                or node.args[0].func.value.id != "json"
                or node.args[0].func.attr != "dumps"
                or len(node.args[0].args) != 1
                or node.args[0].keywords
            ):
                raise ValueError("unsupported print form in typed script")
            json_symbol = self.scope[-1].lookup("json")
            module_json = self.scope[0].lookup("json")
            if not json_symbol.is_global() or module_json.is_assigned():
                raise ValueError("json shadowing is unsupported")
            self.count += 1
            return ast.copy_location(
                ast.Call(
                    func=ast.Name(id="_receipt_emit", ctx=ast.Load()),
                    args=node.args[0].args,
                    keywords=[],
                ),
                node,
            )
        return node


def _instrument(
    script: str, root: Path, operation_id: str, fingerprint: str, endpoint_hash: str
) -> str:
    tree = ast.parse(script)
    runs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    if len(runs) != 1:
        raise ValueError("typed script must define exactly one run function")
    if any(isinstance(n, ast.Name) and n.id.startswith("_receipt_") for n in ast.walk(tree)):
        raise ValueError("reserved receipt identifier in typed script")
    worker = _Instrument(script)
    tree = worker.visit(tree)
    if not worker.count:
        raise ValueError("typed script has no supported JSON output")
    runs[0].name = "_receipt_original_run"
    ast.fix_missing_locations(tree)
    config = {
        "root": str(root.resolve()),
        "id": operation_id,
        "fingerprint": fingerprint,
        "endpoint_hash": endpoint_hash,
    }
    output = ast.unparse(tree) + "\n" + _RUNTIME.replace("__RECEIPT_CONFIG__", repr(config))
    compile(output, "<instrumented-typed-script>", "exec")
    return output


_RUNTIME = """
import json as _receipt_json, os as _receipt_os, tempfile as _receipt_tempfile
_receipt_config = __RECEIPT_CONFIG__
_receipt_outputs = []

def _receipt_emit(value):
    # Capture the same JSON value as at the original print call, even if the
    # script later mutates its Python object before returning.
    _receipt_outputs.append(_receipt_json.loads(_receipt_json.dumps(value)))

def _receipt_write(kind, data):
    data.update({'version': 1, 'id': _receipt_config['id'], 'fingerprint': _receipt_config['fingerprint'],
                 'endpoint_hash': _receipt_config['endpoint_hash']})
    raw = _receipt_json.dumps(data, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > 262144: raise ValueError('receipt exceeds size limit')
    root = _receipt_config['root']
    fd, temp = _receipt_tempfile.mkstemp(prefix='.receipt-', dir=root)
    try:
        with _receipt_os.fdopen(fd, 'wb') as f:
            f.write(raw); f.flush(); _receipt_os.fsync(f.fileno())
        _receipt_os.replace(temp, _receipt_os.path.join(root, _receipt_config['id'] + '.' + kind + '.json'))
    finally:
        if _receipt_os.path.exists(temp): _receipt_os.unlink(temp)

def run(_ctx):
    claim = _receipt_os.path.join(_receipt_config['root'], _receipt_config['id'] + '.claim')
    try:
        claim_fd = _receipt_os.open(claim, _receipt_os.O_CREAT | _receipt_os.O_EXCL | _receipt_os.O_WRONLY, 0o600)
    except FileExistsError:
        terminal = _receipt_os.path.join(_receipt_config['root'], _receipt_config['id'] + '.terminal.json')
        if _receipt_os.path.exists(terminal): return
        raise RuntimeError('operation already claimed; outcome unknown')
    _receipt_os.close(claim_fd)
    try:
        import adsk.core
        doc = adsk.core.Application.get().activeDocument
        doc_id = str(doc.creationId) if doc and doc.creationId else None
    except Exception:
        doc_id = None
    # This must persist before executing the typed operation.
    _receipt_write('started', {'document_creation_id': doc_id, 'session_evidence': 'unavailable'})
    try:
        _receipt_original_run(_ctx)
        if len(_receipt_outputs) != 1 or not isinstance(_receipt_outputs[0], dict) or type(_receipt_outputs[0].get('ok')) is not bool:
            raise ValueError('typed script did not emit exactly one boolean-status result')
        _receipt_write('terminal', {'document_creation_id': doc_id, 'declared_result': _receipt_outputs[0]})
    except BaseException as exc:
        try: _receipt_write('failed', {'document_creation_id': doc_id, 'exception_type': type(exc).__name__})
        except Exception: pass
        raise
"""


def _entry(root: Path, operation_id: str) -> dict[str, Any] | None:
    base = _read(root / (operation_id + ".json"))
    if base is None:
        return None
    if (
        base.get("id") != operation_id
        or not isinstance(base.get("fingerprint"), str)
        or not isinstance(base.get("endpoint_hash"), str)
        or type(base.get("read_only")) is not bool
        or isinstance(base.get("created_at"), bool)
        or not isinstance(base.get("created_at"), (int, float))
        or not math.isfinite(base["created_at"])
    ):
        raise ValueError("invalid receipt identity")
    if "receipt_context" in base:
        base["receipt_context"] = _workspace_context(base["receipt_context"])
    for kind in ("started", "terminal", "failed", "reconciled", "not_started"):
        item = _read(root / (operation_id + "." + kind + ".json"))
        if item is not None:
            if (
                item.get("id") != operation_id
                or item.get("fingerprint") != base.get("fingerprint")
                or item.get("endpoint_hash") != base.get("endpoint_hash")
            ):
                raise ValueError("receipt identity mismatch")
            base[kind] = item
    if "terminal" in base:
        declared = base["terminal"].get("declared_result")
        if not isinstance(declared, dict) or type(declared.get("ok")) is not bool:
            raise ValueError("invalid terminal result")
        if "started" not in base or base["terminal"].get("document_creation_id") != base[
            "started"
        ].get("document_creation_id"):
            raise ValueError("receipt document mismatch")
    if "not_started" in base and ("started" in base or "terminal" in base):
        raise ValueError("contradictory receipt")
    return base


def _unresolved(entry: dict[str, Any]) -> bool:
    if "reconciled" in entry:
        return False
    if "failed" in entry:
        return True
    if "not_started" in entry:
        return False
    terminal = entry.get("terminal")
    if not isinstance(terminal, dict) or not isinstance(terminal.get("declared_result"), dict):
        return True
    result = terminal["declared_result"]
    return not (
        result.get("ok") is True
        or (result.get("ok") is False and result.get("mutation_state") == "not_started")
    )


def _error(code: str, message: str, *, outcome: str = "not_started") -> Envelope:
    return Envelope(
        ok=False,
        error=code,
        message=message,
        state={
            "execution_outcome": outcome,
            "model_may_have_changed": outcome == "unknown",
            "retry_safe": outcome == "not_started",
        },
    )


def get_status(operation_id: str, *, journal_dir: str | os.PathLike[str] | None = None) -> Envelope:
    try:
        if operation_id is None:
            raise ValueError("operation_id is required")
        operation_id = _id(operation_id)
        entry = _entry(_root(journal_dir), operation_id)
        if entry is None:
            return _error("receipt_not_found", "No local receipt exists")
        terminal = entry.get("terminal")
        status = (
            "reconciled"
            if "reconciled" in entry
            else "not_started"
            if "not_started" in entry
            else "unknown"
            if terminal and "failed" in entry
            else "declared_not_started"
            if terminal
            and terminal["declared_result"].get("mutation_state") == "not_started"
            and terminal["declared_result"]["ok"] is False
            else "completed"
            if not _unresolved(entry)
            else "declared_failure_unreconciled"
            if terminal
            else "unknown"
        )
        doc = (entry.get("started") or {}).get("document_creation_id")
        return Envelope(
            ok=True,
            result={
                "operation_id": operation_id,
                "status": status,
                "read_only": entry["read_only"],
                "document_creation_id": doc,
                "session_evidence": (entry.get("started") or {}).get(
                    "session_evidence", "unavailable"
                ),
                "endpoint_hash": entry["endpoint_hash"],
                "request_fingerprint": entry["fingerprint"],
                **(
                    {"receipt_context": entry["receipt_context"]}
                    if "receipt_context" in entry
                    else {}
                ),
                "original_document_identity_unavailable": (entry.get("reconciled") or {}).get(
                    "original_document_identity_unavailable"
                ),
                "declared_result": terminal.get("declared_result") if terminal else None,
                "meaning": "Recorded script declaration, not proof of current geometry or rollback",
            },
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _error("receipt_unavailable", type(exc).__name__, outcome="unknown")


def reconcile(
    operation_id: str,
    *,
    endpoint: str,
    expected_document: str,
    probe_operation_id: str,
    acknowledgement: bool,
    allow_missing_identity: bool = False,
    evidence: str,
    journal_dir: str | os.PathLike[str] | None = None,
) -> Envelope:
    try:
        if operation_id is None:
            raise ValueError("operation_id is required")
        operation_id = _id(operation_id)
        if probe_operation_id is None:
            raise ValueError("probe_operation_id is required")
        probe_operation_id = _id(probe_operation_id)
        if (
            acknowledgement is not True
            or not isinstance(evidence, str)
            or not evidence.strip()
            or len(evidence.encode("utf-8")) > 2048
        ):
            raise ValueError("explicit acknowledgement and bounded evidence are required")
        if type(allow_missing_identity) is not bool:
            raise ValueError("allow_missing_identity must be boolean")
        if not isinstance(expected_document, str) or not expected_document:
            raise ValueError("expected_document is required")
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("endpoint is required")
        root = _root(journal_dir)
        endpoint_hash = _hash(endpoint)
        with _endpoint_lock(root, endpoint_hash, 2.0):
            entry = _entry(root, operation_id)
            if entry is None or entry.get("endpoint_hash") != endpoint_hash:
                raise ValueError("receipt or endpoint mismatch")
            if entry.get("read_only"):
                raise ValueError("cannot reconcile a read-only operation")
            if "reconciled" in entry:
                prior = entry["reconciled"]
                return Envelope(
                    ok=True,
                    result={
                        "operation_id": operation_id,
                        "status": "reconciled",
                        "original_document_identity_unavailable": prior.get(
                            "original_document_identity_unavailable", False
                        ),
                        "meaning": "Previously recorded reconciliation; operation success and rollback are not established",
                    },
                )
            if not _unresolved(entry):
                raise ValueError("operation is already resolved")
            if "receipt_context" in entry:
                return _operation_error(
                    "workspace_reconciliation_required",
                    "Inspect the operation with get_operation_status. Releasing its write barrier "
                    "requires workspace-bound recovery.",
                    operation_id,
                    read_only=False,
                    outcome="unknown",
                )
            actual = (entry.get("started") or {}).get("document_creation_id")
            if actual and actual != expected_document:
                raise ValueError("recorded document identity mismatched")
            if not actual and not allow_missing_identity:
                raise ValueError("recorded document identity unavailable")
            probe = _entry(root, probe_operation_id)
            now = time.time()
            created = probe.get("created_at") if probe else None
            if (
                not probe
                or not probe.get("read_only")
                or probe.get("endpoint_hash") != endpoint_hash
                or _unresolved(probe)
                or "terminal" not in probe
                or probe["terminal"]["declared_result"]["ok"] is not True
                or (probe.get("started") or {}).get("document_creation_id") != expected_document
                or not isinstance(created, (int, float))
                or isinstance(created, bool)
                or created < entry.get("created_at", float("inf"))
                or not 0 <= now - created <= 60
            ):
                raise ValueError("fresh matching read-only probe required")
            _atomic_write(
                root / (operation_id + ".reconciled.json"),
                {
                    "version": _VERSION,
                    "id": operation_id,
                    "fingerprint": entry["fingerprint"],
                    "endpoint_hash": endpoint_hash,
                    "evidence": evidence,
                    "probe_operation_id": probe_operation_id,
                    "original_document_identity_unavailable": not bool(actual),
                },
            )
        return Envelope(
            ok=True,
            result={
                "operation_id": operation_id,
                "status": "reconciled",
                "original_document_identity_unavailable": not bool(actual),
                "meaning": "Barrier released; operation success and rollback are not established",
            },
        )
    except TimeoutError as exc:
        return _error("receipt_busy", str(exc))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _error("reconciliation_refused", type(exc).__name__, outcome="unknown")


def _terminal_result(
    entry: dict[str, Any], operation_id: str, *, read_only: bool, recovered: bool
) -> Envelope:
    if "failed" in entry:
        return _operation_error(
            "receipt_unresolved", "Terminal and failure records conflict; outcome is unknown",
            operation_id, read_only=read_only, outcome="unknown",
        )
    result = entry["terminal"]["declared_result"]
    state = {
        "operation_id": operation_id,
        "document_creation_id": (entry.get("started") or {}).get("document_creation_id"),
        "receipt_evidence": "historical script declaration; current document state is not established",
        "receipt_status": (
            "reconciled"
            if "reconciled" in entry
            else "completed"
            if result["ok"]
            else "declared_not_started"
            if result.get("mutation_state") == "not_started"
            else "declared_failure_unreconciled"
        ),
        "recovered_from_receipt": recovered,
        "execution_outcome": "completed"
        if result["ok"]
        else "not_started"
        if result.get("mutation_state") == "not_started"
        else "unknown",
        "model_may_have_changed": not read_only
        and (result["ok"] or result.get("mutation_state") != "not_started"),
        "retry_safe": read_only
        or (not result["ok"] and result.get("mutation_state") == "not_started"),
    }
    if result["ok"]:
        return Envelope(ok=True, message=json.dumps(result), state=state)
    return Envelope(
        ok=False,
        error=result.get("error") or "declared_failure",
        result=result,
        message=json.dumps(result),
        state=state,
    )


def _operation_error(
    code: str,
    message: str,
    operation_id: str,
    *,
    read_only: bool,
    outcome: str = "not_started",
    barrier_id: str | None = None,
) -> Envelope:
    state = {
        "operation_id": operation_id,
        "execution_outcome": outcome,
        "model_may_have_changed": not read_only and outcome == "unknown",
        "retry_safe": read_only or outcome == "not_started",
    }
    if barrier_id is not None:
        state["barrier_operation_id"] = barrier_id
    return Envelope(ok=False, error=code, message=message, state=state)


def run_typed(
    send: Callable[..., Envelope],
    script: str,
    endpoint: str,
    timeout: float,
    operation_id: str | None = None,
    read_only: bool = False,
    journal_dir: str | os.PathLike[str] | None = None,
    *,
    recovery_probe: bool = False,
    receipt_context: dict[str, Any] | None = None,
) -> Envelope:
    supplied_operation_id = operation_id is not None
    try:
        operation_id = _id(operation_id)
        if (
            not isinstance(script, str)
            or not script
            or not isinstance(endpoint, str)
            or not endpoint
        ):
            raise ValueError("script and endpoint are required")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be positive and finite")
        if type(read_only) is not bool:
            raise ValueError("read_only must be boolean")
        if type(recovery_probe) is not bool or (recovery_probe and not read_only):
            raise ValueError("recovery_probe requires a read-only internal probe")
        context = _workspace_context(receipt_context) if receipt_context is not None else None
        root = _root(journal_dir)
        endpoint_hash = _hash(endpoint)
        fingerprint = _fingerprint(endpoint, script, read_only, context)
        instrumented = _instrument(script, root, operation_id, fingerprint, endpoint_hash)
    except (ValueError, SyntaxError, OverflowError) as exc:
        return _error("receipt_invalid_input", str(exc))

    dispatched = False
    try:
        # Both reads and writes serialize against Fusion's shared interpreter;
        # reads ignore unresolved write barriers once they acquire the lock.
        with _endpoint_lock(root, endpoint_hash, timeout):
            with _endpoint_lock(root, "journal-capacity", timeout):
                existing = _entry(root, operation_id)
                if existing is not None:
                    if (
                        existing.get("fingerprint") != fingerprint
                        or existing.get("endpoint_hash") != endpoint_hash
                        or existing.get("receipt_context") != context
                    ):
                        return _operation_error(
                            "receipt_conflict",
                            "Operation ID is already bound to different input",
                            operation_id,
                            read_only=read_only,
                        )
                    if "terminal" in existing:
                        return _terminal_result(
                            existing, operation_id, read_only=read_only, recovered=True
                        )
                    if "reconciled" in existing:
                        result = _operation_error(
                            "receipt_reconciled",
                            "Prior outcome was reconciled; this ID will not be redispatched",
                            operation_id,
                            read_only=read_only,
                            outcome="unknown",
                        )
                        result.state["receipt_status"] = "reconciled"
                        return result
                    if "not_started" in existing:
                        return _operation_error(
                            "receipt_not_started",
                            "Prior dispatch did not start; use a new operation ID",
                            operation_id,
                            read_only=read_only,
                        )
                    return _operation_error(
                        "receipt_unresolved",
                        "Prior outcome requires reconciliation",
                        operation_id,
                        read_only=read_only,
                        outcome="unknown",
                    )
                manifests = [path for path in root.glob("*.json") if path.name.count(".") == 1]
                # Reserve capacity for internal recovery probes. Completion records,
                # claims and failed records are bounded by this manifest count.
                capacity = _MAX_ENTRIES if recovery_probe else _MAX_ENTRIES - _RECOVERY_RESERVE
                if len(manifests) >= capacity:
                    return _operation_error(
                        "receipt_capacity",
                        "Receipt journal is full",
                        operation_id,
                        read_only=read_only,
                    )
                if not read_only:
                    for path in manifests:
                        if path.name.count(".") != 1:
                            continue
                        try:
                            other_id = _id(path.stem)
                            other = _entry(root, other_id)
                        except (ValueError, OSError, json.JSONDecodeError):
                            return _operation_error(
                                "receipt_unavailable",
                                "Journal contains an invalid record",
                                operation_id,
                                read_only=False,
                                outcome="unknown",
                            )
                        if (
                            other
                            and not other.get("read_only", False)
                            and other.get("endpoint_hash") == endpoint_hash
                            and _unresolved(other)
                        ):
                            result = _operation_error(
                                "receipt_barrier",
                                "Prior operation requires reconciliation",
                                operation_id,
                                read_only=False,
                                outcome="not_started",
                                barrier_id=other_id,
                            )
                            result.state["retry_safe"] = False
                            return result
                _write_exclusive(
                    root / (operation_id + ".json"),
                    {
                        "version": _VERSION,
                        "id": operation_id,
                        "endpoint_hash": endpoint_hash,
                        "fingerprint": fingerprint,
                        "read_only": read_only,
                        "recovery_probe": recovery_probe,
                        "state": "dispatch_recorded",
                        "created_at": time.time(),
                        **({"receipt_context": context} if context is not None else {}),
                    },
                )
            dispatched = True
            try:
                env = send(instrumented, read_only=read_only)
            except Exception as exc:
                entry = _entry(root, operation_id)
                if entry and "terminal" in entry:
                    return _terminal_result(
                        entry, operation_id, read_only=read_only, recovered=True
                    )
                return _operation_error(
                    "receipt_dispatch_unknown",
                    type(exc).__name__,
                    operation_id,
                    read_only=read_only,
                    outcome="unknown",
                )
            entry = _entry(root, operation_id)
            if entry and "terminal" in entry:
                try:
                    response_matches = (
                        env.ok and json.loads(env.message) == entry["terminal"]["declared_result"]
                    )
                except (TypeError, ValueError):
                    response_matches = False
                return _terminal_result(
                    entry, operation_id, read_only=read_only, recovered=not response_matches
                )
            # A definitive transport preflight rejection is safe only when no
            # script-side started record contradicts it.
            if (
                not env.ok
                and (env.state or {}).get("execution_outcome") == "not_started"
                and entry
                and "started" not in entry
                and "failed" not in entry
            ):
                _atomic_write(
                    root / (operation_id + ".not_started.json"),
                    {
                        "version": _VERSION,
                        "id": operation_id,
                        "fingerprint": fingerprint,
                        "endpoint_hash": endpoint_hash,
                    },
                )
                env.state = {
                    **(env.state or {}),
                    "operation_id": operation_id,
                    "receipt_status": "not_started",
                }
                return env
            state = {
                **(env.state or {}),
                "operation_id": operation_id,
                "receipt_status": "unknown",
                "execution_outcome": "unknown",
                "model_may_have_changed": not read_only,
                "retry_safe": read_only,
            }
            return Envelope(
                ok=False,
                error=env.error or "receipt_unconfirmed",
                message=env.message,
                result=env.result,
                traceback=env.traceback,
                state=state,
            )
    except TimeoutError as exc:
        return _operation_error(
            "receipt_busy",
            str(exc),
            operation_id,
            read_only=read_only,
            outcome="unknown" if supplied_operation_id else "not_started",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _operation_error(
            "receipt_unavailable",
            type(exc).__name__,
            operation_id,
            read_only=read_only,
            outcome="unknown" if dispatched else "not_started",
        )
