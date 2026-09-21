"""Paginated and cached MCP tool discovery."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .client_manager import MCPClientManager
from .types import MCPToolDescriptor


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return slug or "tool"


class MCPToolCatalog:
    def __init__(self, manager: MCPClientManager) -> None:
        self.manager = manager
        self._cache: dict[str, tuple[str, tuple[MCPToolDescriptor, ...]]] = {}
        self._logical: dict[str, MCPToolDescriptor] = {}

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
        managed = await self.manager.get(server)
        cache_key = managed.metadata.cache_key
        cached = self._cache.get(server)
        if cached is not None and cached[0] == cache_key and not refresh:
            return cached[1]

        async def collect(session: Any) -> list[Any]:
            from mcp import types

            tools: list[Any] = []
            cursor: str | None = None
            while True:
                params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
                page = await session.list_tools(params=params)
                tools.extend(page.tools)
                cursor = page.next_cursor
                if not cursor:
                    return tools

        raw_tools = await self.manager.call(server, collect, reconnect=True)
        cache_key = (await self.manager.get(server)).metadata.cache_key
        descriptors = tuple(self._descriptor(server, tool) for tool in raw_tools)
        logical_names = [item.logical_name for item in descriptors]
        if len(logical_names) != len(set(logical_names)):
            raise ValueError(f"MCP tool name collision on server {server}")
        previous = self._cache.get(server)
        if previous and tuple(item.schema_hash for item in previous[1]) != tuple(
            item.schema_hash for item in descriptors
        ):
            for item in previous[1]:
                self._logical.pop(item.logical_name, None)
        self._cache[server] = (cache_key, descriptors)
        for descriptor in descriptors:
            if descriptor.logical_name in self._logical and self._logical[descriptor.logical_name] != descriptor:
                raise ValueError(f"duplicate logical MCP tool: {descriptor.logical_name}")
            self._logical[descriptor.logical_name] = descriptor
        return descriptors

    async def list_all(self, *, refresh: bool = False) -> tuple[MCPToolDescriptor, ...]:
        tools = []
        for server in self.manager.config.servers:
            tools.extend(await self.list_server(server, refresh=refresh))
        return tuple(tools)

    async def get(self, logical_name: str) -> MCPToolDescriptor:
        if logical_name not in self._logical:
            await self.list_all()
        try:
            return self._logical[logical_name]
        except KeyError as exc:
            raise KeyError(f"unknown MCP tool: {logical_name}") from exc
