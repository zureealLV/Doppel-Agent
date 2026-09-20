"""Secure, WAL-backed SQLite checkpointers for local LangGraph runs."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def sqlite_checkpointer(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    # Checkpoints contain application-owned plain state. Strict msgpack prevents
    # an altered database from requesting arbitrary Python module loading.
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    async with AsyncSqliteSaver.from_conn_string(str(database)) as saver:
        await saver.conn.execute("PRAGMA journal_mode=WAL")
        await saver.conn.execute("PRAGMA foreign_keys=ON")
        await saver.conn.execute("PRAGMA busy_timeout=5000")
        await saver.conn.commit()
        yield saver
