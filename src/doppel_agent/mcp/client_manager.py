"""MCP SDK session lifecycle, health checks, reconnects and concurrency."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .config import MCPConfig, MCPServerConfig
from .types import MCPServerMetadata, ManagedMCPSession


Connector = Callable[[MCPServerConfig], Any]


async def _drain_owner(task: asyncio.Task[None]) -> None:
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


@dataclass
class _OwnedConnection:
    """SDK cancel scopes must enter and exit in the same lifetime task."""

    ready: asyncio.Future[tuple[Any, Any]]
    release: asyncio.Event
    task: asyncio.Task[None]

    async def close(self) -> None:
        self.release.set()
        await _drain_owner(self.task)


class MCPClientManager:
    def __init__(self, config: MCPConfig, *, connector: Connector | None = None) -> None:
        self.config = config
        self.connector = connector or self._default_connector
        self._clients: dict[str, ManagedMCPSession] = {}
        self._connections: dict[str, _OwnedConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._semaphores = {
            name: asyncio.Semaphore(server.max_concurrency) for name, server in config.servers.items()
        }
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def _default_connector(self, server: MCPServerConfig) -> AsyncIterator[tuple[Any, Any]]:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
        from mcp.client.streamable_http import streamable_http_client

        async with AsyncExitStack() as stack:
            if server.transport == "stdio":
                missing = [name for name in server.env_names if name not in os.environ]
                if missing:
                    raise ValueError(f"missing MCP environment variables: {', '.join(missing)}")
                environment = get_default_environment()
                environment.update({name: os.environ[name] for name in server.env_names})
                parameters = StdioServerParameters(
                    command=server.command or "",
                    args=list(server.args),
                    env=environment,
                    cwd=server.cwd,
                )
                read_stream, write_stream = await stack.enter_async_context(stdio_client(parameters))
            else:
                http_client = None
                if server.auth_profile:
                    import httpx2

                    variable = "DOPPEL_MCP_AUTH_" + server.auth_profile.upper().replace("-", "_") + "_TOKEN"
                    token = os.environ.get(variable)
                    if not token:
                        raise ValueError(f"missing MCP auth profile environment: {variable}")
                    http_client = await stack.enter_async_context(
                        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
                    )
                streams = await stack.enter_async_context(
                    streamable_http_client(server.url or "", http_client=http_client)
                )
                read_stream, write_stream = streams[0], streams[1]
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            initialized = await session.initialize()
            yield session, initialized

    @staticmethod
    def _metadata(server: MCPServerConfig, initialized: Any) -> MCPServerMetadata:
        info = getattr(initialized, "server_info", None)
        capabilities = getattr(initialized, "capabilities", None)
        if hasattr(capabilities, "model_dump"):
            capabilities = capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
        return MCPServerMetadata(
            name=server.name,
            protocol_version=str(getattr(initialized, "protocol_version", "unknown")),
            server_name=str(getattr(info, "name", server.name)),
            server_version=str(getattr(info, "version", "unknown")),
            capabilities=dict(capabilities or {}),
            config_hash=server.identity_hash(),
        )

    async def get(self, name: str) -> ManagedMCPSession:
        if self._closed:
            raise RuntimeError("MCP client manager is closed")
        if name not in self.config.servers:
            raise KeyError(f"unknown MCP server: {name}")
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            if self._closed:
                raise RuntimeError("MCP client manager is closed")
            existing = self._clients.get(name)
            if existing is not None and existing.valid:
                return existing
            await self._discard(name)
            ready = asyncio.get_running_loop().create_future()
            release = asyncio.Event()

            async def own_connection() -> None:
                try:
                    async with self.connector(self.config.servers[name]) as opened:
                        ready.set_result(opened)
                        await release.wait()
                except BaseException as exc:
                    if not ready.done():
                        ready.set_exception(exc)
                    else:
                        raise

            connection = _OwnedConnection(ready, release, asyncio.create_task(own_connection()))
            self._connections[name] = connection
            try:
                session, initialized = await asyncio.shield(ready)
            except BaseException:
                try:
                    await self._discard(name)
                finally:
                    # Even a repeatedly cancelled caller must consume startup errors.
                    if ready.done() and not ready.cancelled():
                        ready.exception()
                raise
            managed = ManagedMCPSession(
                session=session,
                metadata=self._metadata(self.config.servers[name], initialized),
                semaphore=self._semaphores[name],
            )
            self._clients[name] = managed
            self._generations[name] = self._generations.get(name, 0) + 1
            return managed

    def generation(self, name: str) -> int:
        """Monotonic in-process session generation used by schema caches."""
        return self._generations.get(name, 0)

    async def _discard(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client is not None:
            client.valid = False
        connection = self._connections.pop(name, None)
        if connection is not None:
            await connection.close()

    async def invalidate(self, name: str) -> None:
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            await self._discard(name)

    @asynccontextmanager
    async def session(self, name: str) -> AsyncIterator[ManagedMCPSession]:
        managed = await self.get(name)
        async with managed.semaphore:
            yield managed

    async def call(self, name: str, operation: Callable[[Any], Any], *, reconnect: bool = True) -> Any:
        attempts = 2 if reconnect else 1
        for attempt in range(attempts):
            try:
                async with self.session(name) as managed:
                    return await operation(managed.session)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.invalidate(name)
                if attempt + 1 >= attempts:
                    raise
        raise RuntimeError("unreachable MCP retry state")

    async def health(self, name: str) -> MCPServerMetadata:
        async def probe(session: Any) -> Any:
            await session.list_tools()
            return None

        await self.call(name, probe, reconnect=True)
        return (await self.get(name)).metadata

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_all())
        await _drain_owner(self._close_task)

    async def _close_all(self) -> None:
        errors: list[BaseException] = []
        for name in self.config.servers:
            lock = self._locks.setdefault(name, asyncio.Lock())
            async with lock:
                try:
                    await self._discard(name)
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise BaseExceptionGroup("MCP connector shutdown failed", errors)
