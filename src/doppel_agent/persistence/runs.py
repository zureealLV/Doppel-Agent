"""SQLite run records and guarded status transitions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import sqlite_connection
from .migrations import apply_migrations


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
    ) -> tuple[dict[str, Any], bool]:
        now = datetime.now(UTC).isoformat()
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        with sqlite_connection(self.database) as connection:
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
            connection.execute(
                "INSERT INTO runtime_runs(run_id,thread_id,status,mode,idempotency_key,"
                "request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, thread_id, "queued", mode, idempotency_key, encoded, now, now),
            )
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row), True

    def get(self, run_id: str) -> dict[str, Any] | None:
        with sqlite_connection(self.database) as connection:
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row) if row else None

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
            row = connection.execute("SELECT * FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row)

    def request_cancel(self, run_id: str) -> bool:
        with sqlite_connection(self.database) as connection:
            cursor = connection.execute(
                "UPDATE runtime_runs SET cancel_requested=1,updated_at=? WHERE run_id=?",
                (datetime.now(UTC).isoformat(), run_id),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "mode": row["mode"],
            "request": json.loads(row["request_json"]),
            "answer": row["answer"],
            "error": row["error"],
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "cancel_requested": bool(row["cancel_requested"]),
        }
