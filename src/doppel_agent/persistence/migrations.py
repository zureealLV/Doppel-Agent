"""Small forward-only schema migrator for runtime API state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .database import sqlite_connection


MIGRATIONS = {
    1: """
        CREATE TABLE IF NOT EXISTS runtime_runs (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            idempotency_key TEXT UNIQUE,
            request_json TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS runtime_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_events_run_seq
            ON runtime_events(run_id, seq);
    """,
}


def apply_migrations(database: Path) -> int:
    with sqlite_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
        for version, sql in sorted(MIGRATIONS.items()):
            if version in applied:
                continue
            connection.executescript(sql)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (version, datetime.now(UTC).isoformat()),
            )
    return max(MIGRATIONS, default=0)
