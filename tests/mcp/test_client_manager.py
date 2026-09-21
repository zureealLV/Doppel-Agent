import tempfile
import unittest
import asyncio
from pathlib import Path

from mcp import types

from doppel_agent.mcp.client_manager import MCPClientManager

from mcp_support import config, connector_for


class FakeSession:
    def __init__(self):
        self.probes = 0

    async def list_tools(self, *, params=None):
        self.probes += 1
        return types.ListToolsResult(tools=[])


class MCPClientManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_session_health_checks_and_closes_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle = []
            session = FakeSession()
            manager = MCPClientManager(
                config(Path(directory)), connector=connector_for(session, lifecycle)
            )
            first = await manager.get("demo")
            second = await manager.get("demo")
            self.assertIs(first, second)
            metadata = await manager.health("demo")
            self.assertEqual(metadata.protocol_version, "2025-06-18")
            self.assertEqual(session.probes, 1)
            await manager.close()
            self.assertEqual(lifecycle, [("open", "demo"), ("close", "demo")])

    async def test_invalidated_session_is_reconnected(self):
        with tempfile.TemporaryDirectory() as directory:
            lifecycle = []
            manager = MCPClientManager(
                config(Path(directory)), connector=connector_for(FakeSession(), lifecycle)
            )
            await manager.get("demo")
            await manager.invalidate("demo")
            await manager.get("demo")
            await manager.close()
            self.assertEqual([event[0] for event in lifecycle], ["open", "close", "open", "close"])

    async def test_per_server_semaphore_bounds_concurrent_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = MCPClientManager(
                config(Path(directory), maximum=2), connector=connector_for(FakeSession())
            )
            active = 0
            peak = 0

            async def operation(session):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1

            await asyncio.gather(*(manager.call("demo", operation) for _ in range(8)))
            self.assertEqual(peak, 2)
            await manager.close()
