import tempfile
import unittest
from pathlib import Path

from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.mcp.tool_adapter import doppel_mcp_tools, langchain_mcp_tools
from doppel_agent.permissions import PermissionManager

from mcp_support import config, connector_for


class Session:
    async def list_tools(self, *, params=None):
        return types.ListToolsResult(
            tools=[types.Tool(name="echo", description="echo", inputSchema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False})]
        )

    async def call_tool(self, name, arguments=None):
        return types.CallToolResult(content=[types.TextContent(text=arguments["text"])])


class MCPToolAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_langchain_and_focused_graph_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPClientManager(config(Path(directory)), connector=connector_for(Session()))
            catalog = MCPToolCatalog(manager)
            descriptors = await catalog.list_all()
            executor = MCPToolExecutor(
                manager, catalog, PermissionManager(frozenset({"mcp_execute"}))
            )
            langchain = langchain_mcp_tools(descriptors, executor)
            focused = doppel_mcp_tools(descriptors, executor)
            self.assertEqual(langchain[0].name, "mcp__demo__echo")
            self.assertEqual(focused[0].capability, "mcp_execute")
            self.assertIn("Untrusted MCP", focused[0].description)
            await manager.close()
