import tempfile
import unittest
from pathlib import Path

from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager

from mcp_support import config, connector_for


class PagedSession:
    def __init__(self):
        self.version = 1
        self.calls = []

    async def list_tools(self, *, params=None):
        cursor = getattr(params, "cursor", None)
        self.calls.append(cursor)
        if cursor is None:
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="lookup",
                        description="Untrusted remote description",
                        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                    )
                ],
                nextCursor="page-2",
            )
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="calculate",
                    title="Calculator",
                    inputSchema={
                        "type": "object",
                        "properties": {"value": {"type": "integer" if self.version == 1 else "number"}},
                        "required": ["value"],
                    },
                )
            ]
        )


class MCPToolCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_paginates_normalizes_and_invalidates_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            session = PagedSession()
            manager = MCPClientManager(config(Path(directory)), connector=connector_for(session))
            catalog = MCPToolCatalog(manager)
            first = await catalog.list_server("demo")
            self.assertEqual(session.calls, [None, "page-2"])
            self.assertEqual([tool.logical_name for tool in first], ["mcp__demo__lookup", "mcp__demo__calculate"])
            self.assertEqual(first[0].title, "lookup")
            session.version = 2
            second = await catalog.list_server("demo", refresh=True)
            self.assertNotEqual(first[1].schema_hash, second[1].schema_hash)
            await manager.close()
