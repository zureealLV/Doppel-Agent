"""C2c2g FIRST original manager/bridge/Core cleanup definitions, ALL UNRUN."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from doppel_agent.mcp.client_manager import MCPClientManager, MCPCleanupError
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
from doppel_agent.mcp_bridge import MCPBridge
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


def config(path):
    return MCPConfig(
        {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")},
        path / "offline-config.json",
    )


def test_original_failed_exit_retains_same_connection_and_blocks_reconnect_and_new_call(tmp_path):
    async def scenario():
        opened, exited, faults = [], [], []

        @asynccontextmanager
        async def connector(server):
            opened.append(asyncio.current_task())
            try:
                yield object(), SimpleNamespace(capabilities={})
            finally:
                exited.append(asyncio.current_task())
                raise ValueError("PRIVATE_EXIT_OR_AUTH")

        manager = MCPClientManager(
            config(tmp_path), connector=connector, failure=lambda: faults.append("same fault")
        )
        await manager.get("demo")
        original = manager._connections["demo"]
        with pytest.raises(MCPCleanupError, match="^mcp_cleanup_unresolved$"):
            await manager.invalidate("demo")
        assert manager.cleanup_failed and manager._connections["demo"] is original
        assert opened == exited and len(opened) == 1 and faults
        with pytest.raises(MCPCleanupError):
            await manager.get("demo")
        calls = []

        async def operation(_session):
            calls.append("forbidden")

        with pytest.raises(MCPCleanupError):
            await manager.call("demo", operation)
        assert calls == [] and len(opened) == 1
        with pytest.raises(MCPCleanupError):
            await manager.close()
        closing = manager._close_task
        with pytest.raises(MCPCleanupError):
            await manager.close()
        assert manager._close_task is closing and manager._connections["demo"] is original
        assert len(exited) == 1

    asyncio.run(scenario())


def test_original_repeated_cancel_after_known_exit_preserves_cancel_not_cleanup_failure(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        closes = []

        @asynccontextmanager
        async def connector(server):
            try:
                yield object(), SimpleNamespace(capabilities={})
            finally:
                entered.set()
                await release.wait()
                closes.append("original exited")

        manager = MCPClientManager(config(tmp_path), connector=connector)
        await manager.get("demo")
        original = manager._connections["demo"]
        discard = asyncio.create_task(manager.invalidate("demo"))
        try:
            await entered.wait()
            discard.cancel()
            await asyncio.sleep(0)
            discard.cancel()
            assert not discard.done() and manager._connections["demo"] is original
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await discard
        assert closes == ["original exited"] and not manager.cleanup_failed and manager._connections == {}
        await manager.close()

    asyncio.run(scenario())


def test_original_latched_cleanup_unknown_cannot_be_cleared_by_empty_close_or_second_observation(tmp_path):
    async def scenario():
        manager = MCPClientManager(config(tmp_path))
        manager._mark_cleanup_failed()  # Original failure latch fixture; no SDK call.
        with pytest.raises(MCPCleanupError):
            await manager.close()
        original = manager._close_task
        with pytest.raises(MCPCleanupError):
            await manager.close()
        assert manager.cleanup_failed and manager._close_task is original and original.done()
        with pytest.raises(MCPCleanupError):
            await manager.get("demo")

    asyncio.run(scenario())


def failing_bridge(tmp_path, monkeypatch, failure=None):
    import doppel_agent.mcp_bridge as module

    instances = []

    class Manager:
        def __init__(self, *_args, **_kwargs):
            instances.append(self)

        async def close(self):
            raise ValueError("PRIVATE_CLOSE_OR_KEY")

    class Catalog:
        def __init__(self, manager):
            self.manager = manager

        async def list_server(self, name):
            return []

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))
    monkeypatch.setattr(module, "MCPClientManager", Manager)
    monkeypatch.setattr(module, "MCPToolCatalog", Catalog)
    return MCPBridge(tmp_path, failure=failure), instances


def test_original_bridge_exit_error_not_ordinary_value_error_and_no_second_manager(tmp_path, monkeypatch):
    fault = ProviderReceiptFault()
    bridge, managers = failing_bridge(tmp_path, monkeypatch, fault.mark_failed)
    with pytest.raises(MCPCleanupError) as failure:
        bridge.list_tools({"server": "demo"})
    assert str(failure.value) == "mcp_cleanup_unresolved" and fault.broken and bridge.cleanup_failed
    assert len(managers) == 1 and bridge._manager is managers[0]
    with pytest.raises(MCPCleanupError):
        bridge.list_tools({"server": "demo"})
    assert len(managers) == 1


def test_actual_original_core_cleanup_failure_cannot_become_completed_tool_error(tmp_path, monkeypatch):
    from doppel_agent.core import Core
    from doppel_agent.provider import ModelTurn, ToolCall

    fault = ProviderReceiptFault()
    failing_bridge(tmp_path, monkeypatch)
    calls = []

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("original model turn")
            return ModelTurn("", (ToolCall("offline", "mcp_list", {"server": "demo"}),))

    result = Core(
        tmp_path,
        Provider(),
        allow_mcp=True,
        approver=lambda *_args: True,
        mcp_cleanup_failure=fault.mark_failed,
    ).run("offline")
    assert result["status"] == "failed" and fault.broken and calls == ["original model turn"]
    assert "PRIVATE_CLOSE_OR_KEY" not in result["answer"]


def test_native_original_worker_retains_same_failed_core_bridge_manager_after_original_future(
    tmp_path, monkeypatch
):
    from doppel_agent.web.server import JobManager, ApprovalBroker
    from doppel_agent.provider import ModelTurn, ToolCall

    fault = ProviderReceiptFault()
    _unused, managers = failing_bridge(tmp_path, monkeypatch)

    class Provider:
        def next_turn(self, messages, tools):
            return ModelTurn("", (ToolCall("offline", "mcp_list", {"server": "demo"}),))

    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    monkeypatch.setattr(manager, "_provider", lambda _config: Provider())
    monkeypatch.setattr(
        ApprovalBroker, "request", lambda *_args: True
    )  # Original offline fixture consent only.
    futures = []
    original_submit = manager.pool.submit

    def submit(operation):
        future = original_submit(operation)
        futures.append(future)
        return future

    monkeypatch.setattr(manager.pool, "submit", submit)
    accepted = manager.submit({"prompt": "offline", "config": {"provider": "mock"}, "allow_mcp": True})
    assert len(futures) == 1
    futures[0].result(timeout=3)
    identity = accepted["run_id"]
    core = manager._cores[identity]
    assert fault.broken and core.mcp_cleanup_failed and core._mcp_bridge._manager is managers[0]
    assert manager.status(identity)["status"] == "failed" and len(managers) == 1
    with pytest.raises(RuntimeError):
        manager.submit({"prompt": "replacement", "config": {"provider": "mock"}})
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()
    assert manager._cores[identity] is core and len(futures) == 1 and len(managers) == 1


def test_original_core_cannot_reuse_run_after_failed_cleanup_or_drop_bridge(tmp_path, monkeypatch):
    from doppel_agent.core import Core
    from doppel_agent.provider import ModelTurn, ToolCall

    _unused, managers = failing_bridge(tmp_path, monkeypatch)

    class Provider:
        def next_turn(self, messages, tools):
            return ModelTurn("", (ToolCall("offline", "mcp_list", {"server": "demo"}),))

    core = Core(tmp_path, Provider(), allow_mcp=True, approver=lambda *_args: True)
    assert core.run("offline")["status"] == "failed"
    original = core._mcp_bridge
    with pytest.raises(MCPCleanupError):
        core.run("forbidden replacement")
    assert core._mcp_bridge is original and original._manager is managers[0] and len(managers) == 1


def test_original_native_non_mcp_worker_releases_only_healthy_original_core_reference(tmp_path, monkeypatch):
    from doppel_agent.web.server import JobManager

    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    futures = []
    original_submit = manager.pool.submit

    def submit(operation):
        future = original_submit(operation)
        futures.append(future)
        return future

    monkeypatch.setattr(manager.pool, "submit", submit)
    try:
        accepted = manager.submit({"prompt": "offline", "config": {"provider": "mock"}})
        assert len(futures) == 1
        futures[0].result(timeout=3)
        assert accepted["run_id"] not in manager._cores and not fault.broken
    finally:
        manager.close_owned()


def test_original_service_mcp_failure_marks_same_owner_fault_and_close_retains_owner(tmp_path, monkeypatch):
    import doppel_agent.runtime.service as module

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))

    async def scenario():
        service = module.RunService(tmp_path)
        exits = []

        @asynccontextmanager
        async def connector(server):
            try:
                yield object(), SimpleNamespace(capabilities={})
            finally:
                exits.append("original SDK exit")
                raise OSError("PRIVATE_MCP_EXIT")

        service.mcp_manager.connector = connector
        try:
            await service.start()
            await service.mcp_manager.get("demo")
            original = service.mcp_manager._connections["demo"]
            with pytest.raises(MCPCleanupError):
                await service.mcp_manager.invalidate("demo")
            assert (
                service._provider_receipt_fault.broken
                and service.mcp_manager._connections["demo"] is original
            )
            with pytest.raises(RuntimeError):
                service._assert_provider_admission()
            with pytest.raises(MCPCleanupError):
                await service.close()
            closing = service._close_task
            with pytest.raises(MCPCleanupError):
                await service.close()
            assert service._close_task is closing and service._owner.held and not service.cleanup_complete
            assert exits == ["original SDK exit"]
        finally:
            # Disposable failed-close fixture only; no production retry/repair.
            if service._owner.held:
                service._owner.release()

    asyncio.run(scenario())


def test_original_api_exact_mcp_cleanup_error_fixed_no_store_without_runtime_start(tmp_path):
    import httpx
    from doppel_agent.api.app import create_app

    async def scenario():
        app = create_app(tmp_path)

        @app.get("/fixture-mcp-failure")
        async def failure():
            raise MCPCleanupError()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/fixture-mcp-failure")
        assert response.status_code == 503 and response.json() == {"error": "mcp_cleanup_unresolved"}
        assert response.headers["Cache-Control"] == "no-store" and not app.state.run_service._owner.held
        await app.state.run_service.close()

    asyncio.run(scenario())
