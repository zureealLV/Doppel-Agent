"""Bounded original receipt frames, allowlisted numbers/IDs only, no Git/FS IO.

Two independent SQL-read-only snapshots (runtime then ledger) are NOT atomic
with each other or with the preceding event report. Missing/corrupt evidence is
unknown/partial, never permission, current ownership, acceptance or drain proof.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from ..persistence.changes_queries import MAX_BLOB, PatchEvidenceQueries, _date
from ..persistence.inverse_reviews import InverseReviewStore, identifier
from ..persistence.verification_queries import QUERY_SECONDS, VerificationQueries, _check, _safe_database
from ..persistence.verification_reviews import VerificationReviewStore
from ..tasks.work_orders import MAX_ATTEMPTS_PER_TASK
from .run_report import MAX_SAFE, _integer
from .read_lifetime import ReportReadCleanupError, ReportReadSource


MAX_ROWS = 16
MAX_DECODE = 8 * 1024 * 1024
SECTIONS = ("patches", "inverses", "verifications", "work_orders")


def report_digest(kind, value):
    # Raw provider tool-call IDs and user-selected task IDs are arbitrary text.
    # A deterministic domain-separated digest preserves correlation, not anonymity
    # or approval authority. Never export raw IDs through a permissive regex.
    if kind not in {"tool_call", "task_id"}:
        raise ValueError("report_evidence_unavailable")
    value = identifier(value)
    return hashlib.sha256(
        b"doppel-report-v1\0" + kind.encode("ascii") + b"\0" + value.encode("utf-8")
    ).hexdigest()


def unknown(reason="unavailable"):
    return {
        "state": "unknown",
        "total": None,
        "scanned": 0,
        "emitted": 0,
        "omitted": 0,
        "limit": MAX_ROWS,
        "truncated": None,
        "items": [],
        "reason": reason,
    }


class _Budget:
    def __init__(self, deadline):
        self.deadline, self.consumed = deadline, 0

    def claim(self, size):
        _check(self.deadline)
        if not _integer(size) or self.consumed + size > MAX_DECODE:
            raise ValueError("report_evidence_budget")
        self.consumed += size  # before payload SELECT/decode; failed frames aren't refunded


class ReportEvidenceQueries:
    def __init__(self, runtime_database, ledger_database, *, failure=None, cleanup_failure=None):
        self.runtime_database, self.ledger_database = Path(runtime_database), Path(ledger_database)
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._unresolved_reads = {}

    def _retain_read(self, source):
        self._unresolved_reads[id(source)] = source
        if self._cleanup_failure is not None:
            self._cleanup_failure(source)

    def _check_read(self):
        if self._unresolved_reads:
            raise ReportReadCleanupError(next(iter(self._unresolved_reads.values())))

    @contextmanager
    def _connection(self, path, budget):
        self._check_read()
        lifetime = None
        try:
            admitted = _safe_database(path, budget.deadline)
            if Path(path) == self.ledger_database:
                if (
                    admitted.parent != self.runtime_database.absolute().parent
                    or admitted == self.runtime_database.absolute()
                ):
                    raise ValueError("report_evidence_unavailable")
            lifetime = ReportReadSource(failure=self._failure, cleanup_failure=self._retain_read)
            db = lifetime.open_original(sqlite3.connect, admitted.as_uri() + "?mode=ro")
            db.row_factory = sqlite3.Row
            db.set_progress_handler(lambda: int(monotonic() >= budget.deadline), 1000)
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("BEGIN")
            yield db
        finally:
            if lifetime is not None:
                lifetime.close_original()

    @staticmethod
    def _tables(db, names):
        placeholders = ",".join("?" for _ in names)
        return db.execute(
            f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ({placeholders})", names
        ).fetchone()[0] == len(names)

    @classmethod
    def _page(cls, db, budget, tables, scope_sql, order_sql, params, projector, *, selection_params=None):
        try:
            _check(budget.deadline)
            if not cls._tables(db, tables):
                return unknown("missing_tables")
            total = db.execute("SELECT COUNT(*) " + scope_sql, params).fetchone()[0]
            if not _integer(total):
                raise ValueError("report_evidence_unavailable")
            # Internal fixed SQL only; first16 bounded scalar identifiers, not
            # private payload fetchall. Individual body reads require budget claim.
            candidates = db.execute(
                order_sql + " LIMIT ?",
                (*(params if selection_params is None else selection_params), MAX_ROWS),
            ).fetchall()
            items, omitted = [], 0
            for row in candidates:
                try:
                    _check(budget.deadline)
                    items.append(projector(row))
                except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                    omitted += 1
            truncated = total > len(candidates)
            dependent_unknown = any(
                item.get("source_receipt_state") == "unavailable"
                or item.get("effect_receipt_state") == "unavailable"
                or "dispatch_state" in item
                and item["dispatch_state"] is None
                for item in items
            )
            partial = omitted > 0 or truncated or dependent_unknown
            return {
                "state": "partial" if partial else "known",
                "total": total,
                "scanned": len(candidates),
                "emitted": len(items),
                "omitted": omitted,
                "limit": MAX_ROWS,
                "truncated": truncated,
                "items": items,
                "reason": "incomplete_evidence" if partial else None,
            }
        except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return unknown()

    @staticmethod
    def _patch(db, run, call, budget, cache):
        call = identifier(call)
        if call in cache:
            return cache[call]
        budget.claim(PatchEvidenceQueries._evidence(db, run, call))
        view, expired_at = PatchEvidenceQueries._public(db, run, call)
        if expired_at is not None:
            _date(expired_at)
        receipt = view["receipt"]
        item = {
            "tool_call_sha256": report_digest("tool_call", call),
            "operation": view["tool_name"],
            "status": view["operation_status"],
            "patch_id": receipt["patch_id"] if receipt else None,
            "receipt_status": receipt["status"] if receipt else None,
            "confirmed_applied": view["confirmed_applied"],
            "files": len(receipt["files"]) if receipt else None,
            "preimage_state": view["preimage_state"] if receipt else "unknown",
        }
        _check(budget.deadline)
        cache[call] = item  # only numeric allowlist, no retained private bodies
        return item

    @classmethod
    def _receipt_state(cls, db, run, call, patch, budget, cache):
        try:
            item = cls._patch(db, run, call, budget, cache)
            return "confirmed" if item["confirmed_applied"] and item["patch_id"] == patch else "not_confirmed"
        except KeyError:
            return "not_confirmed"  # no receipt in this snapshot, not proof of no effect
        except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return "unavailable"

    @classmethod
    def _inverse(cls, db, run, review, budget, cache):
        identifier(review, uuid=True)
        sizes = db.execute(
            """SELECT length(CAST(proposal_json AS BLOB)),length(CAST(review_json AS BLOB)),
            length(CAST(result_json AS BLOB)),length(CAST(source_tool_call_id AS BLOB)),length(CAST(created_at AS BLOB)),
            length(CAST(expires_at AS BLOB)),length(CAST(status AS BLOB)),length(CAST(decision AS BLOB)),
            length(CAST(error_code AS BLOB)),length(CAST(source_patch_id AS BLOB)),length(CAST(patch_id AS BLOB))
            FROM inverse_patch_reviews WHERE source_run_id=? AND review_id=?""",
            (run, review),
        ).fetchone()
        limits = (MAX_BLOB, MAX_BLOB, MAX_BLOB, 1024, 64, 64, 32, 16, 64, 32, 32)
        if sizes is None or any(
            type(n) is not int or not 0 <= n <= maximum for n, maximum in zip(sizes, limits, strict=True)
        ):
            raise ValueError("report_evidence_unavailable")
        budget.claim(sum(sizes[:3]))
        row = InverseReviewStore._row(db, run, review)
        expected = (
            ""
            if row["status"] in {"pending", "expired"}
            else "reject"
            if row["status"] == "rejected"
            else "approve"
        )
        if row["decision"] != expected:
            raise ValueError("report_evidence_unavailable")
        if row["status"] == "pending":
            InverseReviewStore._pending_proposal(row)  # pure original exact frame, no workspace reads
        view = InverseReviewStore._public(row)
        source = cls._receipt_state(
            db, run, row["source_tool_call_id"], row["source_patch_id"], budget, cache
        )
        effect = cls._receipt_state(
            db, run, InverseReviewStore.effect_id(review), row["patch_id"], budget, cache
        )
        _check(budget.deadline)
        return {
            "review_id": review,
            "source_tool_call_sha256": report_digest("tool_call", row["source_tool_call_id"]),
            "source_patch_id": row["source_patch_id"],
            "patch_id": row["patch_id"],
            "effect_tool_call_sha256": report_digest("tool_call", InverseReviewStore.effect_id(review)),
            "status": row["status"],
            "files": len(view["review"]["files"]),
            "source_receipt_confirmed": source == "confirmed",
            "source_receipt_state": source,
            "effect_receipt_state": effect,
            "outcome_unknown": row["status"] in {"applying", "failed", "indeterminate"},
            "pending_expired": row["status"] == "pending" and _date(row["expires_at"]) <= datetime.now(UTC),
        }

    @classmethod
    def _verification(cls, db, run, review, budget, cache):
        identifier(review, uuid=True)
        budget.claim(VerificationQueries._size(db, run, review))
        row = VerificationReviewStore._row(db, run, review)
        view = VerificationReviewStore._view(db, row)
        source = cls._receipt_state(
            db, run, view["source"]["tool_call_id"], view["source"]["patch_id"], budget, cache
        )
        steps = []
        for step in view["steps"]:
            result = step["result"]
            if (
                result is not None
                and result["exit_code"] is not None
                and not -MAX_SAFE <= result["exit_code"] <= MAX_SAFE
            ):
                raise ValueError("report_evidence_unavailable")
            steps.append(
                {
                    "index": step["index"],
                    "status": step["status"],
                    "exit_code": result["exit_code"] if result else None,
                    "success": result["success"] if result else None,
                    "failure": result["error"] if result else None,
                    "duration_ms": result["duration_ms"] if result else None,
                }
            )
        planned = len(view["plan"]["commands"])
        sealed = sum(step["status"] == "finished" for step in steps)
        exited = sum(step["exit_code"] is not None for step in steps)
        _check(budget.deadline)
        return {
            "review_id": review,
            "source_tool_call_sha256": report_digest("tool_call", view["source"]["tool_call_id"]),
            "source_patch_id": view["source"]["patch_id"],
            "plan_id": view["plan"]["plan_id"],
            "status": view["status"],
            "source_receipt_confirmed": source == "confirmed",
            "source_receipt_state": source,
            "planned_commands": planned,
            "sealed_steps": sealed,
            "exited_commands": exited,
            "failed_attempts": sum(step["success"] is False for step in steps),
            "remaining_commands": planned - sealed,
            "has_unknown_command": view["has_unknown_command"],
            "attempts_success": view["success"],
            "all_commands_exited": exited == planned,
            "steps": steps,
            "pending_expired": view["status"] == "pending" and _date(row["expires_at"]) <= datetime.now(UTC),
            "target": "current_workspace_not_original_patch_snapshot",
            "project_acceptance": False,
        }

    @staticmethod
    def _work_order(row):
        for key in ("attempt_id", "work_order_id"):
            identifier(row[key], uuid=True)
        task = row["task_id"]
        if (
            not isinstance(task, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", task) is None
            or any(
                not _integer(row[key]) or row[key] < 1
                for key in ("revision", "active_revision", "attempt_number")
            )
            or row["attempt_number"] > MAX_ATTEMPTS_PER_TASK
            or row["revision"] > row["active_revision"]
            or row["status"]
            not in {
                "reserved",
                "accepted",
                "running",
                "awaiting_approval",
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
            }
            or row["order_status"]
            not in {"draft", "queued", "running", "paused", "succeeded", "failed", "cancelled"}
            or row["dispatch_state"] not in {None, "pending", "admitted", "abandoned"}
            or row["attempt_run"] is not None
            and row["attempt_run"] != row["scope_run"]
            or row["intent_run"] is not None
            and row["intent_run"] != row["scope_run"]
            or row["dispatch_state"] == "admitted"
            and row["intent_run"] is None
            or row["intent_present"]
            and row["dispatch_state"] is None
            or row["invalid_run_link"]
            or not row["task_present"]
        ):
            raise ValueError("report_evidence_unavailable")
        return {
            "attempt_id": row["attempt_id"],
            "work_order_id": row["work_order_id"],
            "task_id_sha256": report_digest("task_id", task),
            "execution_revision": row["revision"],
            "active_plan_revision": row["active_revision"],
            "attempt_number": row["attempt_number"],
            "attempt_status": row["status"],
            "order_status": row["order_status"],
            "dispatch_state": row["dispatch_state"],
        }

    def read(self, run):
        self._check_read()
        identifier(run, uuid=True)
        budget = _Budget(monotonic() + QUERY_SECONDS)
        sections = {name: unknown() for name in SECTIONS}
        try:
            with self._connection(self.runtime_database, budget) as db:
                if db.execute("SELECT 1 FROM runtime_runs WHERE run_id=?", (run,)).fetchone() is None:
                    raise ValueError("report_evidence_unavailable")
                scope = """FROM work_order_attempts a LEFT JOIN work_order_dispatch_intents i USING(attempt_id)
                    WHERE a.run_id=? OR i.run_id=?"""
                candidates = """SELECT
                    CASE WHEN length(CAST(a.attempt_id AS BLOB))=32 THEN a.attempt_id END AS attempt_id,
                    CASE WHEN length(CAST(a.work_order_id AS BLOB))=32 THEN a.work_order_id END AS work_order_id,
                    CASE WHEN length(CAST(a.task_id AS BLOB))<=256 THEN a.task_id END AS task_id,
                    CASE WHEN typeof(a.revision)='integer' THEN a.revision END AS revision,
                    CASE WHEN typeof(a.attempt_number)='integer' THEN a.attempt_number END AS attempt_number,
                    CASE WHEN length(CAST(a.status AS BLOB))<=32 THEN a.status END AS status,
                    CASE WHEN length(CAST(o.status AS BLOB))<=32 THEN o.status END AS order_status,
                    CASE WHEN typeof(o.active_revision)='integer' THEN o.active_revision END AS active_revision,
                    CASE WHEN length(CAST(i.state AS BLOB))<=16 THEN i.state END AS dispatch_state,
                    i.attempt_id IS NOT NULL AS intent_present,
                    CASE WHEN typeof(a.run_id)='text' AND length(CAST(a.run_id AS BLOB))=32 THEN a.run_id END AS attempt_run,
                    CASE WHEN typeof(i.run_id)='text' AND length(CAST(i.run_id AS BLOB))=32 THEN i.run_id END AS intent_run,
                    (a.run_id IS NOT NULL AND (typeof(a.run_id)!='text' OR length(CAST(a.run_id AS BLOB))!=32))
                        OR (i.run_id IS NOT NULL AND (typeof(i.run_id)!='text' OR length(CAST(i.run_id AS BLOB))!=32)) AS invalid_run_link,
                    ? AS scope_run,
                    EXISTS(SELECT 1 FROM work_order_tasks t WHERE t.work_order_id=a.work_order_id AND t.revision=a.revision AND t.task_id=a.task_id) AS task_present
                    FROM work_order_attempts a LEFT JOIN work_order_dispatch_intents i USING(attempt_id)
                    LEFT JOIN work_orders o ON o.work_order_id=a.work_order_id
                    WHERE a.run_id=? OR i.run_id=? ORDER BY a.attempt_id"""
                # The SELECT has a third source parameter, unlike its COUNT;
                # keep the scope literal rather than interpolating user input.
                sections["work_orders"] = self._work_orders(db, budget, run, scope, candidates)
        except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return self._result(sections, budget)
        try:
            with self._connection(self.ledger_database, budget) as db:
                cache = {}
                # Include receipt-only/orphan/misnamed rows rather than hide them
                # as an observed empty patch scope. Original frame then omits
                # unprovable records explicitly; UNION deduplicates normal pairs.
                scope = """FROM (SELECT tool_call_id FROM tool_executions WHERE run_id=?
                    AND tool_name IN ('propose_patch','inverse_patch')
                    UNION SELECT tool_call_id FROM patch_effect_receipts WHERE run_id=?)"""
                sections["patches"] = self._page(
                    db,
                    budget,
                    ("tool_executions", "patch_effect_receipts"),
                    scope,
                    "SELECT CASE WHEN length(CAST(tool_call_id AS BLOB))<=1024 THEN tool_call_id END AS id "
                    + scope
                    + " ORDER BY tool_call_id",
                    (run, run),
                    lambda row: self._patch(db, run, row["id"], budget, cache),
                )
                for name, table, project in (
                    ("inverses", "inverse_patch_reviews", self._inverse),
                    ("verifications", "verification_reviews", self._verification),
                ):
                    required = (table,) if name == "inverses" else (table, "verification_command_steps")
                    scope = f"FROM {table} WHERE source_run_id=?"
                    sections[name] = self._page(
                        db,
                        budget,
                        required,
                        scope,
                        "SELECT CASE WHEN length(CAST(review_id AS BLOB))=32 THEN review_id END AS id "
                        + scope
                        + " ORDER BY review_id",
                        (run,),
                        lambda row, project=project: project(db, run, row["id"], budget, cache),
                    )
        except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError, RecursionError):
            pass  # fixed unknown, never create a missing ledger or echo private failures
        return self._result(sections, budget)

    @classmethod
    def _work_orders(cls, db, budget, run, scope, candidates):
        # COUNT has two scope parameters; SELECT has an additional bound source.
        tables = ("work_order_attempts", "work_order_dispatch_intents", "work_orders", "work_order_tasks")
        return cls._page(
            db,
            budget,
            tables,
            scope,
            candidates,
            (run, run),
            cls._work_order,
            selection_params=(run, run, run),
        )

    @staticmethod
    def _result(sections, budget):
        return {
            **sections,
            "snapshot_consistency": "independent_read_transactions_not_atomic_with_event_snapshot",
            "tool_call_identity": "domain_separated_sha256_not_raw_id_or_approval_authority",
            "reserved_body_bytes": budget.consumed,
            "decode_limit_bytes": MAX_DECODE,
            "sql_read_only": True,
            "filesystem_zero_write_guarantee": False,
            "current_git_ownership_proven": False,
            "project_acceptance_proven": False,
            "physical_drain_verified": False,
            "approval_granted": False,
            "automatic_retry_allowed": False,
        }
