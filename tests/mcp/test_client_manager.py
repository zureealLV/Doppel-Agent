import tempfile
import unittest
import asyncio
from pathlib import Path

from mcp import types

from doppel_agent.mcp.client_manager import MCPClientManager

from mcp_support import config, connector_for
from contextlib import asynccontextmanager
from types import SimpleNamespace


class FakeSession:
    def __init__(self):
        self.probes = 0

    async def list_tools(self, *, params=None):
        self.probes += 1
        return types.ListToolsResult(tools=[])


class MCPClientManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_connector_close_failure_does_not_abandon_other_owners(self):
        from dataclasses import replace
        from doppel_agent.mcp.config import MCPConfig

        with tempfile.TemporaryDirectory() as directory:
            closed = []

            @asynccontextmanager
            async def connector(server):
                try:
                    yield FakeSession(), SimpleNamespace()
                finally:
                    closed.append(server.name)
                    if server.name == "demo":
                        raise ValueError("close fixture failed")

            original = config(Path(directory))
            servers = {**original.servers, "second": replace(original.servers["demo"], name="second")}
            manager = MCPClientManager(MCPConfig(servers, original.source), connector=connector)
            await manager.get("demo")
            await manager.get("second")
            with self.assertRaisesRegex(ExceptionGroup, "MCP connector shutdown failed"):
                await manager.close()
            self.assertEqual(closed, ["demo", "second"])
            self.assertEqual(manager._connections, {})

    async def test_cancelled_close_drains_all_server_owners(self):
        from dataclasses import replace
        from doppel_agent.mcp.config import MCPConfig

        with tempfile.TemporaryDirectory() as directory:
            first_closing, allow_close = asyncio.Event(), asyncio.Event()
            closed = []

            @asynccontextmanager
            async def connector(server):
                try:
                    yield FakeSession(), SimpleNamespace()
                finally:
                    if server.name == "demo":
                        first_closing.set()
                        await allow_close.wait()
                    closed.append(server.name)

            original = config(Path(directory))
            servers = {**original.servers, "second": replace(original.servers["demo"], name="second")}
            manager = MCPClientManager(MCPConfig(servers, original.source), connector=connector)
            await manager.get("demo")
            await manager.get("second")
            closing = asyncio.create_task(manager.close())
            await asyncio.wait_for(first_closing.wait(), 2)
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            allow_close.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(closing, 2)
            self.assertEqual(closed, ["demo", "second"])
            self.assertEqual(manager._connections, {})
            await manager.close()

    async def test_connector_enters_and_exits_in_same_owner_across_callers(self):
        with tempfile.TemporaryDirectory() as directory:
            owners = []

            @asynccontextmanager
            async def connector(server):
                owners.append(asyncio.current_task())
                try:
                    yield FakeSession(), SimpleNamespace()
                finally:
                    self.assertIs(asyncio.current_task(), owners[0])
                    owners.append(asyncio.current_task())

            manager = MCPClientManager(config(Path(directory)), connector=connector)
            first = await asyncio.create_task(manager.get("demo"))
            self.assertIs(await manager.get("demo"), first)
            await asyncio.create_task(manager.close())
            self.assertEqual(len(owners), 2)
            self.assertTrue(owners[0].done())

    async def test_cancelled_startup_drains_owner_and_preserves_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, allow_open, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

            @asynccontextmanager
            async def connector(server):
                entered.set()
                await allow_open.wait()
                try:
                    yield FakeSession(), SimpleNamespace()
                finally:
                    closed.set()

            manager = MCPClientManager(config(Path(directory)), connector=connector)
            opening = asyncio.create_task(manager.get("demo"))
            await asyncio.wait_for(entered.wait(), 2)
            opening.cancel()
            await asyncio.sleep(0)
            opening.cancel()
            self.assertFalse(opening.done())
            allow_open.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(opening, 2)
            self.assertTrue(closed.is_set())
            self.assertEqual(manager._connections, {})
            await manager.close()

    async def test_startup_error_is_not_cached_and_can_reconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            attempts = 0

            @asynccontextmanager
            async def connector(server):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise ValueError("connector fixture failed")
                yield FakeSession(), SimpleNamespace()

            manager = MCPClientManager(config(Path(directory)), connector=connector)
            with self.assertRaisesRegex(ValueError, "connector fixture failed"):
                await manager.get("demo")
            self.assertEqual(manager._connections, {})
            await manager.get("demo")
            self.assertEqual(manager.generation("demo"), 1)
            await manager.close()

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
