"""Manual verification review and command evidence, separate from patch effects.

An intent is never proof of a command completing or not executing. This store
does not launch/recover/retry commands; only owned service admission may do IO.
Scoped outputs may contain sensitive project data; global redaction is S8.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..context.manifest import relative_path
from ..workspace.verification_config import _hash
from .database import sqlite_connection
from .evidence_json import loads as evidence_loads
from .inverse_reviews import identifier


MAX_PLAN_BYTES = 128 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024  # Aggregate encoded results per operation.
MAX_PENDING = 256
STATES = {"pending", "running", "completed", "failed", "cancelled", "rejected", "expired", "indeterminate"}
CODES = {"", "verification_review_stale", "verification_cancelled", "verification_deadline_exceeded",
         "verification_outcome_indeterminate", "verification_evidence_budget"}


def _dump(value, limit):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > limit:
        raise ValueError("verification_evidence_budget")
    return raw


def _load(raw, limit):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise ValueError("verification_evidence_unavailable")
    try:
        return evidence_loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("verification_evidence_unavailable") from None


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _names(names):
    if names is None:
        return None
    if (not isinstance(names, (tuple, list)) or not 1 <= len(names) <= 16
            or any(not isinstance(name, str) or not 1 <= len(name.encode("utf-8")) <= 128 or "\x00" in name for name in names)
            or len(names) != len(set(names))):
        raise ValueError("invalid_verification_selection")
    return list(names)


def plan_view(value):
    if (not isinstance(value, dict) or set(value) != {"schema", "plan_id", "source", "commands",
            "max_output_bytes", "stop_on_failure", "operation_timeout_seconds"}
            or type(value["schema"]) is not int or value["schema"] != 1 or not _sha(value["plan_id"])):
        raise ValueError("verification_review_unavailable")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {"path", "config_hash", "snapshot_hash", "workspace_id"}:
        raise ValueError("verification_review_unavailable")
    if relative_path(source["path"]) != source["path"] or not all(_sha(source[key]) for key in ("config_hash", "snapshot_hash", "workspace_id")):
        raise ValueError("verification_review_unavailable")
    commands = value["commands"]
    if not isinstance(commands, list) or not 1 <= len(commands) <= 16:
        raise ValueError("verification_review_unavailable")
    seen = set()
    for command in commands:
        if not isinstance(command, dict) or set(command) != {"name", "argv", "timeout_seconds"}:
            raise ValueError("verification_review_unavailable")
        name, argv, timeout = command["name"], command["argv"], command["timeout_seconds"]
        if (not isinstance(name, str) or not 1 <= len(name.encode("utf-8")) <= 128 or "\x00" in name or name in seen
                or not isinstance(argv, list) or not 1 <= len(argv) <= 64
                or any(not isinstance(arg, str) or not 1 <= len(arg.encode("utf-8")) <= 4096 or "\x00" in arg for arg in argv)
                or type(timeout) not in {int, float} or not 0 < timeout <= 600 or not math.isfinite(timeout)):
            raise ValueError("verification_review_unavailable")
        seen.add(name)
    if (type(value["max_output_bytes"]) is not int or not 1 <= value["max_output_bytes"] <= 1048576
            or type(value["stop_on_failure"]) is not bool or type(value["operation_timeout_seconds"]) is not float
            or value["operation_timeout_seconds"] != 600.0
            or _hash({key: item for key, item in value.items() if key != "plan_id"}) != value["plan_id"]):
        raise ValueError("verification_review_unavailable")
    return _load(_dump(value, MAX_PLAN_BYTES), MAX_PLAN_BYTES)


def _result(value, command):
    if (not isinstance(value, dict) or set(value) != {"name", "argv", "exit_code", "success", "stdout", "stderr",
                                                    "duration_ms", "supervision", "error"}
            or value["name"] != command["name"] or value["argv"] != command["argv"]
            or value["exit_code"] is not None and type(value["exit_code"]) is not int
            or type(value["success"]) is not bool or value["success"] != (value["exit_code"] == 0)
            or not isinstance(value["stdout"], str) or not isinstance(value["stderr"], str)
            or type(value["duration_ms"]) is not int or not 0 <= value["duration_ms"] <= 86400000
            or not isinstance(value["supervision"], str) or not 1 <= len(value["supervision"]) <= 64
            or value["error"] is not None and not isinstance(value["error"], str)
            or value["error"] not in {None, "verification_timeout", "verification_output_limit", "verification_supervision_unavailable"}
            or (value["exit_code"] is None) != (value["error"] is not None)
            or value["error"] is not None and (value["stdout"] or value["stderr"]
                or value["supervision"] != ("unavailable" if value["error"] == "verification_supervision_unavailable" else "terminated"))):
        raise ValueError("verification_result_unavailable")
    return value


class VerificationReviewStore:
    def __init__(self, database: Path, *, ttl_seconds=900):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("invalid_verification_review_ttl")
        self.database, self.ttl_seconds = database, ttl_seconds
        with sqlite_connection(database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS verification_reviews (
                review_id TEXT PRIMARY KEY, source_run_id TEXT NOT NULL,
                source_tool_call_id TEXT NOT NULL, source_patch_id TEXT NOT NULL,
                plan_id TEXT NOT NULL, plan_json TEXT NOT NULL, selection_json TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled','rejected','expired','indeterminate')),
                decision TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT ''
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS ix_verification_source ON verification_reviews(source_run_id,review_id)")
            db.execute("CREATE INDEX IF NOT EXISTS ix_verification_lifecycle ON verification_reviews(status,review_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS verification_command_steps (
                review_id TEXT NOT NULL, step_index INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running','finished')),
                result_json TEXT NOT NULL DEFAULT '', PRIMARY KEY(review_id,step_index),
                FOREIGN KEY(review_id) REFERENCES verification_reviews(review_id) ON DELETE CASCADE
            )""")

    @staticmethod
    def _row(db, run_id, review_id):
        identifier(run_id, uuid=True)
        identifier(review_id, uuid=True)
        row = db.execute("""SELECT
            CASE WHEN typeof(review_id)='text' AND length(CAST(review_id AS BLOB))=32 THEN review_id END AS review_id,
            CASE WHEN typeof(source_run_id)='text' AND length(CAST(source_run_id AS BLOB))=32 THEN source_run_id END AS source_run_id,
            CASE WHEN typeof(source_tool_call_id)='text' AND length(CAST(source_tool_call_id AS BLOB))<=1024 THEN source_tool_call_id END AS source_tool_call_id,
            CASE WHEN typeof(source_patch_id)='text' AND length(CAST(source_patch_id AS BLOB))=32 THEN source_patch_id END AS source_patch_id,
            CASE WHEN typeof(plan_id)='text' AND length(CAST(plan_id AS BLOB))=64 THEN plan_id END AS plan_id,
            CASE WHEN typeof(created_at)='text' AND length(CAST(created_at AS BLOB))<=64 THEN created_at END AS created_at,
            CASE WHEN typeof(expires_at)='text' AND length(CAST(expires_at AS BLOB))<=64 THEN expires_at END AS expires_at,
            CASE WHEN typeof(status)='text' AND length(CAST(status AS BLOB))<=32 THEN status END AS status,
            CASE WHEN typeof(decision)='text' AND length(CAST(decision AS BLOB))<=16 THEN decision END AS decision,
            CASE WHEN typeof(error_code)='text' AND length(CAST(error_code AS BLOB))<=64 THEN error_code END AS error_code,
            CASE WHEN length(CAST(plan_json AS BLOB))<=131072 THEN plan_json END AS plan_json,
            CASE WHEN length(CAST(selection_json AS BLOB))<=8192 THEN selection_json END AS selection_json
            FROM verification_reviews WHERE review_id=? AND source_run_id=?""", (review_id, run_id)).fetchone()
        if row is None:
            raise ValueError("verification_review_scope_unavailable")
        identifier(row["review_id"], uuid=True)
        identifier(row["source_run_id"], uuid=True)
        identifier(row["source_tool_call_id"])
        identifier(row["source_patch_id"], uuid=True)
        if (not _sha(row["plan_id"]) or row["status"] not in STATES or row["decision"] not in {"", "approve", "reject", "cancel"}
                or row["error_code"] not in CODES):
            raise ValueError("verification_review_unavailable")
        dates = []
        for key in ("created_at", "expires_at"):
            try:
                date = datetime.fromisoformat(row[key])
            except (TypeError, ValueError):
                raise ValueError("verification_review_unavailable") from None
            if date.utcoffset() != timedelta(0) or date.isoformat() != row[key]:
                raise ValueError("verification_review_unavailable")
            dates.append(date)
        if not 0 < (dates[1] - dates[0]).total_seconds() <= 86400:
            raise ValueError("verification_review_unavailable")
        return row

    @classmethod
    def _view(cls, db, row, *, replayed=False):
        plan = plan_view(_load(row["plan_json"], MAX_PLAN_BYTES))
        if plan["plan_id"] != row["plan_id"]:
            raise ValueError("verification_review_unavailable")
        selection = _names(_load(row["selection_json"], 8192))
        if selection is not None and selection != [command["name"] for command in plan["commands"]]:
            raise ValueError("verification_review_unavailable")
        size = db.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(result_json AS BLOB))),0) FROM verification_command_steps WHERE review_id=?", (row["review_id"],)).fetchone()
        if size[0] > 16 or size[1] > MAX_RESULT_BYTES:
            raise ValueError("verification_evidence_budget")
        records = db.execute("""SELECT
            CASE WHEN typeof(step_index)='integer' AND step_index BETWEEN 0 AND 15 THEN step_index END AS step_index,
            CASE WHEN typeof(status)='text' AND length(CAST(status AS BLOB))<=16 THEN status END AS status,
            CASE WHEN length(CAST(result_json AS BLOB))<=4194304 THEN result_json END AS result_json
            FROM verification_command_steps WHERE review_id=? ORDER BY step_index LIMIT 17""", (row["review_id"],)).fetchall()
        steps, consumed = [], 0
        for index, step in enumerate(records):
            if (index >= len(plan["commands"]) or type(step["step_index"]) is not int
                    or step["step_index"] != index or step["status"] not in {"running", "finished"}):
                raise ValueError("verification_evidence_unavailable")
            raw = step["result_json"]
            if not isinstance(raw, str):
                raise ValueError("verification_evidence_unavailable")
            consumed += len(raw.encode("utf-8"))
            if consumed > MAX_RESULT_BYTES:
                raise ValueError("verification_evidence_budget")
            actual = _result(_load(raw, MAX_RESULT_BYTES), plan["commands"][index]) if raw else None
            if (step["status"] == "finished") != (actual is not None) or index < len(records) - 1 and step["status"] != "finished":
                raise ValueError("verification_evidence_unavailable")
            if index and plan["stop_on_failure"] and steps[-1]["result"] and not steps[-1]["result"]["success"]:
                raise ValueError("verification_evidence_unavailable")
            steps.append({"index": index, "status": step["status"], "result": actual})
        unknown = any(step["status"] == "running" for step in steps)
        if (row["status"] in {"pending", "rejected", "expired"} and steps
                or row["status"] in {"pending", "expired"} and row["decision"] != ""
                or row["status"] == "rejected" and row["decision"] != "reject"
                or row["status"] in {"running", "completed", "failed", "indeterminate"} and row["decision"] != "approve"
                or row["status"] == "cancelled" and (row["decision"] not in {"approve", "cancel"}
                                                       or row["decision"] == "cancel" and steps)):
            raise ValueError("verification_evidence_unavailable")
        success = None
        if row["status"] == "completed":
            if (unknown or not steps or len(steps) != len(plan["commands"])
                    and not (plan["stop_on_failure"] and not steps[-1]["result"]["success"])):
                raise ValueError("verification_completion_requires_sealed_results")
            success = all(step["result"]["success"] for step in steps)
        return {"review_id": row["review_id"], "operation_id": "manual-verification:" + row["review_id"],
                "operation_kind": "manual_verification", "source": {"run_id": row["source_run_id"],
                    "tool_call_id": row["source_tool_call_id"], "patch_id": row["source_patch_id"]},
                "target": "current_workspace_not_original_patch_snapshot", "plan": plan,
                "created_at": row["created_at"], "expires_at": row["expires_at"], "status": row["status"],
                "error_code": row["error_code"] or None, "success": success, "steps": steps,
                "has_unknown_command": unknown, "decision_replayed": replayed,
                "evidence": "sealed_verification_steps" if row["status"] == "completed" else "not_completion_proof"}

    @classmethod
    def _expire(cls, db, row):
        cls._view(db, row)
        if row["status"] == "pending" and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            db.execute("UPDATE verification_reviews SET status='expired' WHERE review_id=? AND status='pending'", (row["review_id"],))

    def get(self, run_id, review_id):
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, self._row(db, run_id, review_id))
            return self._view(db, self._row(db, run_id, review_id))

    def prepare_replay(self, run_id, tool_call_id, patch_id, review_id, names):
        identifier(run_id, uuid=True)
        identifier(review_id, uuid=True)
        request = _dump(_names(names), 8192)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute("SELECT source_run_id FROM verification_reviews WHERE review_id=?", (review_id,)).fetchone()
            if exists is None:
                return None
            row = self._row(db, run_id, review_id)
            if (row["source_tool_call_id"] != tool_call_id or row["source_patch_id"] != patch_id or row["selection_json"] != request):
                raise ValueError("verification_review_scope_unavailable")
            self._expire(db, row)
            return self._view(db, self._row(db, run_id, review_id))

    def save(self, run_id, tool_call_id, patch_id, review_id, names, plan):
        for value in (run_id, patch_id, review_id):
            identifier(value, uuid=True)
        identifier(tool_call_id)
        plan = plan_view(plan)
        request = _dump(_names(names), 8192)
        now = datetime.now(UTC)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("""SELECT COUNT(*) FROM verification_reviews WHERE status IN ('running','indeterminate')
                OR status='pending' AND expires_at>?""", (now.isoformat(),)).fetchone()[0] >= MAX_PENDING:
                raise ValueError("verification_review_pending_budget")
            db.execute("""INSERT INTO verification_reviews(review_id,source_run_id,source_tool_call_id,source_patch_id,
                plan_id,plan_json,selection_json,created_at,expires_at,status) VALUES(?,?,?,?,?,?,?,?,?,'pending')""",
                       (review_id, run_id, tool_call_id, patch_id, plan["plan_id"], _dump(plan, MAX_PLAN_BYTES), request,
                        now.isoformat(), (now + timedelta(seconds=self.ttl_seconds)).isoformat()))
            return self._view(db, self._row(db, run_id, review_id))

    def claim(self, run_id, review_id, plan_id, action):
        if action not in {"approve", "reject"}:
            raise ValueError("verification_decision_must_be_approve_or_reject")
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id, review_id)
            if row["plan_id"] != plan_id:
                raise ValueError("verification_review_scope_unavailable")
            self._expire(db, row)
            row = self._row(db, run_id, review_id)
            if (row["status"] == "completed" and action == "approve" or row["status"] == "rejected" and action == "reject"):
                return self._view(db, row, replayed=True), None
            if row["status"] == "pending":
                db.execute("UPDATE verification_reviews SET status=?,decision=? WHERE review_id=? AND status='pending'",
                           ("running" if action == "approve" else "rejected", action, review_id))
                view = self._view(db, self._row(db, run_id, review_id))
                return view, view["plan"] if action == "approve" else None
        # Commit expiration before refusing, never revive unknown/failed/cancelled.
        raise RuntimeError("verification_review_not_pending_or_outcome_indeterminate")

    def start_step(self, run_id, review_id, index, command):
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            view = self._view(db, self._row(db, run_id, review_id))
            if view["status"] != "running" or view["has_unknown_command"]:
                raise RuntimeError("verification_command_outcome_indeterminate")
            if (type(index) is not int or index != len(view["steps"]) or not 0 <= index < len(view["plan"]["commands"])
                    or command != view["plan"]["commands"][index]
                    or view["steps"] and view["plan"]["stop_on_failure"] and not view["steps"][-1]["result"]["success"]):
                raise ValueError("verification_step_scope_unavailable")
            db.execute("INSERT INTO verification_command_steps(review_id,step_index,status) VALUES(?,?,'running')", (review_id, index))

    def seal_step(self, run_id, review_id, index, actual):
        # Normalize tuples to exact JSON arrays before framing checks.
        actual = _load(_dump(actual, MAX_RESULT_BYTES), MAX_RESULT_BYTES)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            view = self._view(db, self._row(db, run_id, review_id))
            if (view["status"] != "running" or type(index) is not int or index != len(view["steps"]) - 1
                    or not view["has_unknown_command"]):
                raise ValueError("verification_step_scope_unavailable")
            raw = _dump(_result(actual, view["plan"]["commands"][index]), MAX_RESULT_BYTES)
            used = db.execute("SELECT COALESCE(SUM(length(CAST(result_json AS BLOB))),0) FROM verification_command_steps WHERE review_id=?", (review_id,)).fetchone()[0]
            if used + len(raw.encode("utf-8")) > MAX_RESULT_BYTES:
                raise ValueError("verification_evidence_budget")
            db.execute("UPDATE verification_command_steps SET status='finished',result_json=? WHERE review_id=? AND step_index=? AND status='running'", (raw, review_id, index))
            self._view(db, self._row(db, run_id, review_id))

    def finish(self, run_id, review_id, status, *, error_code=""):
        if status not in {"completed", "failed", "cancelled", "indeterminate"} or error_code not in CODES:
            raise ValueError("verification_invalid_outcome")
        with sqlite_connection(self.database) as db:
            cursor = db.execute("UPDATE verification_reviews SET status=?,error_code=? WHERE review_id=? AND source_run_id=? AND status='running'", (status, error_code, review_id, run_id))
            if cursor.rowcount != 1:
                raise ValueError("verification_review_scope_unavailable")
            return self._view(db, self._row(db, run_id, review_id))

    def cancel_pending(self, run_id, review_id, plan_id):
        """Cancel an unclaimed review only; never claim a live process stopped."""
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id, review_id)
            if row["plan_id"] != plan_id:
                raise ValueError("verification_review_scope_unavailable")
            self._expire(db, row)
            row = self._row(db, run_id, review_id)
            if row["status"] == "pending":
                db.execute("""UPDATE verification_reviews SET status='cancelled',decision='cancel',
                    error_code='verification_cancelled' WHERE review_id=? AND source_run_id=? AND status='pending'""",
                           (review_id, run_id))
            return self._view(db, self._row(db, run_id, review_id))

    @classmethod
    def _reconcile_row(cls, db, row):
        """Owner excludes live execution; stored evidence only, no project/process IO."""
        view = cls._view(db, row)
        if row["status"] not in {"running", "indeterminate"}:
            return view
        steps, plan = view["steps"], view["plan"]
        complete = (not view["has_unknown_command"] and bool(steps)
                    and (len(steps) == len(plan["commands"])
                         or plan["stop_on_failure"] and not steps[-1]["result"]["success"]))
        state = "completed" if complete else "indeterminate"
        code = "" if complete else (row["error_code"] or "verification_outcome_indeterminate")
        db.execute("""UPDATE verification_reviews SET status=?,error_code=?
            WHERE review_id=? AND source_run_id=? AND status IN ('running','indeterminate')""",
                   (state, code, row["review_id"], row["source_run_id"]))
        return cls._view(db, cls._row(db, row["source_run_id"], row["review_id"]))

    def reconcile(self, run_id, review_id):
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, run_id, review_id)
            self._expire(db, row)
            return self._reconcile_row(db, self._row(db, run_id, review_id))
