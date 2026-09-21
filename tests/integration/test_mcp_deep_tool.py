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
from doppel_agent.mcp.tool_adapter import langchain_mcp_tools
from doppel_agent.permissions import PermissionManager
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.runtime.base import ResumeCommand, RunRequest
from doppel_agent.runtime.deep import DeepAgentRuntime


class Provider:
    def __init__(self):
        self.turn = 0

    def next_turn(self, messages, tools):
        self.turn += 1
        if self.turn == 1:
            return ModelTurn(tool_calls=(ToolCall("original-call", "mcp__demo__echo", {"text": "deep"}),))
        return ModelTurn(content="deep-verified:" + messages[-1].content)


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


class MCPDeepIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_deep_tool_uses_gateway_and_hitl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = MCPConfig(
                {"demo": MCPServerConfig("demo", "stdio", command="C:/Python/python.exe")},
                root / ".doppel" / "mcp.json",
            )
            session = Session()

            @asynccontextmanager
            async def connector(server):
                yield session, SimpleNamespace(
                    protocol_version="2025-06-18",
                    server_info=SimpleNamespace(name="fake", version="1"),
                    capabilities={},
                )

            manager = MCPClientManager(config, connector=connector)
            catalog = MCPToolCatalog(manager)
            descriptors = await catalog.list_all()
            executor = MCPToolExecutor(
                manager,
                catalog,
                PermissionManager(frozenset({"mcp_execute"})),
                ledger=ToolExecutionLedger(root / "mcp-ledger.sqlite3"),
            )
            runtime = DeepAgentRuntime(root, Provider(), max_subagents=0)
            runtime.additional_tools = langchain_mcp_tools(descriptors, executor)
            request = RunRequest("echo deeply", run_id="b" * 32, thread_id="deep-thread")
            interrupted = await runtime.run(request)
            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(session.calls, 0)
            completed = await runtime.resume(
                ResumeCommand(request.run_id, request.thread_id, {"action": "approve"})
            )
            self.assertEqual(completed.status, "completed")
            self.assertIn("deep-verified:deep", completed.answer)
            self.assertEqual(session.calls, 1)
            await manager.close()
