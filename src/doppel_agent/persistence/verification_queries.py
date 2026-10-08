"""Scoped, bounded SQL-read-only queries over existing manual evidence.

No stores, migrations, expiration/reconciliation, config/project reads or events.
mode=ro/query_only prohibit application data writes, not SQLite WAL shared-memory
coordination or sidecar creation. Never use immutable=1 for these live databases.
This is not an OS sandbox, lossless output identity, or model/test-quality proof.
"""

from __future__ import annotations

import sqlite3
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from .inverse_reviews import identifier
from .verification_reviews import MAX_PLAN_BYTES, MAX_RESULT_BYTES, VerificationReviewStore


MAX_QUERY_ROWS = 16
MAX_QUERY_BYTES = 8 * 1024 * 1024
QUERY_SECONDS = 3
RUN_STATES = {"queued", "running", "interrupted", "completed", "failed", "cancelled", "interrupted_expired"}
SAFE_ERRORS = {
    "verification_query_unavailable",
    "verification_query_budget",
    "verification_query_source_unavailable",
    "verification_review_scope_unavailable",
    "inverse_review_scope_unavailable",
    "verification_review_unavailable",
    "verification_evidence_budget",
    "verification_evidence_unavailable",
    "verification_result_unavailable",
    "verification_completion_requires_sealed_results",
    "invalid_verification_selection",
    "changes_evidence_unavailable",
    "inverse_review_unavailable",
    "inverse_review_invalid_proposal",
    "patch_evidence_unavailable",
    "patch_receipt_unavailable",
    "patch_receipt_page_budget_exceeded",
    "invalid_patch_receipt_page",
}


def _check(deadline):
    if monotonic() >= deadline:
        raise ValueError("verification_query_budget")


def _safe_database(path, deadline):
    original = Path(path).absolute()
    _check(deadline)
    # Fixed owned metadata paths, no links/hardlinks/reparse aliases. Checks are
    # application-level admission, not atomic against a hostile path replacer.
    if len(original.parents) > 64 or original.resolve(strict=True) != original:
        raise ValueError("verification_query_unavailable")
    for parent in original.parents:
        _check(deadline)
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("verification_query_unavailable")
    for candidate in (
        original,
        *(original.with_name(original.name + suffix) for suffix in ("-wal", "-shm", "-journal")),
    ):
        _check(deadline)
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == original:
                raise
            continue
        # Windows SQLite sidecar disposal can briefly publish a zero-link
        # regular file before removing its name. Do NOT accept that sample:
        # reobserve this optional path once, under the SAME budget. Absence is
        # allowed; every returned replacement still undergoes all checks below.
        # Main DB, aliases/reparse/nonregular files and persistent zero links
        # never get this allowance. No SQL/model retry or atomic-path claim.
        if (candidate != original and info.st_nlink == 0
                and stat.S_ISREG(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400):
            _check(deadline)
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or candidate.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("verification_query_unavailable")
    return original


class ExistingEvidenceQueries:
    required_tables: tuple[str, ...] = ()

    def __init__(self, database: Path, runtime_database: Path):
        # Store paths only: construction itself never touches a database.
        self.database = Path(database)
        self.runtime_database = Path(runtime_database)

    @contextmanager
    def _connection(self):
        deadline = monotonic() + QUERY_SECONDS
        db = None
        try:
            original = _safe_database(self.database, deadline)
            runtime = _safe_database(self.runtime_database, deadline)
            if original.parent != runtime.parent or original == runtime:
                raise ValueError("verification_query_unavailable")
            db = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True, timeout=1)
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("ATTACH DATABASE ? AS registered_runtime", (runtime.as_uri() + "?mode=ro",))
            db.execute("BEGIN")
            # Names are internal subclass constants only, never request fields.
            placeholders = ",".join("?" for _ in self.required_tables)
            tables = db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (" + placeholders + ")",
                self.required_tables,
            ).fetchone()[0]
            registered = db.execute("""SELECT COUNT(*) FROM registered_runtime.sqlite_master
                WHERE type='table' AND name='runtime_runs'""").fetchone()[0]
            if not self.required_tables or tables != len(self.required_tables) or registered != 1:
                raise ValueError("verification_query_unavailable")
            _check(deadline)
            yield db, deadline
        except (sqlite3.Error, OSError):
            # Raw path/SQLite text is not diagnostic output.
            raise ValueError("verification_query_unavailable") from None
        except ValueError as exc:
            # Corrupt Unicode/JSON/date/framing errors must not echo any stored
            # private argv/output/path fragment. Keep only fixed stable codes.
            code = str(exc) if str(exc) in SAFE_ERRORS else "verification_query_unavailable"
            raise ValueError(code) from None
        except (TypeError, RecursionError, RuntimeError):
            raise ValueError("verification_query_unavailable") from None
        finally:
            if db is not None:
                try:
                    db.close()
                except sqlite3.Error:
                    raise ValueError("verification_query_unavailable") from None

    @staticmethod
    def _source(db, run_id):
        # Registration only, never fetch prompts/events/settings or use runs.get
        # (which uses a writable connection). Attached WAL databases are not an
        # atomic cross-DB snapshot; no quiescence/admission proof is inferred.
        row = db.execute(
            """SELECT
            CASE WHEN typeof(status)='text' AND length(CAST(status AS BLOB))<=32 THEN status END AS status,
            CASE WHEN typeof(lease_active)='integer' THEN lease_active END AS lease_active
            FROM registered_runtime.runtime_runs WHERE run_id=?""",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["status"] not in RUN_STATES or row["lease_active"] not in {0, 1}:
            raise ValueError("verification_query_source_unavailable")
        return {"status": row["status"], "lease_active": bool(row["lease_active"])}


class VerificationQueries(ExistingEvidenceQueries):
    required_tables = ("verification_reviews", "verification_command_steps")

    @staticmethod
    def _size(db, run_id, review_id):
        row = db.execute(
            """SELECT length(CAST(plan_json AS BLOB)),length(CAST(selection_json AS BLOB)),
            COALESCE((SELECT SUM(length(CAST(result_json AS BLOB))) FROM verification_command_steps
                      WHERE review_id=?),0),
            (SELECT COUNT(*) FROM verification_command_steps WHERE review_id=?)
            FROM verification_reviews WHERE source_run_id=? AND review_id=?""",
            (review_id, review_id, run_id, review_id),
        ).fetchone()
        if row is None:
            raise ValueError("verification_review_scope_unavailable")
        if (
            any(type(value) is not int or value < 0 for value in row)
            or row[0] > MAX_PLAN_BYTES
            or row[1] > 8192
            or row[2] > MAX_RESULT_BYTES
            or row[3] > 16
        ):
            raise ValueError("verification_evidence_budget")
        return sum(row[:3])

    @staticmethod
    def _presentation(view, source, now):
        pending_expired = view["status"] == "pending" and datetime.fromisoformat(view["expires_at"]) <= now
        sealed = sum(step["result"] is not None for step in view["steps"])
        planned = len(view["plan"]["commands"])
        view["lifecycle"] = {
            "stored_status": view["status"],
            "effective_status": "expired" if pending_expired else view["status"],
            "pending_expired": pending_expired,
            "pending_unexpired": view["status"] == "pending" and not pending_expired,
            "approval_available": False,  # A read is never fresh permission/admission/config validation.
            "retry_available": False,  # Never reopen a consumed review through a query.
            "planned_commands": planned,
            "sealed_steps": sealed,
            "remaining_commands": planned - sealed,
            "outcome_unknown": view["status"] in {"running", "indeterminate"} or view["has_unknown_command"],
            "all_planned_attempts_sealed": sealed == planned and not view["has_unknown_command"],
            "commands_completed": sealed == planned
            and all(
                step["result"]["exit_code"] is not None
                for step in view["steps"]
                if step["result"] is not None
            ),
        }
        view["provenance"] = {
            "source_registration": "registered_run",
            "source_run": source,
            "patch_success": "not_inferred_from_verification",
            "plan": "stored_exact_review_not_current_config_validation",
            "output": "stored_sealed_attempts_not_whole_project_acceptance",
            "cross_database_snapshot": "not_atomic",
        }
        view["output"] = {
            "sensitive": True,
            "globally_redacted": False,
            "encoding": "utf8_replacement_presentation_not_lossless_bytes",
            "aggregate_evidence_limit_bytes": MAX_RESULT_BYTES,
            "limits": [
                "not_an_os_sandbox",
                "executable_and_dependencies_not_pinned",
                "current_workspace_not_original_patch_snapshot",
                "no_global_s8_redaction",
            ],
        }
        return view

    def detail(self, run_id: str, review_id: str):
        identifier(run_id, uuid=True)
        identifier(review_id, uuid=True)
        with self._connection() as (db, deadline):
            source = self._source(db, run_id)
            size = self._size(db, run_id, review_id)
            row = VerificationReviewStore._row(db, run_id, review_id)
            view = VerificationReviewStore._view(db, row)
            _check(deadline)
            view = self._presentation(view, source, datetime.now(UTC))
            view["query"] = {
                "sql_read_only": True,
                "decode_bytes": size,
                "filesystem_zero_write_guarantee": False,
            }
            return view

    def list(
        self,
        run_id: str,
        *,
        limit: int = MAX_QUERY_ROWS,
        after_id: str = "",
        tool_call_id: str | None = None,
        patch_id: str | None = None,
    ):
        identifier(run_id, uuid=True)
        if type(limit) is not int or not 1 <= limit <= MAX_QUERY_ROWS:
            raise ValueError("verification_query_limit")
        if after_id != "":
            identifier(after_id, uuid=True)
        if tool_call_id is not None:
            identifier(tool_call_id)
        if patch_id is not None:
            identifier(patch_id, uuid=True)
        with self._connection() as (db, deadline):
            source = self._source(db, run_id)
            clauses, params = ["source_run_id=?", "review_id>?"], [run_id, after_id]
            if tool_call_id is not None:
                clauses.append("source_tool_call_id=?")
                params.append(tool_call_id)
            if patch_id is not None:
                clauses.append("source_patch_id=?")
                params.append(patch_id)
            # Only fixed SQL identifiers/clauses above, all external values bound.
            rows = db.execute(
                """SELECT CASE WHEN typeof(review_id)='text'
                AND length(CAST(review_id AS BLOB))=32 THEN review_id END AS review_id
                FROM verification_reviews WHERE """
                + " AND ".join(clauses)
                + " ORDER BY review_id LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            items, consumed, budget_limited = [], 0, False
            now = datetime.now(UTC)
            for candidate in rows[:limit]:
                _check(deadline)
                review = identifier(candidate["review_id"], uuid=True)
                size = self._size(db, run_id, review)
                if consumed + size > MAX_QUERY_BYTES:
                    budget_limited = True
                    break
                row = VerificationReviewStore._row(db, run_id, review)
                view = self._presentation(VerificationReviewStore._view(db, row), source, now)
                # Validate the bounded actual framing before projecting away all
                # plan/argv/name/stdout/stderr. Summary never claims unread proof.
                items.append(
                    {
                        key: view[key]
                        for key in (
                            "review_id",
                            "operation_id",
                            "operation_kind",
                            "source",
                            "target",
                            "created_at",
                            "expires_at",
                            "status",
                            "error_code",
                            "success",
                            "has_unknown_command",
                            "evidence",
                            "lifecycle",
                            "provenance",
                        )
                    }
                )
                consumed += size
            _check(deadline)
            has_more = budget_limited or len(rows) > limit
            return {
                "items": items,
                "has_more": has_more,
                "next_after_id": items[-1]["review_id"] if has_more and items else None,
                "budget_limited": budget_limited,
                "decode_bytes": consumed,
                "sql_read_only": True,
                "filesystem_zero_write_guarantee": False,
                "order": "review_id_keyset_not_chronological",
            }
