import tempfile
import unittest
from pathlib import Path

from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.permissions import PermissionManager
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger

from mcp_support import config, connector_for


class ExecuteSession:
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.raise_transport = False

    async def list_tools(self, *, params=None):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="inspect",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "api_token": {"type": "string"},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                )
            ]
        )

    async def call_tool(self, name, arguments=None):
        self.calls += 1
        if self.raise_transport:
            raise ConnectionError("disconnected after send")
        if self.fail:
            return types.CallToolResult(content=[types.TextContent(text="remote failure")], isError=True)
        return types.CallToolResult(
            content=[
                types.TextContent(text=f"found:{arguments['query']}"),
                types.ImageContent(data="YWJj", mimeType="image/png"),
                types.ResourceLink(name="evidence", uri="https://example.com/evidence"),
            ],
            structuredContent={"count": 1},
        )


class MCPToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.session = ExecuteSession()
        self.manager = MCPClientManager(config(self.root), connector=connector_for(self.session))
        self.catalog = MCPToolCatalog(self.manager)
        self.executor = MCPToolExecutor(
            self.manager,
            self.catalog,
            PermissionManager(frozenset({"mcp_execute"})),
            ledger=ToolExecutionLedger(self.root / "ledger.sqlite3"),
        )

    async def asyncTearDown(self):
        await self.manager.close()
        self.temp.cleanup()

    async def test_normalizes_multimodal_content_and_replays_idempotently(self):
        first = await self.executor.execute(
            "mcp__demo__inspect", {"query": "needle"}, run_id="run", tool_call_id="call"
        )
        second = await self.executor.execute(
            "mcp__demo__inspect", {"query": "needle"}, run_id="run", tool_call_id="call"
        )
        self.assertTrue(first.success)
        self.assertIn("[image: image/png", first.model_view)
        self.assertEqual(first.application_view["structured_content"], {"count": 1})
        self.assertTrue(second.replayed)
        self.assertEqual(self.session.calls, 1)

    async def test_checks_is_error_and_validates_schema_before_call(self):
        with self.assertRaises(ValueError):
            await self.executor.execute(
                "mcp__demo__inspect", {"query": 3}, run_id="r2", tool_call_id="bad"
            )
        self.assertEqual(self.session.calls, 0)
        self.session.fail = True
        result = await self.executor.execute(
            "mcp__demo__inspect", {"query": "x"}, run_id="r2", tool_call_id="failed"
        )
        self.assertFalse(result.success)
        self.assertIn("remote failure", result.error)

    async def test_audit_redacts_secret_shaped_arguments(self):
        events = []
        executor = MCPToolExecutor(
            self.manager,
            self.catalog,
            PermissionManager(frozenset({"mcp_execute"})),
            audit=lambda payload: events.append(payload),
        )
        await executor.execute(
            "mcp__demo__inspect",
            {"query": "safe", "api_token": "do-not-persist"},
            run_id="audit",
            tool_call_id="one",
        )
        self.assertEqual(
            events[0]["arguments"],
            {"query": "safe", "api_token": "<redacted>"},
        )

    async def test_ambiguous_tool_failure_is_not_retried(self):
        self.session.raise_transport = True
        executor = MCPToolExecutor(
            self.manager,
            self.catalog,
            PermissionManager(frozenset({"mcp_execute"})),
        )
        with self.assertRaises(ConnectionError):
            await executor.execute(
                "mcp__demo__inspect",
                {"query": "side effect"},
                run_id="ambiguous",
                tool_call_id="once",
            )
        self.assertEqual(self.session.calls, 1)
