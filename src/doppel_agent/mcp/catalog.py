"""Paginated and cached MCP tool discovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from .client_manager import MCPAdmissionError, MCPClientManager, _MCPSessionSource
from .types import MCPToolDescriptor


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return slug or "tool"


class MCPToolCatalog:
    def __init__(self, manager: MCPClientManager) -> None:
        self.manager = manager
        self._cache: dict[str, tuple[str, int, tuple[MCPToolDescriptor, ...]]] = {}
        self._logical: dict[str, MCPToolDescriptor] = {}
        self._sources: dict[str, _MCPSessionSource] = {}
        self._unavailable: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    def cached_server(self, server: str) -> tuple[str, tuple[MCPToolDescriptor, ...] | None]:
        """Memory-only snapshot, NOT current remote metadata or health validation.

        Unlike list_server(), this never calls manager.get(), opens a transport,
        discovers tools, invalidates a cache or changes the session generation.
        Missing/stale is distinct from a successfully discovered empty catalog.
        A historical cached projection is NOT descriptor invocation permission;
        failed/in-progress explicit refresh refuses source_for_descriptor().
        """
        cached = self._cache.get(server)
        if cached is None:
            return "missing", None
        if cached[1] != self.manager.generation(server):
            return "stale_generation", None
        return "cached", cached[2]

    @staticmethod
    def _descriptor(server: str, tool: Any) -> MCPToolDescriptor:
        schema = dict(getattr(tool, "input_schema", None) or {})
        output_schema = getattr(tool, "output_schema", None)
        annotations = getattr(tool, "annotations", None)
        if hasattr(annotations, "model_dump"):
            annotations = annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
        digest = hashlib.sha256(
            json.dumps(
                {"input": schema, "output": output_schema},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        remote_name = str(tool.name)
        return MCPToolDescriptor(
            logical_name=f"mcp__{_slug(server)}__{_slug(remote_name)}",
            server_name=server,
            remote_name=remote_name,
            title=str(getattr(tool, "title", None) or remote_name),
            description=str(getattr(tool, "description", None) or "MCP tool; description unavailable"),
            input_schema=schema,
            output_schema=dict(output_schema) if isinstance(output_schema, dict) else None,
            annotations=dict(annotations) if isinstance(annotations, dict) else None,
            schema_hash=digest,
        )

    async def list_server(self, server: str, *, refresh: bool = False) -> tuple[MCPToolDescriptor, ...]:
        self.manager._check_reentrant(server)  # Refuse SAME-task SDK/catalog self-wait before lock.
        if server not in self.manager.config.servers:
            raise KeyError(f"unknown MCP server: {server}")
        lock = self._locks.setdefault(server, asyncio.Lock())
        async with lock:
            # Same original catalog/server serializes response publication;
            # older slow refresh cannot overwrite a later same-session refresh
            # or clear the later active refresh's unavailable marker.
            return await self._list_server(server, refresh=refresh)

    async def _list_server(self, server: str, *, refresh: bool) -> tuple[MCPToolDescriptor, ...]:
        managed = await self.manager.get(server)
        cache_key = managed.metadata.cache_key
        generation = self.manager.generation(server)
        cached = self._cache.get(server)
        if (
            cached is not None
            and cached[0] == cache_key
            and cached[1] == generation
            and not refresh
            and server not in self._unavailable
        ):
            return cached[2]

        # Preserve old maps as historical cache, but they cannot authorize old
        # schema invocation while original refresh is in progress/has failed.
        # A known later successful discovery can replace this metadata state;
        # it never resets the manager's execution/resource-failure latch.
        self._unavailable.add(server)

        async def collect(session: Any) -> list[Any]:
            from mcp import types

            tools: list[Any] = []
            cursor: str | None = None
            seen: set[str] = set()
            pages = 0
            while True:
                params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
                page = await session.list_tools(params=params)
                pages += 1
                tools.extend(page.tools)
                if len(tools) > 4096:
                    raise ValueError("MCP pagination exceeds the 4096-tool catalog limit")
                cursor = page.next_cursor
                if not cursor:
                    return tools
                if cursor in seen or pages >= 100:
                    raise ValueError("MCP pagination repeated a cursor or exceeds 100 pages")
                seen.add(cursor)

        observed = await self.manager.call(server, collect, reconnect=True, with_source=True)
        descriptors = tuple(self._descriptor(server, tool) for tool in observed.value)
        logical_names = [item.logical_name for item in descriptors]
        if len(logical_names) != len(set(logical_names)):
            raise ValueError(f"MCP tool name collision on server {server}")
        previous = self._cache.get(server)
        proposed = dict(self._logical)
        if previous:
            # A rename can have exactly the same schema hash. Remove every
            # previous SAME descriptor, not only hash-different old entries.
            for item in previous[2]:
                if proposed.get(item.logical_name) is item:
                    proposed.pop(item.logical_name)
        for descriptor in descriptors:
            if descriptor.logical_name in proposed and proposed[descriptor.logical_name] != descriptor:
                raise ValueError(f"duplicate logical MCP tool: {descriptor.logical_name}")
            proposed[descriptor.logical_name] = descriptor
        # No await between original source check and coherent same-loop map
        # publication. Failed/stale/collision refresh never partially commits.
        self.manager.check_source(observed.source)
        self._cache[server] = (observed.source.cache_key, observed.source.generation, descriptors)
        self._sources[server] = observed.source
        self._logical = proposed
        self._unavailable.discard(server)
        return descriptors

    async def list_all(self, *, refresh: bool = False) -> tuple[MCPToolDescriptor, ...]:
        tools = []
        for server in self.manager.config.servers:
            tools.extend(await self.list_server(server, refresh=refresh))
        return tuple(tools)

    async def get(self, logical_name: str) -> MCPToolDescriptor:
        previous = self._logical.get(logical_name)
        if previous is None:
            await self.list_all()
        else:
            # A known logical descriptor can outlive its original SDK session.
            # Refresh the SAME configured server before returning a descriptor,
            # rather than silently validating/invoking its historical schema.
            await self.list_server(previous.server_name)
        try:
            return self._logical[logical_name]
        except KeyError as exc:
            raise KeyError(f"unknown MCP tool: {logical_name}") from exc

    def source_for_descriptor(self, descriptor: MCPToolDescriptor) -> _MCPSessionSource:
        """Memory-only SAME catalog object/source binding, not remote health."""
        source = self._sources.get(descriptor.server_name)
        if (source is None or descriptor.server_name in self._unavailable
                or self._logical.get(descriptor.logical_name) is not descriptor):
            raise MCPAdmissionError()
        self.manager.check_source(source)
        return source
