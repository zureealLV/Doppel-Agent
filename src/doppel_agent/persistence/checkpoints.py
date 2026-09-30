"""Secure, WAL-backed SQLite checkpointers for local LangGraph runs."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


async def _drain_owned_task(task: asyncio.Future):
    """Finish acquired-resource work despite repeated caller cancellation."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


@asynccontextmanager
async def sqlite_checkpointer(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    # Checkpoints contain application-owned plain state. Strict msgpack prevents
    # an altered database from requesting arbitrary Python module loading.
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    # aiosqlite opens SQLite on a worker thread. Cancelling its connection
    # future can discard an already-acquired handle before it is assigned to
    # the proxy. Own/shield acquisition, then close it before propagating cancel.
    opening = asyncio.ensure_future(aiosqlite.connect(str(database)))
    try:
        connection = await asyncio.shield(opening)
    except asyncio.CancelledError:
        try:
            connection, _ = await _drain_owned_task(opening)
        except Exception:
            # The opening failed, so there is no acquired connection to close.
            raise asyncio.CancelledError from None
        await _drain_owned_task(asyncio.create_task(connection.close()))
        raise
    try:
        saver = AsyncSqliteSaver(connection)
        await saver.conn.execute("PRAGMA journal_mode=WAL")
        await saver.conn.execute("PRAGMA foreign_keys=ON")
        await saver.conn.execute("PRAGMA busy_timeout=5000")
        await saver.conn.commit()
        yield saver
    finally:
        _, cancelled = await _drain_owned_task(asyncio.create_task(connection.close()))
        if cancelled:
            raise asyncio.CancelledError
