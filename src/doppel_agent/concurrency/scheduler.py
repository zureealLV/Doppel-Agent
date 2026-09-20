"""Bounded FIFO scheduler for local agent runs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .cancellation import CancellationToken


class QueueCapacityError(RuntimeError):
    """Raised when the bounded waiting queue cannot accept another run."""


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    future: asyncio.Future[Any]
    token: CancellationToken


@dataclass
class _Job:
    run_id: str
    operation: Callable[[CancellationToken], Awaitable[Any]]
    future: asyncio.Future[Any]
    token: CancellationToken


class AsyncRunScheduler:
    """Execute at most ``max_active`` jobs and bound jobs waiting in memory."""

    _STOP = object()

    def __init__(self, *, max_active: int = 4, queue_capacity: int = 100):
        if max_active < 1 or queue_capacity < 1:
            raise ValueError("scheduler limits must be positive")
        self.max_active = max_active
        self.queue_capacity = queue_capacity
        self._queue: asyncio.Queue[_Job | object] = asyncio.Queue(maxsize=queue_capacity)
        self._workers: list[asyncio.Task[None]] = []
        self._jobs: dict[str, _Job] = {}
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._statuses: dict[str, str] = {}
        self._accepting = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._workers:
                return
            self._accepting = True
            self._workers = [
                asyncio.create_task(self._worker(), name=f"doppel-run-worker-{index}")
                for index in range(self.max_active)
            ]

    async def submit(
        self,
        run_id: str,
        operation: Callable[[CancellationToken], Awaitable[Any]],
    ) -> RunHandle:
        if not self._accepting:
            raise RuntimeError("scheduler is not accepting runs")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        token = CancellationToken()
        job = _Job(run_id, operation, future, token)
        async with self._lock:
            if run_id in self._jobs and self._statuses.get(run_id) not in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise ValueError("run id is already scheduled")
            try:
                self._queue.put_nowait(job)
            except asyncio.QueueFull as exc:
                raise QueueCapacityError("run queue is full") from exc
            self._jobs[run_id] = job
            self._statuses[run_id] = "queued"
        return RunHandle(run_id, future, token)

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is self._STOP:
                    return
                job = item
                assert isinstance(job, _Job)
                if job.token.cancelled:
                    if not job.future.done():
                        job.future.cancel()
                    self._statuses[job.run_id] = "cancelled"
                    continue
                task = asyncio.create_task(job.operation(job.token), name=f"doppel-run-{job.run_id}")
                self._running[job.run_id] = task
                self._statuses[job.run_id] = "running"
                try:
                    result = await task
                except asyncio.CancelledError:
                    self._statuses[job.run_id] = "cancelled"
                    if not job.future.done():
                        job.future.cancel()
                except Exception as exc:  # noqa: BLE001 - isolate arbitrary run failures
                    self._statuses[job.run_id] = "failed"
                    if not job.future.done():
                        job.future.set_exception(exc)
                else:
                    self._statuses[job.run_id] = "completed"
                    if not job.future.done():
                        job.future.set_result(result)
                finally:
                    self._running.pop(job.run_id, None)
            finally:
                self._queue.task_done()

    async def cancel(self, run_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(run_id)
            if job is None or self._statuses.get(run_id) in {
                "completed",
                "failed",
                "cancelled",
            }:
                return False
            job.token.cancel()
            running = self._running.get(run_id)
            if running is not None:
                running.cancel()
            elif not job.future.done():
                job.future.cancel()
            self._statuses[run_id] = "cancelled"
            return True

    def status(self, run_id: str) -> str | None:
        return self._statuses.get(run_id)

    async def wait(self, run_id: str) -> Any:
        """Wait for the currently scheduled generation of a run."""
        job = self._jobs.get(run_id)
        if job is None:
            raise KeyError(run_id)
        return await asyncio.shield(job.future)

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return len(self._running)

    async def shutdown(self, *, cancel_pending: bool = True, cancel_running: bool = True) -> None:
        async with self._lock:
            if not self._workers:
                self._accepting = False
                return
            self._accepting = False
            if cancel_pending:
                for run_id, job in self._jobs.items():
                    if self._statuses.get(run_id) == "queued":
                        job.token.cancel()
                        if not job.future.done():
                            job.future.cancel()
                        self._statuses[run_id] = "cancelled"
            if cancel_running:
                for run_id, task in tuple(self._running.items()):
                    self._jobs[run_id].token.cancel()
                    task.cancel()
            workers = list(self._workers)
            self._workers.clear()
        if not cancel_pending:
            await self._queue.join()
        for _ in workers:
            await self._queue.put(self._STOP)
        await asyncio.gather(*workers, return_exceptions=True)
