import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.mcp.tool_adapter import doppel_mcp_tools
from doppel_agent.permissions import PermissionManager
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.base import ResumeCommand, RunRequest
from doppel_agent.runtime.graph import GraphRuntime
from doppel_agent.tools import ToolRegistry



def config(root):
    return MCPConfig(
        {"demo": MCPServerConfig("demo", "stdio", command="C:/Python/python.exe")},
        root / ".doppel" / "mcp.json",
    )


def connector_for(session):
    @asynccontextmanager
    async def connector(server):
        yield session, SimpleNamespace(
            protocol_version="2025-06-18",
            server_info=SimpleNamespace(name="fake", version="1"),
            capabilities={},
        )

    return connector


class Provider:
    def __init__(self):
        self.turn = 0

    def next_turn(self, messages, tools):
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(tool_calls=(ToolCall("mcp-call-1", "mcp__demo__echo", {"text": "hi"}),))
        return ModelTurn(content="verified:" + messages[-1].content)


class Session:
    def __init__(self):
        self.calls = 0

    async def list_tools(self, *, params=None):
        return types.ListToolsResult(
            tools=[types.Tool(name="echo", inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False})]
        )

    async def call_tool(self, name, arguments=None):
        self.calls += 1
        return types.CallToolResult(content=[types.TextContent(text=arguments["text"])])


class MCPGraphIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensitive_mcp_tool_interrupts_then_executes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session()
            manager = MCPClientManager(config(root), connector=connector_for(session))
            catalog = MCPToolCatalog(manager)
            descriptors = await catalog.list_all()
            permissions = PermissionManager(frozenset({"workspace_read", "mcp_execute"}))
            executor = MCPToolExecutor(manager, catalog, permissions)
            registry = ToolRegistry(permissions)
            for tool in doppel_mcp_tools(descriptors, executor):
                registry.register(tool)
            runtime = GraphRuntime(root, Provider(), tools=registry)
            request = RunRequest("echo", run_id="a" * 32, thread_id="thread")
            interrupted = await runtime.run(request)
            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(session.calls, 0)
            completed = await runtime.resume(
                ResumeCommand(request.run_id, request.thread_id, {"action": "approve"})
            )
            self.assertEqual(completed.status, "completed")
            self.assertIn("verified:hi", completed.answer)
            self.assertEqual(session.calls, 1)
            await manager.close()
