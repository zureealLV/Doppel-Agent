"""Durable ordered event log for API and SSE consumers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import sqlite_connection
from .migrations import apply_migrations


class EventStore:
    def __init__(self, database: Path):
        self.database = database
        apply_migrations(database)

    def append(
        self,
        run_id: str,
        thread_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = datetime.now(UTC).isoformat()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with sqlite_connection(self.database) as connection:
            cursor = connection.execute(
                "INSERT INTO runtime_events(run_id,thread_id,type,timestamp,payload_json) VALUES(?,?,?,?,?)",
                (run_id, thread_id, event_type, timestamp, encoded),
            )
            seq = int(cursor.lastrowid)
        return {
            "seq": seq,
            "run_id": run_id,
            "thread_id": thread_id,
            "type": event_type,
            "timestamp": timestamp,
            "payload": payload,
        }

    def list(self, run_id: str, *, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        if after_seq < 0 or not 1 <= limit <= 2000:
            raise ValueError("invalid event query")
        with sqlite_connection(self.database) as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after_seq, limit),
            ).fetchall()
        return [
            {
                "seq": row["seq"],
                "run_id": row["run_id"],
                "thread_id": row["thread_id"],
                "type": row["type"],
                "timestamp": row["timestamp"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
