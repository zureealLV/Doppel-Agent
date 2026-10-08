"""C2c2g3 FIRST original pre-ready/late-result/executor definitions, ALL UNRUN.

Disposable SDK modules only; exercise the actual built-in connector without
network/environment/auth access. Recorded exits are source-level fixture
observations, not SDK/native process/port/outer-owner drain evidence.
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import pytest

from doppel_agent.mcp.client_manager import MCPClientManager, MCPCleanupError
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig


def config(path, *, second=False):
    servers = {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")}
    if second:
        servers["second"] = MCPServerConfig(
            "second", "streamable_http", url="http://127.0.0.1/offline-second"
        )
    return MCPConfig(servers, path / "offline-config.json")


class OfflineSDK:
    def __init__(self, monkeypatch, mode):
        self.mode = mode
        self.events = []
        self.tasks = []
        fixture = self

        class Resource:
            def __init__(self, kind):
                self.kind = kind

            async def __aenter__(self):
                fixture.events.append(self.kind + "_enter")
                fixture.tasks.append(asyncio.current_task())
                if fixture.mode == self.kind + "_enter_fail":
                    raise ValueError("PRIVATE_OPAQUE_ENTER")
                return (object(), object(), None) if self.kind == "transport" else self

            async def initialize(self):
                fixture.events.append("initialize")
                if fixture.mode in {"initialize_fail", "session_exit_fail", "session_exit_suppressed"}:
                    raise ValueError("offline initialization failed")
                return SimpleNamespace(capabilities={})

            async def __aexit__(self, *_exc):
                fixture.events.append(self.kind + "_exit")
                fixture.tasks.append(asyncio.current_task())
                if (
                    fixture.mode == self.kind + "_exit_fail"
                    or self.kind == "session"
                    and fixture.mode in {"session_exit_suppressed", "healthy_exit_suppressed"}
                ):
                    raise OSError("PRIVATE_SDK_EXIT")
                if self.kind == "transport" and fixture.mode in {
                    "session_exit_suppressed",
                    "healthy_exit_suppressed",
                }:
                    return True  # Original later SDK exit suppresses earlier exit failure.
                return False

        def forbidden(*_args, **_kwargs):
            raise AssertionError("offline HTTP fixture must not enter stdio/environment")

        def resource(kind):
            if fixture.mode == kind + "_factory_fail":
                fixture.events.append(kind + "_factory")
                fixture.tasks.append(asyncio.current_task())
                raise ValueError("PRIVATE_OPAQUE_FACTORY")
            return Resource(kind)

        root = ModuleType("mcp")
        root.__path__ = []
        client = ModuleType("mcp.client")
        client.__path__ = []
        stdio = ModuleType("mcp.client.stdio")
        http = ModuleType("mcp.client.streamable_http")
        root.ClientSession = lambda *_args: resource("session")
        stdio.StdioServerParameters = forbidden
        stdio.get_default_environment = forbidden
        stdio.stdio_client = forbidden
        http.streamable_http_client = lambda *_args, **_kwargs: resource("transport")
        root.client = client
        client.stdio = stdio
        client.streamable_http = http
        for name, module in (
            (root.__name__, root),
            (client.__name__, client),
            (stdio.__name__, stdio),
            (http.__name__, http),
        ):
            monkeypatch.setitem(sys.modules, name, module)


def test_opaque_pre_ready_error_retains_same_owner_even_when_owner_task_returns(tmp_path):
    async def scenario():
        entered = []
        faults = []

        @asynccontextmanager
        async def connector(_server):
            entered.append(asyncio.current_task())
            raise ValueError("PRIVATE_OPAQUE_STARTUP")
            yield  # Unreturned enter cannot self-report absence of resources.

        manager = MCPClientManager(
            config(tmp_path), connector=connector, failure=lambda: faults.append("original fault")
        )
        with pytest.raises(MCPCleanupError, match="^mcp_cleanup_unresolved$"):
            await manager.get("demo")
        original = manager._connections["demo"]
        assert original.task is entered[0] and original.task.done() and original.task.exception() is None
        assert not original.lifetime.known_closed and manager.cleanup_failed and faults
        assert manager.generation("demo") == 0 and manager._clients == {}
        with pytest.raises(MCPCleanupError):
            await manager.get("demo")
        with pytest.raises(MCPCleanupError):
            await manager.close()
        closing = manager._close_task
        with pytest.raises(MCPCleanupError):
            await manager.close()
        assert (
            manager._connections["demo"] is original and manager._close_task is closing and len(entered) == 1
        )

    asyncio.run(scenario())


def test_actual_builtin_known_sdk_exit_preserves_initialization_error_and_healthy_retry(
    tmp_path, monkeypatch
):
    sdk = OfflineSDK(monkeypatch, "initialize_fail")

    async def scenario():
        faults = []
        manager = MCPClientManager(config(tmp_path), failure=lambda: faults.append("forbidden"))
        with pytest.raises(ValueError, match="^offline initialization failed$"):
            await manager.get("demo")
        assert sdk.events == [
            "transport_enter",
            "session_enter",
            "initialize",
            "session_exit",
            "transport_exit",
        ]
        assert len(set(sdk.tasks)) == 1 and sdk.tasks[0].done()
        assert manager._connections == {} and not manager.cleanup_failed and not faults
        sdk.mode = "healthy"
        await manager.get("demo")
        original = manager._connections["demo"]
        assert manager.generation("demo") == 1 and original.task is not sdk.tasks[0]
        await manager.close()
        assert original.lifetime.known_closed and manager._connections == {} and not manager.cleanup_failed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode,events",
    [
        ("transport_factory_fail", ["transport_factory"]),
        ("transport_enter_fail", ["transport_enter"]),
        ("session_factory_fail", ["transport_enter", "session_factory", "transport_exit"]),
        ("session_enter_fail", ["transport_enter", "session_enter", "transport_exit"]),
        (
            "session_exit_fail",
            ["transport_enter", "session_enter", "initialize", "session_exit", "transport_exit"],
        ),
        (
            "session_exit_suppressed",
            ["transport_enter", "session_enter", "initialize", "session_exit", "transport_exit"],
        ),
    ],
)
def test_actual_builtin_unreturned_enter_or_failed_sdk_exit_is_unknown_not_initialization_error(
    tmp_path, monkeypatch, mode, events
):
    sdk = OfflineSDK(monkeypatch, mode)

    async def scenario():
        faults = []
        manager = MCPClientManager(config(tmp_path), failure=lambda: faults.append("original fault"))
        with pytest.raises(MCPCleanupError, match="^mcp_cleanup_unresolved$"):
            await manager.get("demo")
        original = manager._connections["demo"]
        assert original.task is sdk.tasks[0] and original.task.done() and sdk.events == events
        assert (
            len(set(sdk.tasks)) == 1
            and not original.lifetime.known_closed
            and faults
            and manager.cleanup_failed
        )
        before = list(sdk.events)
        with pytest.raises(MCPCleanupError):
            await manager.get("demo")
        with pytest.raises(MCPCleanupError):
            await manager.close()
        assert manager._connections["demo"] is original and sdk.events == before

    asyncio.run(scenario())


def test_actual_builtin_later_sdk_suppression_cannot_clear_original_exit_failure(tmp_path, monkeypatch):
    sdk = OfflineSDK(monkeypatch, "healthy_exit_suppressed")

    async def scenario():
        faults = []
        manager = MCPClientManager(config(tmp_path), failure=lambda: faults.append("original fault"))
        await manager.get("demo")
        original = manager._connections["demo"]
        with pytest.raises(MCPCleanupError, match="^mcp_cleanup_unresolved$"):
            await manager.close()
        assert manager.cleanup_failed and faults and manager._connections["demo"] is original
        assert original.lifetime.sdk_exit_failed and not original.lifetime.known_closed
        assert sdk.events == [
            "transport_enter",
            "session_enter",
            "initialize",
            "session_exit",
            "transport_exit",
        ]
        assert len(set(sdk.tasks)) == 1 and sdk.tasks[0] is original.task

    asyncio.run(scenario())


def test_original_entered_call_cannot_publish_late_success_after_another_exact_owner_failed_exit(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        opened = []
        closed = []

        @asynccontextmanager
        async def connector(server):
            opened.append(server.name)
            try:
                yield object(), SimpleNamespace(capabilities={})
            finally:
                closed.append(server.name)
                if server.name == "second":
                    raise OSError("PRIVATE_OTHER_EXIT")

        manager = MCPClientManager(config(tmp_path, second=True), connector=connector)
        await manager.get("demo")
        await manager.get("second")
        original = manager._connections["demo"]
        failed = manager._connections["second"]

        async def operation(_session):
            calls.append("entered")
            entered.set()
            await release.wait()
            calls.append("returned")
            return "raw late success must not publish"

        running = asyncio.create_task(manager.call("demo", operation))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(MCPCleanupError):
                await manager.invalidate("second")
            assert not running.done() and manager._connections["demo"] is original
            assert manager._connections["second"] is failed and closed == ["second"]
        finally:
            release.set()
        with pytest.raises(MCPCleanupError):
            await asyncio.wait_for(running, 2)
        assert calls == ["entered", "returned"] and opened == ["demo", "second"]
        assert manager.generation("demo") == 1
        with pytest.raises(MCPCleanupError):
            await manager.close()
        assert manager._connections["second"] is failed and closed == ["second", "demo"]

    asyncio.run(scenario())


@pytest.mark.parametrize("use_ledger", [False, True])
def test_actual_executor_preserves_exact_cleanup_error_without_normalization_or_success_audit(
    tmp_path, monkeypatch, use_ledger
):
    from doppel_agent.mcp.executor import MCPToolExecutor
    from doppel_agent.mcp.types import MCPToolDescriptor
    from doppel_agent.persistence.tool_ledger import ToolExecutionLedger

    async def scenario():
        calls = []
        exits = []
        audits = []

        class Session:
            async def call_tool(self, *_args, **_kwargs):
                calls.append("original invoke")
                raise ConnectionError("PRIVATE_TRANSPORT")

        @asynccontextmanager
        async def connector(_server):
            try:
                yield Session(), SimpleNamespace(capabilities={})
            finally:
                exits.append("original exit")
                raise OSError("PRIVATE_CLOSE")

        descriptor = MCPToolDescriptor("mcp__demo__write", "demo", "write", "Offline", "", {"type": "object"})

        class Catalog:
            async def get(self, _name):
                return descriptor

            def source_for_descriptor(self, _descriptor):
                return None  # Synthetic catalog only; original cleanup gate, NOT discovery binding proof.

        permissions = SimpleNamespace(check=lambda *_args: SimpleNamespace(allowed=True))
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3") if use_ledger else None
        manager = MCPClientManager(config(tmp_path), connector=connector)
        executor = MCPToolExecutor(manager, Catalog(), permissions, ledger=ledger, audit=audits.append)

        def forbidden(*_args):
            raise AssertionError("cleanup unknown must never normalize success")

        monkeypatch.setattr(executor, "_normalize", forbidden)
        with pytest.raises(MCPCleanupError, match="^mcp_cleanup_unresolved$"):
            await executor.execute(descriptor.logical_name, {}, run_id="offline", tool_call_id="one")
        original = manager._connections["demo"]
        assert (
            manager.cleanup_failed
            and calls == ["original invoke"]
            and exits == ["original exit"]
            and audits == []
        )
        if ledger is not None:
            with ledger._connect() as connection:
                row = connection.execute("SELECT status,result,error FROM tool_executions").fetchone()
                assert row["status"] == "failed" and row["result"] == ""
                assert row["error"] == "MCPCleanupError: tool execution failed"
        with pytest.raises(MCPCleanupError):
            await manager.close()
        assert manager._connections["demo"] is original and exits == ["original exit"]

    asyncio.run(scenario())
