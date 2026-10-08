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
from ..persistence.owned import await_durable
from .base import EventSink, NullEventSink


def _check_generation(actual: int, expected: int | None) -> None:
    if expected is None:
        return  # legacy callers retain their existing unpinned semantics
    if type(expected) is not int or expected < 1:
        raise ValueError("invalid_subagent_generation")
    if actual != expected:
        raise ValueError("subagent_generation_changed")


@dataclass(frozen=True)
class AsyncSubagentRequest:
    subagent_id: str
    parent_run_id: str
    prompt: str
    history: tuple[dict[str, str], ...]
    permissions: frozenset[str] = frozenset({"workspace_read"})
    allow_delegate: bool = False
    generation: int = 1


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
        return self._record(row)

    @staticmethod
    def _record(row) -> dict[str, Any]:
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

    @staticmethod
    def _review_page(record: dict[str, Any], offset: int, limit: int) -> dict[str, Any]:
        history = record["history"]
        return {**record, "history": history[offset:offset + limit],
                "history_total": len(history), "history_offset": offset, "history_limit": limit,
                "history_truncated": offset > 0 or offset + limit < len(history)}

    def review_snapshot(self, parent_run_id: str) -> dict[str, Any]:
        """One original-store read snapshot, latest 16 turns, no history mutation.

        UI projection is bounded, not the runtime's durable context. Parsing the
        existing history JSON still reads the full local history. No new store.
        """
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN")
            total = connection.execute(
                "SELECT COUNT(*) AS count FROM async_subagents WHERE parent_run_id=?", (parent_run_id,),
            ).fetchone()["count"]
            active = connection.execute(
                "SELECT COUNT(*) AS count FROM async_subagents WHERE parent_run_id=? "
                "AND status IN ('queued','running')", (parent_run_id,),
            ).fetchone()["count"]
            rows = connection.execute(
                "SELECT * FROM async_subagents WHERE parent_run_id=? ORDER BY created_at,subagent_id LIMIT 100",
                (parent_run_id,),
            ).fetchall()
        records = [self._record(row) for row in rows]
        return {"parent_run_id": parent_run_id,
                "items": [self._review_page(record, max(0, len(record["history"]) - 16), 16) for record in records],
                "total": total, "limit": 100, "truncated": total > 100,
                "counts": {"lifetime_for_parent": total, "durable_active_for_parent": active}}

    def review_history(self, parent_run_id: str, subagent_id: str, *, expected_generation: int,
                       offset: int, limit: int = 16) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("invalid_subagent_history_page")
        with sqlite_connection(self.database) as connection:
            row = connection.execute(
                "SELECT * FROM async_subagents WHERE parent_run_id=? AND subagent_id=?", (parent_run_id, subagent_id),
            ).fetchone()
        if row is None:
            raise KeyError(subagent_id)
        # Same immutable row supplies scope, generation and the entire history;
        # a later committed turn cannot be silently mixed into an older page.
        _check_generation(row["generation"], expected_generation)
        record = self._record(row)
        if offset > len(record["history"]):
            raise ValueError("invalid_subagent_history_page")
        return self._review_page(record, offset, limit)

    def list_for_parent(self, parent_run_id: str) -> list[dict[str, Any]]:
        with sqlite_connection(self.database) as connection:
            rows = connection.execute(
                "SELECT subagent_id FROM async_subagents WHERE parent_run_id=? "
                "ORDER BY created_at,subagent_id",
                (parent_run_id,),
            ).fetchall()
        return [record for row in rows if (record := self.get(row["subagent_id"])) is not None]

    def update(self, subagent_id: str, status: str, *, answer: str = "", error: str = "") -> None:
        with sqlite_connection(self.database) as connection:
            connection.execute(
                "UPDATE async_subagents SET status=?,answer=?,error=?,updated_at=? "
                "WHERE subagent_id=?",
                (status, answer, error, datetime.now(UTC).isoformat(), subagent_id),
            )

    def follow_up(self, subagent_id: str, prompt: str, *, expected_generation: int | None = None) -> dict[str, Any]:
        with sqlite_connection(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM async_subagents WHERE subagent_id=?", (subagent_id,)).fetchone()
            if row is None:
                raise KeyError(subagent_id)
            # Compare within the same BEGIN IMMEDIATE as history/generation
            # advancement. A fast completed newer turn is not the reviewed turn.
            _check_generation(row["generation"], expected_generation)
            if row["status"] != "completed":
                raise ValueError("follow-up requires a completed subagent")
            history = [*json.loads(row["history_json"]), {"prompt": row["prompt"], "answer": row["answer"]}]
            connection.execute(
                "UPDATE async_subagents SET status='queued',prompt=?,history_json=?,answer='',"
                "error='',generation=generation+1,updated_at=? WHERE subagent_id=?",
                (prompt, json.dumps(history, ensure_ascii=False, separators=(",", ":")),
                 datetime.now(UTC).isoformat(), subagent_id),
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
        self._start_lock = asyncio.Lock()
        # Serialize generation claim/admission with cancel of that stable child
        # id. This is not a second scheduler; the original scheduler owns jobs.
        self._child_mutations: dict[str, asyncio.Lock] = {}

    def _child_lock(self, subagent_id: str) -> asyncio.Lock:
        if subagent_id not in self._child_mutations:
            self._child_mutations[subagent_id] = asyncio.Lock()
        return self._child_mutations[subagent_id]

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await await_durable(asyncio.to_thread(self.store.fail_incomplete_after_restart))
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
            try:
                token.raise_if_cancelled()
                await await_durable(asyncio.to_thread(self.store.update, subagent_id, "running"))
                await await_durable(self.sink.emit(
                    "subagent.started", subagent_id=subagent_id,
                    parent_run_id=record["parent_run_id"], generation=record["generation"],
                ))
                request = AsyncSubagentRequest(subagent_id, record["parent_run_id"], record["prompt"], tuple(record["history"]),
                                               generation=record["generation"])
                answer = (await self.runner(request))[:8000]
                await await_durable(asyncio.to_thread(self.store.update, subagent_id, "completed", answer=answer))
                await await_durable(self.sink.emit(
                    "subagent.completed", subagent_id=subagent_id,
                    parent_run_id=record["parent_run_id"], generation=record["generation"], answer_chars=len(answer),
                ))
                return answer
            except asyncio.CancelledError:
                await await_durable(self._finish(record, "cancelled"))
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: subagent execution failed"
                await await_durable(self._finish(record, "failed", error=error))
                raise

        try:
            handle = await self.scheduler.submit(subagent_id, operation)
        except (QueueCapacityError, RuntimeError, ValueError):
            await await_durable(self._finish(record, "failed", error="subagent queue rejected task"))
            raise
        handle.future.add_done_callback(self._consume_future)

    @staticmethod
    def _consume_future(future: asyncio.Future[Any]) -> None:
        if not future.cancelled():
            future.exception()

    async def _finish(self, record: dict[str, Any], status: str, *, error: str = "") -> None:
        # The entire cleanup is owned, not individual writes which a repeated
        # cancellation could interrupt between persistence and notification.
        await asyncio.to_thread(self.store.update, record["subagent_id"], status, error=error)
        await self.sink.emit(f"subagent.{status}", subagent_id=record["subagent_id"],
                             parent_run_id=record["parent_run_id"], generation=record["generation"],
                             **({"error": error} if error else {}))

    async def _admit(self, awaitable, event_type: str) -> dict[str, Any]:
        # Both callers hold this child's mutation lock until admission/queued
        # notification settles. Cancellation must not recursively acquire it.
        admission = asyncio.create_task(awaitable)
        try:
            record = await await_durable(admission)
        except asyncio.CancelledError:
            if not admission.cancelled() and admission.exception() is None:
                await await_durable(self._finish(admission.result(), "cancelled"))
            raise
        try:
            await self._submit(record)
            await await_durable(self.sink.emit(event_type, subagent_id=record["subagent_id"],
                                              parent_run_id=record["parent_run_id"], generation=record["generation"]))
            return record
        except asyncio.CancelledError:
            await await_durable(self._cancel_unlocked(record["subagent_id"], expected_generation=record["generation"]))
            raise

    async def spawn(self, parent_run_id: str, prompt: str) -> dict[str, Any]:
        await self.start()
        prompt = self._validate_prompt(prompt)
        subagent_id = uuid4().hex
        async with self._child_lock(subagent_id):
            return await self._admit(asyncio.to_thread(
                self.store.create_bounded, subagent_id, parent_run_id, prompt, self.max_per_parent,
            ), "subagent.queued")

    async def get(self, subagent_id: str) -> dict[str, Any] | None:
        return await await_durable(asyncio.to_thread(self.store.get, subagent_id))

    async def list_for_parent(self, parent_run_id: str) -> list[dict[str, Any]]:
        return await await_durable(asyncio.to_thread(self.store.list_for_parent, parent_run_id))

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

    async def follow_up(self, subagent_id: str, prompt: str, *, expected_generation: int | None = None) -> dict[str, Any]:
        prompt = self._validate_prompt(prompt)
        async with self._child_lock(subagent_id):
            record = await self.get(subagent_id)
            if record is None:
                raise KeyError(subagent_id)
            _check_generation(record["generation"], expected_generation)
            if expected_generation is not None and record["status"] != "completed":
                raise ValueError("follow-up requires a completed subagent")
            # Read row + borrow the matching original job before another claim
            # can advance this stable child id. Never look up the job after the
            # await and accidentally wait for a newly admitted generation.
            previous_completion = self.scheduler.completion(subagent_id)
        # The runner persists ``completed`` before emitting the completion event.
        # A client can therefore observe the durable terminal state while the
        # scheduler still owns the previous generation. Wait for that generation
        # to be released before reusing the stable subagent id. Completed records
        # restored after a process restart have no in-memory scheduler job.
        # Never hold the mutation lock while waiting on the previous generation's
        # scheduler drain: a cancellation must still reach that old generation.
        try:
            if previous_completion is not None:
                await asyncio.shield(previous_completion)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is None or task.cancelling():
                raise
            # Cancellation of the *old child*, not this caller: after its original
            # completion settles, the SQL status/CAS below rejects any new turn.
        except Exception:
            # A post-persistence event-sink failure must not make a durable,
            # completed child impossible to continue.
            pass
        async with self._child_lock(subagent_id):
            options = {} if expected_generation is None else {"expected_generation": expected_generation}
            return await self._admit(asyncio.to_thread(self.store.follow_up, subagent_id, prompt, **options), "subagent.followed_up")

    async def cancel(self, subagent_id: str, *, expected_generation: int | None = None) -> bool:
        async with self._child_lock(subagent_id):
            return await self._cancel_unlocked(subagent_id, expected_generation=expected_generation)

    async def _cancel_unlocked(self, subagent_id: str, *, expected_generation: int | None = None) -> bool:
        record = await self.get(subagent_id)
        if record is None:
            return False
        _check_generation(record["generation"], expected_generation)
        previous = self.scheduler.status(subagent_id)
        cancelled = await self.scheduler.cancel(subagent_id)
        if cancelled and previous == "queued":
            await await_durable(self._finish(record, "cancelled"))
        # Running/pre-entry operations own their drain and durable terminal state.
        return cancelled

    async def close(self) -> None:
        await await_durable(self.scheduler.shutdown())
        self._started = False
