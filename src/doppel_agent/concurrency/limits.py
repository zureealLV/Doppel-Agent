"""Named resource semaphores used by providers, MCP servers and commands."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ResourceLimits:
    def __init__(
        self,
        *,
        model_calls_per_profile: int = 2,
        mcp_calls_per_server: int = 4,
        command_processes: int = 2,
    ):
        if min(model_calls_per_profile, mcp_calls_per_server, command_processes) < 1:
            raise ValueError("resource limits must be positive")
        self.model_calls_per_profile = model_calls_per_profile
        self.mcp_calls_per_server = mcp_calls_per_server
        self._providers: dict[str, asyncio.Semaphore] = {}
        self._mcp_servers: dict[str, asyncio.Semaphore] = {}
        self._commands = asyncio.Semaphore(command_processes)
        self._guard = asyncio.Lock()

    async def _named(
        self, collection: dict[str, asyncio.Semaphore], name: str, capacity: int
    ) -> asyncio.Semaphore:
        async with self._guard:
            return collection.setdefault(name, asyncio.Semaphore(capacity))

    @asynccontextmanager
    async def provider(self, profile_id: str) -> AsyncIterator[None]:
        semaphore = await self._named(self._providers, profile_id, self.model_calls_per_profile)
        async with semaphore:
            yield

    @asynccontextmanager
    async def mcp_server(self, server: str) -> AsyncIterator[None]:
        semaphore = await self._named(self._mcp_servers, server, self.mcp_calls_per_server)
        async with semaphore:
            yield

    @asynccontextmanager
    async def command(self) -> AsyncIterator[None]:
        async with self._commands:
            yield
