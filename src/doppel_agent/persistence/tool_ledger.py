"""Idempotency ledger for graph tool side effects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


class ToolExecutionLedger:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
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

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                    raise ValueError("tool call id was reused with different input")
                if existing["status"] == "completed":
                    return existing["result"], True
                if existing["status"] == "failed":
                    raise ValueError(f"previous tool execution failed: {existing['error']}")
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
                    (f"{type(exc).__name__}: {exc}", run_id, tool_call_id),
                )
            raise
        with self._connect() as connection:
            connection.execute(
                "UPDATE tool_executions SET status='completed',result=? WHERE run_id=? AND tool_call_id=?",
                (result, run_id, tool_call_id),
            )
        return result, False
