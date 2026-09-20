"""Writer-preferring asynchronous read/write locks keyed by workspace."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path


class _AsyncRWLock:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    async def acquire_read(self) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: not self._writer and self._waiting_writers == 0)
            self._readers += 1

    async def release_read(self) -> None:
        async with self._condition:
            self._readers -= 1
            self._condition.notify_all()

    async def acquire_write(self) -> None:
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._condition.wait_for(lambda: not self._writer and self._readers == 0)
                self._writer = True
            finally:
                self._waiting_writers -= 1

    async def release_write(self) -> None:
        async with self._condition:
            self._writer = False
            self._condition.notify_all()


class WorkspaceLockManager:
    def __init__(self) -> None:
        self._locks: dict[str, _AsyncRWLock] = {}
        self._guard = asyncio.Lock()

    async def _lock(self, workspace: Path | str) -> _AsyncRWLock:
        key = str(Path(workspace).resolve()).casefold()
        async with self._guard:
            return self._locks.setdefault(key, _AsyncRWLock())

    @asynccontextmanager
    async def read(self, workspace: Path | str) -> AsyncIterator[None]:
        lock = await self._lock(workspace)
        await lock.acquire_read()
        try:
            yield
        finally:
            await lock.release_read()

    @asynccontextmanager
    async def write(self, workspace: Path | str) -> AsyncIterator[None]:
        lock = await self._lock(workspace)
        await lock.acquire_write()
        try:
            yield
        finally:
            await lock.release_write()
