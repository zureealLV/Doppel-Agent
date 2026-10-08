"""Idempotency ledger for graph tool side effects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock, get_ident
from typing import Any

from .evidence_json import loads as evidence_loads
from .owned import await_durable


class ToolLedgerAdmissionError(ValueError):
    """Known input/previous-failed refusal; not failed local receipt IO."""


class ToolLedgerPersistenceError(RuntimeError):
    """Original SQL lifetime failure, never private path/SQL/exception text."""

    def __init__(self, source: ToolExecutionLedger | None = None):
        super().__init__('tool_ledger_unresolved')
        self.source = source  # Private original source retention, never an API payload.


@dataclass
class _LedgerConnectionLifetime:
    connection: Any = None
    owner_thread: int = field(default_factory=get_ident)
    connect_attempted: bool = False
    transaction_entered: bool = False
    transaction_exit_returned: bool = False
    close_returned: bool = False


class ToolExecutionLedger:
    def __init__(self, database: Path, *, failure: Callable[[], None] | None = None,
                 cleanup_failure: Callable[[ToolExecutionLedger], None] | None = None):
        self.database = database
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._failed = Event()
        self._cleanup_uncertain = Event()
        self._lifetime_lock = Lock()
        self._unresolved_connections: dict[int, _LedgerConnectionLifetime] = {}
        try:
            database.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, sqlite3.Error):
            self._mark_failed()
            raise ToolLedgerPersistenceError(self) from None
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tool_executions (
                    run_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
                    result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (run_id, tool_call_id)
                )
            """)
            # Additive, same owned ledger. Legacy executions remain untouched
            # and do not acquire invented preimages/attribution retrospectively.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS patch_effect_receipts (
                    run_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    patch_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, tool_call_id),
                    FOREIGN KEY (run_id, tool_call_id) REFERENCES tool_executions(run_id,tool_call_id) ON DELETE CASCADE
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(patch_effect_receipts)")}
            for name, definition in (("summary_json", "TEXT NOT NULL DEFAULT ''"), ("preimage_expired_at", "TEXT")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE patch_effect_receipts ADD COLUMN {name} {definition}")
            connection.execute("CREATE INDEX IF NOT EXISTS ix_patch_private_retention ON patch_effect_receipts(preimage_expired_at,run_id,tool_call_id)")

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    @property
    def cleanup_uncertain(self) -> bool:
        return self._cleanup_uncertain.is_set()

    def _mark_failed(self) -> None:
        if self._failed.is_set():
            return
        self._failed.set()
        if self._failure is not None:
            try:
                self._failure()  # Same original owner fault BEFORE publication/close; no IO.
            except BaseException:
                pass

    def _retain_uncertain(self, frame: _LedgerConnectionLifetime) -> None:
        self._cleanup_uncertain.set()
        with self._lifetime_lock:
            self._unresolved_connections[id(frame)] = frame
        self._mark_failed()
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # Original failed constructor can be retained too.
            except BaseException:
                pass

    def check_cleanup(self) -> None:
        if self.cleanup_uncertain:
            raise ToolLedgerPersistenceError(self)

    @contextmanager
    def _connect(self):
        if self.failed:
            raise ToolLedgerPersistenceError(self)  # No later healthy connection/reset/retry.
        frame = _LedgerConnectionLifetime()
        frame.connect_attempted = True  # BEFORE original factory construction.
        try:
            connection = sqlite3.connect(self.database, timeout=5)
        except BaseException:
            # Opaque factory may allocate then fail without returning a handle.
            # Keep SAME original attempt, never fabricate closure from missing handle.
            self._retain_uncertain(frame)
            raise ToolLedgerPersistenceError(self) from None
        frame.connection = connection
        try:
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=5000")
                # SAME original transaction protocol expanded explicitly only to
                # distinguish actual failed exit from body validation, even when
                # exit raises the very same exception object as the body.
                connection.__enter__()
                frame.transaction_entered = True
            except BaseException:
                self._mark_failed()
                raise ToolLedgerPersistenceError(self) from None
            try:
                yield connection
            except BaseException as exc:
                storage_failure = isinstance(exc, (sqlite3.Error, OSError, ToolLedgerPersistenceError))
                if storage_failure:
                    self._mark_failed()
                try:
                    suppressed = connection.__exit__(type(exc), exc, exc.__traceback__)
                    frame.transaction_exit_returned = True
                except BaseException:
                    self._mark_failed()
                    raise ToolLedgerPersistenceError(self) from None
                if storage_failure:
                    raise ToolLedgerPersistenceError(self) from None
                if not suppressed:
                    raise  # Actual healthy rollback/close preserves known validation/cancellation.
            else:
                try:
                    connection.__exit__(None, None, None)
                    frame.transaction_exit_returned = True
                except BaseException:
                    self._mark_failed()
                    raise ToolLedgerPersistenceError(self) from None
        finally:
            try:
                connection.close()
                frame.close_returned = True
            except BaseException:
                self._retain_uncertain(frame)
                raise ToolLedgerPersistenceError(self) from None

    @staticmethod
    def _hash(arguments: dict) -> str:
        payload = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def execute_once(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        operation: Callable[[], str],
    ) -> tuple[str, bool]:
        arguments_hash = self._hash(arguments)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM tool_executions WHERE run_id=? AND tool_call_id=?",
                (run_id, tool_call_id),
            ).fetchone()
            if existing:
                if existing["tool_name"] != tool_name or existing["arguments_hash"] != arguments_hash:
                    raise ToolLedgerAdmissionError("tool call id was reused with different input")
                if existing["status"] == "completed":
                    return existing["result"], True
                if existing["status"] == "failed":
                    raise ToolLedgerAdmissionError("previous tool execution failed")
                self._mark_failed()  # Original reserved/unsealed outcome, never re-enter effect.
                raise RuntimeError("tool execution outcome is indeterminate after interruption")
            connection.execute(
                "INSERT INTO tool_executions(run_id,tool_call_id,tool_name,arguments_hash,status) VALUES(?,?,?,?,?)",
                (run_id, tool_call_id, tool_name, arguments_hash, "running"),
            )
        try:
            result = operation()
        except Exception as exc:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE tool_executions SET status='failed',error=? WHERE run_id=? AND tool_call_id=?",
                    (f"{type(exc).__name__}: tool execution failed", run_id, tool_call_id),
                )
            raise
        with self._connect() as connection:
            connection.execute(
                "UPDATE tool_executions SET status='completed',result=? WHERE run_id=? AND tool_call_id=?",
                (result, run_id, tool_call_id),
            )
        return result, False

    def _begin(self, run_id: str, tool_call_id: str, tool_name: str, arguments: dict) -> tuple[str | None, bool]:
        arguments_hash = self._hash(arguments)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM tool_executions WHERE run_id=? AND tool_call_id=?",
                (run_id, tool_call_id),
            ).fetchone()
            if existing:
                if existing["tool_name"] != tool_name or existing["arguments_hash"] != arguments_hash:
                    raise ToolLedgerAdmissionError("tool call id was reused with different input")
                if existing["status"] == "completed":
                    return existing["result"], True
                if existing["status"] == "failed":
                    raise ToolLedgerAdmissionError("previous tool execution failed")
                self._mark_failed()  # Sync patch/manual callers share this original boundary too.
                raise RuntimeError("tool execution outcome is indeterminate after interruption")
            connection.execute(
                "INSERT INTO tool_executions(run_id,tool_call_id,tool_name,arguments_hash,status) VALUES(?,?,?,?,?)",
                (run_id, tool_call_id, tool_name, arguments_hash, "running"),
            )
        return None, False

    def _finish(self, run_id: str, tool_call_id: str, *, result: str = "", error: str = "") -> None:
        status = "failed" if error else "completed"
        with self._connect() as connection:
            connection.execute(
                "UPDATE tool_executions SET status=?,result=?,error=? WHERE run_id=? AND tool_call_id=?",
                (status, result, error, run_id, tool_call_id),
            )

    def _patch_intent(self, run_id: str, tool_call_id: str, tool_name: str, arguments: dict, receipt) -> None:
        from ..workspace.patch_receipts import PatchReceipt

        receipt = PatchReceipt.from_dict(receipt.as_dict())
        if receipt.status != "prepared":
            raise ValueError("invalid_patch_intent")
        with self._connect() as connection:
            operation = connection.execute(
                "SELECT * FROM tool_executions WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id)
            ).fetchone()
            if (operation is None or operation["status"] != "running" or operation["tool_name"] != tool_name
                    or operation["arguments_hash"] != self._hash(arguments)):
                raise ValueError("patch_intent_requires_reserved_operation")
            connection.execute(
                "INSERT INTO patch_effect_receipts(run_id,tool_call_id,patch_id,receipt_json,created_at,summary_json) VALUES(?,?,?,?,?,?)",
                (run_id, tool_call_id, receipt.patch_id,
                 json.dumps(receipt.as_dict(), ensure_ascii=False, separators=(",", ":")), datetime.now(UTC).isoformat(),
                 json.dumps(receipt.as_dict(include_preimage=False), ensure_ascii=False, separators=(",", ":"))),
            )

    def _finish_patch(self, run_id: str, tool_call_id: str, receipt, *, result: str = "", error: str = "") -> None:
        from ..workspace.patch_receipts import PatchReceipt

        if receipt is not None:
            receipt = PatchReceipt.from_dict(receipt.as_dict())
        if not error and (receipt is None or receipt.status != "applied"):
            raise ValueError("patch_completion_requires_applied_receipt")
        with self._connect() as connection:
            operation = connection.execute(
                "SELECT status FROM tool_executions WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id)
            ).fetchone()
            if operation is None or operation["status"] != "running":
                raise ValueError("patch_completion_requires_reserved_operation")
            if receipt is not None:
                stored = connection.execute(
                    "SELECT patch_id FROM patch_effect_receipts WHERE run_id=? AND tool_call_id=?", (run_id, tool_call_id)
                ).fetchone()
                if stored is None or stored["patch_id"] != receipt.patch_id:
                    raise ValueError("patch_receipt_identity_mismatch")
                connection.execute(
                    "UPDATE patch_effect_receipts SET receipt_json=?,summary_json=? WHERE run_id=? AND tool_call_id=?",
                    (json.dumps(receipt.as_dict(), ensure_ascii=False, separators=(",", ":")),
                     json.dumps(receipt.as_dict(include_preimage=False), ensure_ascii=False, separators=(",", ":")), run_id, tool_call_id),
                )
            # Receipt/result seal is one DB transaction; it cannot make the
            # filesystem and SQLite globally atomic. Unsealed intent stays unknown.
            connection.execute(
                "UPDATE tool_executions SET status=?,result=?,error=? WHERE run_id=? AND tool_call_id=?",
                ("failed" if error else "completed", result, error, run_id, tool_call_id),
            )

    def execute_patch_once(self, run_id: str, tool_call_id: str, arguments: dict, service, proposal,
                           *, tool_name: str = "propose_patch") -> tuple[str, bool]:
        """Reserved, durable patch application; caller supplies actual approval/lease.

        B2 must use this INSTEAD OF wrapping the same key in execute_once, and
        run verification separately after sealing this patch effect. No Git,
        runtime, provider, approval inference or automatic recovery is done here.
        """
        if tool_name not in {"propose_patch", "inverse_patch"}:
            raise ValueError("invalid_patch_operation")
        bound = arguments.get("_doppel_patch", arguments) if isinstance(arguments, dict) else None
        if not isinstance(bound, dict) or self._hash(bound) != self._hash(proposal.as_dict()):
            raise ValueError("patch_operation_proposal_mismatch")
        if tool_name == "propose_patch" and any(change.content is None for change in proposal.changes):
            raise ValueError("patch_deletion_requires_inverse_operation")
        existing, replayed = self._begin(run_id, tool_call_id, tool_name, arguments)
        if replayed:
            return existing or "", True
        try:
            applied = service.apply(proposal, record_intent=lambda receipt: self._patch_intent(
                run_id, tool_call_id, tool_name, arguments, receipt,
            ))
        except Exception as exc:
            self._finish_patch(run_id, tool_call_id, getattr(exc, "patch_receipt", None),
                               error=f"{type(exc).__name__}: patch execution failed")
            raise
        result = json.dumps({"patch_id": applied.patch_id, "changed_paths": applied.changed_paths,
                             "unified_diff": proposal.unified_diff,
                             "patch_receipt": applied.receipt.as_dict(include_preimage=False)},
                            ensure_ascii=False, separators=(",", ":"))
        # If final persistence fails after writes, keep running+prepared intent.
        # Do not undo the applied effect, retry it or mark it safely absent.
        self._finish_patch(run_id, tool_call_id, applied.receipt, result=result)
        return result, False

    @staticmethod
    def _decode_patch_receipt(raw: str):
        from ..workspace.patch_receipts import PatchReceipt

        try:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4 * 1024 * 1024:
                raise ValueError("patch_receipt_unavailable")
            return PatchReceipt.from_dict(evidence_loads(raw))
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
            raise ValueError("patch_receipt_unavailable") from None

    def read_applied_patch(self, run_id: str, tool_call_id: str):
        """Private preimage lookup, never a public content/report endpoint."""
        with self._connect() as connection:
            row = connection.execute("""
                SELECT p.patch_id,CASE WHEN length(p.receipt_json)<=4194304 THEN p.receipt_json ELSE NULL END AS receipt_json,
                       p.preimage_expired_at,t.status,t.tool_name FROM patch_effect_receipts p
                JOIN tool_executions t USING(run_id,tool_call_id)
                WHERE p.run_id=? AND p.tool_call_id=?
            """, (run_id, tool_call_id)).fetchone()
        if row is None or row["status"] != "completed" or row["tool_name"] not in {"propose_patch", "inverse_patch"}:
            raise ValueError("patch_applied_receipt_unavailable")
        if row["preimage_expired_at"] is not None:
            raise ValueError("patch_preimage_expired")
        receipt = self._decode_patch_receipt(row["receipt_json"])
        if receipt.status != "applied" or receipt.patch_id != row["patch_id"]:
            raise ValueError("patch_applied_receipt_unavailable")
        return receipt

    def execution_status(self, run_id: str, tool_call_id: str) -> str | None:
        """Private operation-state query; no error/result/preimage projection."""
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM tool_executions WHERE run_id=? AND tool_call_id=?",
                                     (run_id, tool_call_id)).fetchone()
        if row is None:
            return None
        if row["status"] not in {"running", "completed", "failed"}:
            raise ValueError("patch_operation_unavailable")
        return row["status"]

    @classmethod
    def _historical_summary(cls, raw, summary, expired):
        from ..workspace.patch_receipts import receipt_summary

        if expired is None:
            return cls._decode_patch_receipt(raw).as_dict(include_preimage=False)
        try:
            observed = datetime.fromisoformat(expired)
            if observed.tzinfo is None or observed.utcoffset().total_seconds() != 0 or observed.isoformat() != expired:
                raise ValueError("patch_receipt_unavailable")
        except (TypeError, ValueError):
            raise ValueError("patch_receipt_unavailable") from None
        if raw != "" or not isinstance(summary, str) or len(summary.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError("patch_receipt_unavailable")
        try:
            return receipt_summary(evidence_loads(summary))
        except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
            raise ValueError("patch_receipt_unavailable") from None

    def read_patch_evidence(self, run_id: str, tool_call_id: str) -> dict | None:
        """Read sealed historical evidence only; never recover/retry an effect."""
        with self._connect() as connection:
            row = connection.execute("""SELECT t.tool_name,t.status,p.patch_id,p.preimage_expired_at,
                CASE WHEN length(CAST(t.result AS BLOB))<=4194304 THEN t.result END AS result,
                CASE WHEN length(CAST(p.receipt_json AS BLOB))<=4194304 THEN p.receipt_json END AS receipt_json,
                CASE WHEN length(CAST(p.summary_json AS BLOB))<=4194304 THEN p.summary_json END AS summary_json
                FROM tool_executions t LEFT JOIN patch_effect_receipts p USING(run_id,tool_call_id)
                WHERE t.run_id=? AND t.tool_call_id=?""", (run_id, tool_call_id)).fetchone()
        if row is None:
            return None
        return self._patch_evidence_row(row)

    @classmethod
    def _patch_evidence_row(cls, row):
        """Pure framing shared by writer compatibility and existing-only queries."""
        if row["tool_name"] not in {"propose_patch", "inverse_patch"} or row["status"] not in {"running", "completed", "failed"}:
            raise ValueError("patch_evidence_unavailable")
        summary = None if row["patch_id"] is None else cls._historical_summary(row["receipt_json"], row["summary_json"], row["preimage_expired_at"])
        if summary is not None and summary["patch_id"] != row["patch_id"]:
            raise ValueError("patch_evidence_unavailable")
        confirmed = row["status"] == "completed" and summary is not None and summary["status"] == "applied"
        result = None
        if confirmed:
            try:
                result = evidence_loads(row["result"])
                if (not isinstance(result, dict) or set(result) != {"patch_id", "changed_paths", "unified_diff", "patch_receipt"}
                        or result["patch_id"] != row["patch_id"] or result["patch_receipt"] != summary
                        or result["changed_paths"] != [file["path"] for file in summary["files"]]
                        or not isinstance(result["unified_diff"], str)):
                    raise ValueError("patch_evidence_unavailable")
            except (TypeError, ValueError, RecursionError):
                raise ValueError("patch_evidence_unavailable") from None
        return {"tool_name": row["tool_name"], "operation_status": row["status"], "confirmed_applied": confirmed,
                "receipt": summary, "result": result, "preimage_state": "expired" if row["preimage_expired_at"] is not None else "retained"}

    def list_patch_receipts(self, run_id: str, *, limit: int = 100, offset: int = 0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100_000:
            raise ValueError("invalid_patch_receipt_page")
        result, consumed = [], 0
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT p.run_id,p.tool_call_id,p.patch_id,p.created_at,
                       CASE WHEN length(CAST(p.receipt_json AS BLOB))<=4194304 THEN p.receipt_json ELSE NULL END AS receipt_json,
                       CASE WHEN length(CAST(p.summary_json AS BLOB))<=4194304 THEN p.summary_json END AS summary_json,
                       p.preimage_expired_at,t.status AS operation_status,t.tool_name FROM patch_effect_receipts p
                JOIN tool_executions t USING(run_id,tool_call_id) WHERE p.run_id=?
                ORDER BY p.created_at,p.tool_call_id LIMIT ? OFFSET ?
            """, (run_id, limit, offset))
            for row in rows:
                raw = row["receipt_json"]
                if not isinstance(raw, str):
                    raise ValueError("patch_receipt_unavailable")
                public_raw = row["summary_json"] if row["preimage_expired_at"] is not None else raw
                if not isinstance(public_raw, str):
                    raise ValueError("patch_receipt_unavailable")
                consumed += len(public_raw.encode("utf-8"))
                if consumed > 4 * 1024 * 1024:
                    raise ValueError("patch_receipt_page_budget_exceeded")
                receipt = self._historical_summary(raw, row["summary_json"], row["preimage_expired_at"])
                if receipt["patch_id"] != row["patch_id"] or row["tool_name"] not in {"propose_patch", "inverse_patch"}:
                    raise ValueError("patch_receipt_unavailable")
                result.append({"run_id": row["run_id"], "tool_call_id": row["tool_call_id"],
                               "tool_name": row["tool_name"], "operation_status": row["operation_status"],
                               "created_at": row["created_at"], "receipt": receipt,
                               "preimage_state": "expired" if row["preimage_expired_at"] is not None else "retained",
                               "preimage_expired_at": row["preimage_expired_at"],
                               "confirmed_applied": row["operation_status"] == "completed" and receipt["status"] == "applied",
                               "evidence": "durable_patch_effect_not_current_git_ownership"})
        return result

    async def aexecute_once(
        self,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        operation: Callable[[], Awaitable[str]],
        *, on_failure: Callable[[], None] | None = None,
    ) -> tuple[str, bool]:
        import asyncio

        def mark_failed():
            self._mark_failed()  # Default runtime callers fence SAME ledger/owner too.
            if on_failure is not None:
                try:
                    on_failure()  # SAME original worker/owner, before outcome publication.
                except BaseException:
                    pass

        async def phase(method, *args, **kwargs):
            def original_phase():
                try:
                    return method(*args, **kwargs)
                except ToolLedgerAdmissionError:
                    raise  # Known validation/status refusal, not lost receipt.
                except BaseException:
                    mark_failed()
                    raise
            return await await_durable(asyncio.to_thread(original_phase), on_cancel=mark_failed)

        existing, replayed = await phase(self._begin, run_id, tool_call_id, tool_name, arguments)
        if replayed:
            return existing or "", True
        try:
            result = await operation()
        except Exception as exc:
            await phase(
                self._finish,
                run_id,
                tool_call_id,
                error=f"{type(exc).__name__}: tool execution failed",
            )
            raise
        await phase(self._finish, run_id, tool_call_id, result=result)
        return result, False
