"""Cooperative cancellation state shared with one scheduled run."""

from __future__ import annotations

import asyncio


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError

    async def wait(self) -> None:
        await self._event.wait()
