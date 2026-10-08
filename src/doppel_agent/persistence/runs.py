"""SQLite run records and guarded status transitions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .database import sqlite_connection
from .migrations import apply_migrations
from .conversations import bind_turn, insert_conversation, project_run


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted_expired"}


class RuntimeRunStore:
    def __init__(self, database: Path):
        self.database = database
        apply_migrations(database)

    def create(
        self,
        run_id: str,
        thread_id: str,
        mode: str,
        request: dict[str, Any],
        idempotency_key: str | None,
        *,
        native: bool = False,
        profile_snapshot: dict[str, Any] | None = None,
        expected_profile_id: Any = ...,
        write_scope: bool = False,
        input_resolver: Callable[[sqlite3.Connection, dict], dict] | None = None,
        frozen_input_snapshot: dict | None = None,
    ) -> tuple[dict[str, Any], bool]:
        from ..context.input import normalize_request

        if type(write_scope) is not bool:
            raise ValueError("write scope must be an explicit internal boolean")
        request = normalize_request(request)
        now = datetime.now(UTC).isoformat()
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute(
                    "SELECT * FROM runtime_runs WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    stored = json.loads(existing["request_json"])
                    if stored != request:
                        raise ValueError("idempotency key was reused with a different request")
                    return self._decode(existing), False
            input_snapshot = None
            if frozen_input_snapshot is not None:
                from ..context.input import validate_input_snapshot

                input_snapshot = validate_input_snapshot(frozen_input_snapshot)
            elif request.get("context") is not None:
                if input_resolver is None:
                    raise ValueError("context resolver required")
                input_snapshot = input_resolver(connection, request["context"])
            conversation_id = None
            if native:
                conversation_id = request.get("conversation_id")
                if conversation_id is None:
                    conversation_id = insert_conversation(
                        connection, mode, " ".join(request["prompt"].split())[:42] or "新对话",
                        request.get("profile_id"),
                    )
                thread_id = bind_turn(connection, conversation_id, mode)
                conversation = connection.execute("SELECT * FROM native_conversations WHERE id=?", (conversation_id,)).fetchone()
                if expected_profile_id is not ... and conversation["profile_id"] != expected_profile_id:
                    raise ValueError("conversation profile changed during acceptance; retry")
                if conversation["title"] == "新对话":
                    connection.execute("UPDATE native_conversations SET title=? WHERE id=?",
                                       (" ".join(request["prompt"].split())[:42] or "新对话", conversation_id))
            connection.execute(
                "INSERT INTO runtime_runs(run_id,thread_id,status,mode,idempotency_key,"
                "request_json,created_at,updated_at,conversation_id,profile_json,lease_active,write_scope,input_snapshot_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, thread_id, "queued", mode, idempotency_key, encoded, now, now,
                 conversation_id, json.dumps(profile_snapshot or {}, ensure_ascii=False), int(native), int(write_scope),
                 json.dumps(input_snapshot, ensure_ascii=False, separators=(",", ":")) if input_snapshot is not None else None),
            )
            project_run(connection, run_id)
            if native:
                self._projection_event(connection, run_id, thread_id, conversation_id, "queued", now)
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row), True

    def get(self, run_id: str) -> dict[str, Any] | None:
        with sqlite_connection(self.database) as connection:
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row) if row else None

    def active_queue(self, limit: int = 100) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid runtime queue limit")
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN")
            where = "r.status IN ('queued','running','interrupted') OR r.lease_active=1"
            total = connection.execute("SELECT COUNT(*) FROM runtime_runs r WHERE " + where).fetchone()[0]
            rows = connection.execute("""
                SELECT r.run_id,r.conversation_id,r.mode,r.status,r.lease_active,r.request_json,
                       a.work_order_id,a.task_id
                FROM runtime_runs r LEFT JOIN work_order_attempts a ON a.run_id=r.run_id
                WHERE """ + where + " ORDER BY r.created_at DESC,r.run_id DESC LIMIT ?", (limit,)).fetchall()
            items = []
            for row in rows:
                item = {key: row[key] for key in ("run_id", "conversation_id", "mode", "status", "work_order_id", "task_id")}
                item["lease_active"] = bool(row["lease_active"])
                item["title"] = " ".join(str(json.loads(row["request_json"]).get("prompt", "")).split())[:120]
                items.append(item)
            return {"items": items, "total": total, "limit": limit}

    def replay(self, request: dict[str, Any]) -> dict[str, Any] | None:
        from ..context.input import normalize_request

        request = normalize_request(request)
        key = request.get("idempotency_key")
        if not key:
            return None
        with sqlite_connection(self.database) as connection:
            row = connection.execute("SELECT * FROM runtime_runs WHERE idempotency_key=?", (key,)).fetchone()
        if row:
            if json.loads(row["request_json"]) != request:
                raise ValueError("idempotency key was reused with a different request")
            return self._decode(row)
        return None

    def update(
        self,
        run_id: str,
        status: str,
        *,
        answer: str = "",
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        started = now if status == "running" else None
        finished = now if status in TERMINAL_STATUSES else None
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
            if not existing:
                raise KeyError(run_id)
            connection.execute(
                "UPDATE runtime_runs SET status=?,answer=?,error=?,metadata_json=?,updated_at=?,"
                "started_at=COALESCE(started_at,?),finished_at=COALESCE(?,finished_at) WHERE run_id=?",
                (
                    status,
                    answer,
                    error,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                    now,
                    started,
                    finished,
                    run_id,
                ),
            )
            project_run(connection, run_id)
            if existing["conversation_id"] and status != existing["status"]:
                self._projection_event(connection, run_id, existing["thread_id"], existing["conversation_id"], status, now)
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row)

    def request_cancel(self, run_id: str) -> bool:
        with sqlite_connection(self.database) as connection:
            cursor = connection.execute(
                "UPDATE runtime_runs SET cancel_requested=1,updated_at=? WHERE run_id=?",
                (datetime.now(UTC).isoformat(), run_id),
            )
        return cursor.rowcount == 1

    def release_conversation_turn(self, run_id: str) -> None:
        """Release only after the owned operation has drained all durable writes.

        A completed status can be visible while cancellation is draining its
        commit; do not allow the next turn to inherit that uncertain checkpoint.
        Interrupted turns deliberately retain their lease until a decision.
        """
        with sqlite_connection(self.database) as connection:
            connection.execute(
                "UPDATE runtime_runs SET lease_active=0 WHERE run_id=? "
                "AND status IN ('completed','failed','cancelled','interrupted_expired')", (run_id,),
            )

    def claim_interrupt(self, run_id: str, interrupt_id: str, decision: dict[str, Any], *, ttl_seconds: int) -> dict[str, Any]:
        """Consume one pending interrupt and append its decision atomically.

        A queued claim lost before scheduling is terminalized by normal restart
        recovery, never replayed blindly. This is at-most-one approval claim,
        not a claim of transactional/exactly-once external tool execution.
        """
        expired = False
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
            if not existing:
                raise KeyError(run_id)
            record = self._decode(existing)
            now = datetime.now(UTC)
            timestamp = now.isoformat()
            if record["status"] != "interrupted" or record["cancel_requested"]:
                raise ValueError("run is not waiting for an interrupt")
            known = {item.get("id") for item in record["metadata"].get("interrupts", [])}
            if interrupt_id not in known:
                raise ValueError("interrupt id does not match the pending approval")
            expired = (now - datetime.fromisoformat(record["updated_at"])).total_seconds() > ttl_seconds
            if expired:
                connection.execute("UPDATE runtime_runs SET status='interrupted_expired',lease_active=0,updated_at=?,finished_at=? WHERE run_id=?",
                                   (timestamp, timestamp, run_id))
                payload = {"interrupt_id": interrupt_id}
                event_type = "approval.expired"
            else:
                connection.execute("UPDATE runtime_runs SET status='queued',updated_at=? WHERE run_id=?", (timestamp, run_id))
                payload = {"interrupt_id": interrupt_id, "decision": decision}
                event_type = "approval.decided"
            connection.execute("INSERT INTO runtime_events(run_id,thread_id,type,timestamp,payload_json) VALUES(?,?,?,?,?)",
                               (run_id, record["thread_id"], event_type, timestamp, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
            project_run(connection, run_id)
        if expired:
            raise TimeoutError("approval interrupt has expired")  # after committing the durable expiry
        return self._decode(row)

    def recover_incomplete(
        self, error: str = "service restarted before completion"
    ) -> list[dict[str, Any]]:
        """Atomically terminalize durable runs whose in-memory lease was lost."""
        now = datetime.now(UTC).isoformat()
        recovered: list[dict[str, Any]] = []
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM runtime_runs WHERE status IN ('queued','running') ORDER BY created_at"
            ).fetchall()
            for row in rows:
                previous = row["status"]
                connection.execute(
                    "UPDATE runtime_runs SET status='failed',error=?,updated_at=?,finished_at=? "
                    "WHERE run_id=? AND status=?",
                    (error, now, now, row["run_id"], previous),
                )
                payload = json.dumps(
                    {"previous": previous, "status": "failed", "error": error},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                connection.execute(
                    "INSERT INTO runtime_events(run_id,thread_id,type,timestamp,payload_json) "
                    "VALUES(?,?,?,?,?)",
                    (row["run_id"], row["thread_id"], "run.recovered_after_restart", now, payload),
                )
                project_run(connection, row["run_id"])
                recovered.append(
                    {
                        "run_id": row["run_id"],
                        "thread_id": row["thread_id"],
                        "previous": previous,
                        "status": "failed",
                        "error": error,
                    }
                )
            connection.execute(
                "UPDATE runtime_runs SET lease_active=0 WHERE status IN ('completed','failed','cancelled','interrupted_expired')"
            )
        return recovered

    @staticmethod
    def _projection_event(connection, run_id, thread_id, conversation_id, status, timestamp):
        connection.execute(
            "INSERT INTO runtime_events(run_id,thread_id,type,timestamp,payload_json) VALUES(?,?,?,?,?)",
            (run_id, thread_id, "conversation.turn_updated", timestamp,
             json.dumps({"conversation_id": conversation_id, "status": status}, separators=(",", ":"))),
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "conversation_id": row["conversation_id"],
            "lease_active": bool(row["lease_active"]),
            "write_scope": bool(row["write_scope"]),
            "profile_snapshot": json.loads(row["profile_json"]),
            "thread_id": row["thread_id"],
            "status": row["status"],
            "mode": row["mode"],
            "request": json.loads(row["request_json"]),
            "input_snapshot": json.loads(row["input_snapshot_json"]) if row["input_snapshot_json"] else None,
            "answer": row["answer"],
            "error": row["error"],
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "cancel_requested": bool(row["cancel_requested"]),
        }
