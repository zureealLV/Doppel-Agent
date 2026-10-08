import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

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

    async def test_reconnect_invalidates_schema_cache_even_when_server_version_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            sessions = [PagedSession(), PagedSession()]
            sessions[1].version = 2
            opened = 0

            @asynccontextmanager
            async def rotating_connector(server):
                nonlocal opened
                session = sessions[opened]
                opened += 1
                yield session, SimpleNamespace(
                    protocol_version="2025-06-18",
                    server_info=SimpleNamespace(name="fake", version="1.0"),
                    capabilities={},
                )

            manager = MCPClientManager(
                config(Path(directory)), connector=rotating_connector
            )
            catalog = MCPToolCatalog(manager)
            first = await catalog.list_server("demo")
            await manager.invalidate("demo")
            second = await catalog.list_server("demo")

            self.assertEqual(opened, 2)
            self.assertNotEqual(first[1].schema_hash, second[1].schema_hash)
            await manager.close()


def test_mcp_catalog_repeated_cursor_is_bounded(tmp_path):
    import asyncio
    import pytest
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from mcp import types
    from doppel_agent.mcp.catalog import MCPToolCatalog
    from doppel_agent.mcp.client_manager import MCPClientManager
    from mcp_support import config
    calls=[]
    class Session:
        async def list_tools(self, *, params=None):
            calls.append(getattr(params,'cursor',None))
            # Yield so the test watchdog can stop the old unbounded collector.
            await asyncio.sleep(0)
            return types.ListToolsResult(tools=[],nextCursor='repeated')
    @asynccontextmanager
    async def connector(server):
        yield Session(),SimpleNamespace(protocol_version='2025-06-18',server_info=None,capabilities={})
    async def scenario():
        manager=MCPClientManager(config(tmp_path),connector=connector)
        catalog=MCPToolCatalog(manager)
        try:
            with pytest.raises(ValueError,match='pagination'):
                await asyncio.wait_for(catalog.list_server('demo'),1)
            assert len(calls)==4  # two bounded discovery attempts, two pages each
        finally:
            await manager.close()
    asyncio.run(scenario())
