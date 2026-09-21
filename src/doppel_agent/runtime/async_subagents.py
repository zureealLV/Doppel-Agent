"""Bounded, persistent lifecycle management for read-only background subagents."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..concurrency.scheduler import AsyncRunScheduler, QueueCapacityError
from ..persistence.database import sqlite_connection
from .base import EventSink, NullEventSink


@dataclass(frozen=True)
class AsyncSubagentRequest:
    subagent_id: str
    parent_run_id: str
    prompt: str
    history: tuple[dict[str, str], ...]
    permissions: frozenset[str] = frozenset({"workspace_read"})
    allow_delegate: bool = False


SubagentRunner = Callable[[AsyncSubagentRequest], Awaitable[str]]


class AsyncSubagentStore:
    def __init__(self, database: Path):
        self.database = database
        with sqlite_connection(database) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS async_subagents ("
                "subagent_id TEXT PRIMARY KEY,parent_run_id TEXT NOT NULL,status TEXT NOT NULL,"
                "prompt TEXT NOT NULL,history_json TEXT NOT NULL,answer TEXT NOT NULL DEFAULT '',"
                "error TEXT NOT NULL DEFAULT '',generation INTEGER NOT NULL DEFAULT 1,"
                "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
            )

    def create_bounded(
        self,
        subagent_id: str,
        parent_run_id: str,
        prompt: str,
        max_per_parent: int,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM async_subagents WHERE parent_run_id=?",
                (parent_run_id,),
            ).fetchone()
            if int(row["count"]) >= max_per_parent:
                raise ValueError("subagent budget exhausted for parent run")
            connection.execute(
                "INSERT INTO async_subagents(subagent_id,parent_run_id,status,prompt,history_json,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (subagent_id, parent_run_id, "queued", prompt, "[]", now, now),
            )
        record = self.get(subagent_id)
        assert record is not None
        return record

    def get(self, subagent_id: str) -> dict[str, Any] | None:
        with sqlite_connection(self.database) as connection:
            row = connection.execute(
                "SELECT * FROM async_subagents WHERE subagent_id=?", (subagent_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "subagent_id": row["subagent_id"],
            "parent_run_id": row["parent_run_id"],
            "status": row["status"],
            "prompt": row["prompt"],
            "history": json.loads(row["history_json"]),
            "answer": row["answer"],
            "error": row["error"],
            "generation": row["generation"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update(self, subagent_id: str, status: str, *, answer: str = "", error: str = "") -> None:
        with sqlite_connection(self.database) as connection:
            connection.execute(
                "UPDATE async_subagents SET status=?,answer=?,error=?,updated_at=? "
                "WHERE subagent_id=?",
                (status, answer, error, datetime.now(UTC).isoformat(), subagent_id),
            )

    def follow_up(self, subagent_id: str, prompt: str) -> dict[str, Any]:
        record = self.get(subagent_id)
        if record is None:
            raise KeyError(subagent_id)
        if record["status"] != "completed":
            raise ValueError("follow-up requires a completed subagent")
        history = [*record["history"], {"prompt": record["prompt"], "answer": record["answer"]}]
        with sqlite_connection(self.database) as connection:
            connection.execute(
                "UPDATE async_subagents SET status='queued',prompt=?,history_json=?,answer='',"
                "error='',generation=generation+1,updated_at=? WHERE subagent_id=?",
                (
                    prompt,
                    json.dumps(history, ensure_ascii=False, separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                    subagent_id,
                ),
            )
        updated = self.get(subagent_id)
        assert updated is not None
        return updated

    def fail_incomplete_after_restart(self) -> None:
        with sqlite_connection(self.database) as connection:
            connection.execute(
                "UPDATE async_subagents SET status='failed',error=?,updated_at=? "
                "WHERE status IN ('queued','running')",
                ("subagent manager restarted before completion", datetime.now(UTC).isoformat()),
            )


class AsyncSubagentManager:
    """Run a maximum number of non-recursive read-only child conversations."""

    def __init__(
        self,
        database: Path,
        runner: SubagentRunner,
        *,
        max_active: int = 2,
        queue_capacity: int = 16,
        max_per_parent: int = 2,
        sink: EventSink | None = None,
    ) -> None:
        if max_per_parent < 1:
            raise ValueError("max_per_parent must be positive")
        self.store = AsyncSubagentStore(database)
        self.runner = runner
        self.scheduler = AsyncRunScheduler(max_active=max_active, queue_capacity=queue_capacity)
        self.max_per_parent = max_per_parent
        self.sink = sink or NullEventSink()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await asyncio.to_thread(self.store.fail_incomplete_after_restart)
        await self.scheduler.start()
        self._started = True

    @staticmethod
    def _validate_prompt(prompt: str) -> str:
        value = prompt.strip()
        if not value or len(value) > 4000:
            raise ValueError("subagent prompt must contain 1 to 4000 characters")
        return value

    async def _submit(self, record: dict[str, Any]) -> None:
        subagent_id = record["subagent_id"]

        async def operation(token) -> str:
            token.raise_if_cancelled()
            await asyncio.to_thread(self.store.update, subagent_id, "running")
            await self.sink.emit(
                "subagent.started",
                subagent_id=subagent_id,
                parent_run_id=record["parent_run_id"],
                generation=record["generation"],
            )
            request = AsyncSubagentRequest(
                subagent_id,
                record["parent_run_id"],
                record["prompt"],
                tuple(record["history"]),
            )
            try:
                answer = await self.runner(request)
            except asyncio.CancelledError:
                await asyncio.to_thread(self.store.update, subagent_id, "cancelled")
                await self.sink.emit("subagent.cancelled", subagent_id=subagent_id)
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                await asyncio.to_thread(self.store.update, subagent_id, "failed", error=error)
                await self.sink.emit("subagent.failed", subagent_id=subagent_id, error=error)
                raise
            answer = answer[:8000]
            await asyncio.to_thread(self.store.update, subagent_id, "completed", answer=answer)
            await self.sink.emit(
                "subagent.completed", subagent_id=subagent_id, answer_chars=len(answer)
            )
            return answer

        try:
            handle = await self.scheduler.submit(subagent_id, operation)
        except (QueueCapacityError, RuntimeError, ValueError):
            await asyncio.to_thread(
                self.store.update, subagent_id, "failed", error="subagent queue rejected task"
            )
            raise
        handle.future.add_done_callback(self._consume_future)

    @staticmethod
    def _consume_future(future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            future.exception()

    async def spawn(self, parent_run_id: str, prompt: str) -> dict[str, Any]:
        await self.start()
        prompt = self._validate_prompt(prompt)
        subagent_id = uuid4().hex
        record = await asyncio.to_thread(
            self.store.create_bounded,
            subagent_id,
            parent_run_id,
            prompt,
            self.max_per_parent,
        )
        await self._submit(record)
        await self.sink.emit(
            "subagent.queued", subagent_id=subagent_id, parent_run_id=parent_run_id
        )
        return record

    async def get(self, subagent_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.get, subagent_id)

    async def wait(self, subagent_id: str) -> dict[str, Any]:
        try:
            await self.scheduler.wait(subagent_id)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
        except Exception:
            pass
        record = await self.get(subagent_id)
        if record is None:
            raise KeyError(subagent_id)
        return record

    async def follow_up(self, subagent_id: str, prompt: str) -> dict[str, Any]:
        prompt = self._validate_prompt(prompt)
        record = await asyncio.to_thread(self.store.follow_up, subagent_id, prompt)
        await self._submit(record)
        await self.sink.emit(
            "subagent.followed_up",
            subagent_id=subagent_id,
            generation=record["generation"],
        )
        return record

    async def cancel(self, subagent_id: str) -> bool:
        cancelled = await self.scheduler.cancel(subagent_id)
        if cancelled:
            await asyncio.to_thread(self.store.update, subagent_id, "cancelled")
        return cancelled

    async def close(self) -> None:
        await self.scheduler.shutdown()
        self._started = False
