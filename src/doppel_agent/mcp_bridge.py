"""Explicitly configured stdio MCP servers, using the official Python SDK."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .tools import Tool


class MCPBridge:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        self.config_path = self.workspace / ".doppel" / "mcp.json"
        self.servers = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.config_path.exists():
            return {}
        if (self.config_path.is_symlink() or not self.config_path.resolve(strict=True).is_relative_to(self.workspace)
                or self.config_path.stat().st_size > 64 * 1024):
            raise ValueError("invalid MCP configuration file")
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"servers"} or not isinstance(payload["servers"], dict):
            raise ValueError("MCP configuration must contain a servers object")
        if len(payload["servers"]) > 16:
            raise ValueError("too many MCP servers")
        for name, config in payload["servers"].items():
            if not isinstance(name, str) or not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name):
                raise ValueError("invalid MCP server name")
            if not isinstance(config, dict) or set(config) - {"command", "args", "env_names"}:
                raise ValueError(f"invalid MCP server config: {name}")
            command = config.get("command")
            args = config.get("args", [])
            env_names = config.get("env_names", [])
            if not isinstance(command, str) or not Path(command).is_absolute() or not Path(command).is_file():
                raise ValueError(f"MCP command must be an existing absolute executable: {name}")
            if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
                raise ValueError(f"MCP args must be strings: {name}")
            if not isinstance(env_names, list) or not all(isinstance(x, str) and x.isidentifier() for x in env_names):
                raise ValueError(f"MCP env_names must be variable names: {name}")
        return payload["servers"]

    def approval_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        context = dict(arguments)
        config = self.servers.get(arguments.get("server"))
        if config:
            context["configured_command"] = config["command"]
            context["configured_args"] = config.get("args", [])
            context["forwarded_env_names"] = config.get("env_names", [])
        return context

    def _parameters(self, name: str):
        config = self.servers.get(name)
        if config is None:
            raise ValueError(f"unknown MCP server: {name}")
        try:
            from mcp import StdioServerParameters
        except ImportError as exc:
            raise RuntimeError('MCP support requires: pip install "doppel-agent[mcp]"') from exc
        env_names = config.get("env_names", [])
        missing = [key for key in env_names if key not in os.environ]
        if missing:
            raise ValueError(f"missing MCP environment variables: {', '.join(missing)}")
        return StdioServerParameters(
            command=config["command"], args=config.get("args", []),
            env={key: os.environ[key] for key in env_names}, cwd=str(self.workspace),
        )

    def _run(self, name: str, tool: str | None = None, arguments: dict[str, Any] | None = None) -> str:
        params = self._parameters(name)

        async def invoke() -> str:
            from mcp import Client, stdio_client

            async with Client(stdio_client(params), read_timeout_seconds=30) as client:
                if tool is None:
                    result = await client.list_tools()
                    data = [
                        {"name": item.name, "description": item.description, "input_schema": item.input_schema}
                        for item in result.tools
                    ]
                    return json.dumps(data, ensure_ascii=False)
                listed = await client.list_tools()
                if tool not in {item.name for item in listed.tools}:
                    raise ValueError(f"unknown MCP tool: {tool}")
                result = await client.call_tool(tool, arguments or {})
                content = [item.text for item in result.content if getattr(item, "type", None) == "text"]
                data = {"is_error": bool(result.is_error), "content": content, "structured_content": result.structured_content}
                return json.dumps(data, ensure_ascii=False)

        try:
            output = asyncio.run(asyncio.wait_for(invoke(), timeout=45))
        except ValueError:
            raise
        except Exception as exc:
            # MCP/anyio may raise an ExceptionGroup. Keep it inside the
            # recoverable tool-error boundary without leaking server output.
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
        Tool("mcp_list", "List tools from an explicitly configured MCP server", "mcp_execute", {"server": {"type": "string"}}, bridge.list_tools),
        Tool(
            "mcp_call", "Call a tool on an explicitly configured MCP server; inspect its schema first", "mcp_execute",
            {"server": {"type": "string"}, "tool": {"type": "string"}, "arguments_json": {"type": "string"}}, bridge.call_tool,
        ),
    )
