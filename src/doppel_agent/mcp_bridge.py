"""Compatibility facade for the legacy ``mcp_list`` / ``mcp_call`` tools.

New graph and deep runtimes use normalized tools from ``doppel_agent.mcp``.
This module preserves the v0.3 API while routing every call through the same
SDK session, schema, policy, content, and lifecycle implementation.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .mcp import MCPClientManager, MCPToolCatalog, MCPToolExecutor, load_mcp_config
from .permissions import PermissionManager
from .tools import Tool


class MCPBridge:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        self.config = load_mcp_config(self.workspace)
        self.config_path = self.config.source
        self.servers = {name: asdict(server) for name, server in self.config.servers.items()}

    def approval_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        context = dict(arguments)
        config = self.servers.get(arguments.get("server"))
        if config:
            context["configured_transport"] = config["transport"]
            context["configured_command"] = config.get("command")
            context["configured_args"] = list(config.get("args") or ())
            context["forwarded_env_names"] = list(config.get("env_names") or ())
            context["configured_url"] = config.get("url")
            context["auth_profile"] = config.get("auth_profile")
        return context

    async def _invoke(
        self,
        name: str,
        tool: str | None,
        arguments: dict[str, Any] | None,
    ) -> str:
        if name not in self.config.servers:
            raise ValueError(f"unknown MCP server: {name}")
        manager = MCPClientManager(self.config)
        catalog = MCPToolCatalog(manager)
        try:
            descriptors = await catalog.list_server(name)
            if tool is None:
                return json.dumps(
                    [
                        {
                            "name": item.remote_name,
                            "title": item.title,
                            "description": item.description,
                            "input_schema": item.input_schema,
                        }
                        for item in descriptors
                    ],
                    ensure_ascii=False,
                )
            try:
                descriptor = next(item for item in descriptors if item.remote_name == tool)
            except StopIteration as exc:
                raise ValueError(f"unknown MCP tool: {tool}") from exc
            executor = MCPToolExecutor(
                manager,
                catalog,
                PermissionManager(frozenset({"mcp_execute"})),
            )
            result = await executor.execute(
                descriptor.logical_name,
                arguments or {},
                run_id=uuid4().hex,
                tool_call_id=uuid4().hex,
            )
            blocks = result.application_view["content"]
            content = [
                block.get("text")
                if block.get("type") == "text"
                else f"[{block.get('type', 'content')}]"
                for block in blocks
            ]
            return json.dumps(
                {
                    "is_error": not result.success,
                    "content": content,
                    "structured_content": result.application_view.get("structured_content"),
                },
                ensure_ascii=False,
            )
        finally:
            await manager.close()

    def _run(
        self,
        name: str,
        tool: str | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        try:
            output = asyncio.run(asyncio.wait_for(self._invoke(name, tool, arguments), timeout=45))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"MCP request failed ({type(exc).__name__})") from exc
        if len(output.encode("utf-8")) > 64 * 1024:
            raise ValueError("MCP response exceeds 64 KiB")
        return output

    def list_tools(self, arguments: dict[str, Any]) -> str:
        return self._run(arguments["server"])

    def call_tool(self, arguments: dict[str, Any]) -> str:
        try:
            parsed = json.loads(arguments["arguments_json"])
        except json.JSONDecodeError as exc:
            raise ValueError("arguments_json must contain a JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("arguments_json must contain a JSON object")
        return self._run(arguments["server"], arguments["tool"], parsed)


def mcp_tools(bridge: MCPBridge) -> tuple[Tool, Tool]:
    return (
        Tool(
            "mcp_list",
            "List tools from an explicitly configured MCP server",
            "mcp_execute",
            {"server": {"type": "string"}},
            bridge.list_tools,
        ),
        Tool(
            "mcp_call",
            "Call a configured MCP tool through the Doppel gateway; inspect its schema first",
            "mcp_execute",
            {
                "server": {"type": "string"},
                "tool": {"type": "string"},
                "arguments_json": {"type": "string"},
            },
            bridge.call_tool,
        ),
    )
