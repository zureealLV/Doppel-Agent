"""C2c2g5d FIRST original SQL lifetime/owner definitions, ALL UNRUN.

Same actual disposable SQLite connection through observed proxy boundaries.
Returned proxy methods are not native/SDK/remote/process/billing proof.
"""

import asyncio
import sqlite3
import threading

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


def observe(monkeypatch, database, events, *, failure):
    import doppel_agent.persistence.tool_ledger as module

    original_connect = module.sqlite3.connect
    connections = []

    class Connection:
        def __init__(self, raw):
            self.raw = raw
            self.closes = 0

        @property
        def row_factory(self):
            return self.raw.row_factory

        @row_factory.setter
        def row_factory(self, value):
            if failure == "row_factory":
                raise ValueError("PRIVATE_SQL_SETUP")
            self.raw.row_factory = value

        def execute(self, sql, *args):
            if failure == "setup" and sql.startswith("PRAGMA"):
                raise OSError("PRIVATE_PRAGMA")
            if failure == "body" and sql.startswith("SELECT"):
                raise sqlite3.OperationalError("PRIVATE_QUERY")
            return self.raw.execute(sql, *args)

        def __enter__(self):
            self.raw.__enter__()
            events.append("original transaction enter")
            return self

        def __exit__(self, *details):
            value = self.raw.__exit__(*details)
            events.append("original rollback" if details[0] is not None else "original commit")
            if failure == "commit" and details[0] is None:
                raise OSError("PRIVATE_COMMIT_RETURN")
            if failure == "rollback" and details[0] is not None:
                raise ValueError("PRIVATE_ROLLBACK_RETURN")
            return value

        def close(self):
            self.closes += 1
            events.append("original close attempted")
            if failure == "close":
                raise ValueError("PRIVATE_SQL_CLOSE")
            self.raw.close()
            if failure == "closed_then_throw":
                raise OSError("PRIVATE_SQL_CLOSED_THROW")
            events.append("original close returned")

    def connect(path, *args, **kwargs):
        if str(path) != str(database):
            return original_connect(path, *args, **kwargs)
        # Fixture-only original raw handle may be torn down on fixture thread
        # after owned worker return; production sqlite thread policy untouched.
        instance = Connection(original_connect(path, *args, **{**kwargs, "check_same_thread": False}))
        connections.append(instance)
        events.append("original factory entered")
        return instance

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    return connections


@pytest.mark.parametrize("phase", ["row_factory", "setup", "body", "commit"])
def test_original_sql_failure_fences_before_close_but_known_close_is_not_fake_resource_unknown(
    tmp_path, monkeypatch, phase
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    path = tmp_path / "offline.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    ledger = ToolExecutionLedger(
        path,
        failure=lambda: (events.append("same fault"), fault.mark_failed()),
        cleanup_failure=fault.retain_cleanup,
    )
    connections = observe(monkeypatch, path, events, failure=phase)
    with pytest.raises(ToolLedgerPersistenceError, match="^tool_ledger_unresolved$") as error:
        ledger._begin("offline", "one", "write", {})
    assert error.value.source is ledger and ledger.failed and fault.broken
    assert not ledger.cleanup_uncertain and not fault.cleanup_uncertain
    assert len(connections) == 1 and connections[0].closes == 1
    assert events.index("same fault") < events.index("original close attempted")
    ledger.check_cleanup()
    fault.check_cleanup()
    with pytest.raises(ToolLedgerPersistenceError):
        ledger._begin("offline", "two", "write", {})
    assert len(connections) == 1 and connections[0].closes == 1


@pytest.mark.parametrize("phase", ["close", "closed_then_throw"])
def test_original_failed_close_retains_exact_connection_frame_and_owner_fault_no_reopen_or_retry(
    tmp_path, monkeypatch, phase
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    path = tmp_path / "offline.sqlite3"
    fault = ProviderReceiptFault()
    events = []
    ledger = ToolExecutionLedger(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    connections = observe(monkeypatch, path, events, failure=phase)
    try:
        with pytest.raises(ToolLedgerPersistenceError):
            ledger._begin("offline", "one", "write", {})
        frame = next(iter(ledger._unresolved_connections.values()))
        assert frame.connection is connections[0] and not frame.close_returned
        assert ledger.failed and ledger.cleanup_uncertain and fault.cleanup_uncertain
        assert next(iter(fault._cleanup_sources.values())) is ledger
        with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
            fault.check_cleanup()
        with pytest.raises(ToolLedgerPersistenceError):
            ledger.check_cleanup()
        with pytest.raises(ToolLedgerPersistenceError):
            ledger._finish("offline", "one", result="forbidden")
        assert next(iter(ledger._unresolved_connections.values())) is frame
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        connections[0].raw.close()  # Fixture-only cleanup, never resets production observations.


def test_original_known_input_refusal_healthy_rollback_close_does_not_latch_storage_unknown(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    ledger = ToolExecutionLedger(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    ledger._begin("offline", "one", "write", {})
    connections = observe(monkeypatch, path, events, failure="healthy")
    with pytest.raises(ValueError, match="different input"):
        ledger._begin("offline", "one", "other", {})
    assert not ledger.failed and not ledger.cleanup_uncertain and not fault.broken
    assert len(connections) == 1 and connections[0].closes == 1
    assert "original rollback" in events and events[-1] == "original close returned"


def test_original_known_refusal_then_failed_rollback_is_typed_storage_failure_not_validation(
    tmp_path, monkeypatch
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    path = tmp_path / "offline.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    ledger = ToolExecutionLedger(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    ledger._begin("offline", "one", "write", {})
    connections = observe(monkeypatch, path, events, failure="rollback")
    with pytest.raises(ToolLedgerPersistenceError):
        ledger._begin("offline", "one", "other", {})
    assert ledger.failed and fault.broken and not ledger.cleanup_uncertain
    assert len(connections) == 1 and connections[0].closes == 1


@pytest.mark.parametrize("entry", ["sync", "begin"])
def test_original_unsealed_reserved_row_stops_same_owner_even_without_async_phase_wrapper(
    tmp_path, monkeypatch, entry
):
    path = tmp_path / "offline.sqlite3"
    fault = ProviderReceiptFault()
    events = []
    ledger = ToolExecutionLedger(
        path,
        failure=lambda: (events.append("same fault"), fault.mark_failed()),
        cleanup_failure=fault.retain_cleanup,
    )
    ledger._begin("offline", "one", "write", {})
    connections = observe(monkeypatch, path, events, failure="healthy")
    invoked = []
    with pytest.raises(RuntimeError, match="indeterminate"):
        if entry == "sync":
            ledger.execute_once("offline", "one", "write", {}, lambda: invoked.append("forbidden effect"))
        else:
            ledger._begin("offline", "one", "write", {})
    assert not invoked and ledger.failed and fault.broken
    assert not ledger.cleanup_uncertain and not fault.cleanup_uncertain
    assert events.index("same fault") < events.index("original close attempted")
    assert len(connections) == 1 and connections[0].closes == 1


def test_original_constructor_failed_close_is_retained_before_missing_result_assignment(
    tmp_path, monkeypatch
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    path = tmp_path / "offline.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    connections = observe(monkeypatch, path, events, failure="close")
    try:
        with pytest.raises(ToolLedgerPersistenceError) as error:
            ToolExecutionLedger(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
        original = error.value.source
        assert next(iter(fault._cleanup_sources.values())) is original
        assert next(iter(original._unresolved_connections.values())).connection is connections[0]
        assert fault.broken and fault.cleanup_uncertain and connections[0].closes == 1
    finally:
        connections[0].raw.close()


def test_original_opaque_connect_throw_after_allocation_retains_original_attempt_not_second_connection(
    tmp_path, monkeypatch
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError
    import doppel_agent.persistence.tool_ledger as module

    path = tmp_path / "offline.sqlite3"
    fault = ProviderReceiptFault()
    events = []
    ledger = ToolExecutionLedger(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    original_connect = module.sqlite3.connect
    handles = []

    def opaque(*args, **kwargs):
        handles.append(original_connect(*args, **kwargs))
        events.append("original allocation")
        raise OSError("PRIVATE_UNRETURNED_FACTORY")

    monkeypatch.setattr(module.sqlite3, "connect", opaque)
    try:
        with pytest.raises(ToolLedgerPersistenceError):
            ledger._begin("offline", "one", "write", {})
        frame = next(iter(ledger._unresolved_connections.values()))
        assert frame.connection is None and frame.connect_attempted and not frame.close_returned
        assert fault.cleanup_uncertain and ledger.cleanup_uncertain
        with pytest.raises(ToolLedgerPersistenceError):
            ledger._begin("offline", "two", "write", {})
        assert events == ["original allocation"] and len(handles) == 1
    finally:
        handles[0].close()  # Fixture's external original handle, not ledger cleanup proof.


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_actual_factory_runtime_constructor_binds_same_owner_ledger_cleanup_retention(
    tmp_path, monkeypatch, mode
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.factory import create_runtime

    fault = ProviderReceiptFault()
    path = tmp_path / ".doppel-agent" / "tool-executions.sqlite3"
    events = []
    connections = observe(monkeypatch, path, events, failure="close")
    try:
        with pytest.raises(ToolLedgerPersistenceError) as error:
            create_runtime(mode, tmp_path, MockProvider(), reviewed_legacy=True, provider_receipt_fault=fault)
        assert fault.broken and fault.cleanup_uncertain
        assert next(iter(fault._cleanup_sources.values())) is error.value.source
        assert next(iter(error.value.source._unresolved_connections.values())).connection is connections[0]
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        connections[0].raw.close()


@pytest.mark.parametrize("which", ["mcp", "manual_constructor"])
def test_actual_original_service_failed_ledger_sql_close_holds_owner_and_same_failed_close_task(
    tmp_path, monkeypatch, which
):
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError
    from doppel_agent.runtime.service import RunService

    async def scenario():
        service = RunService(tmp_path)
        await service.start()
        fault = service._provider_receipt_fault
        events = []
        path = (
            service.mcp_ledger.database if which == "mcp" else service.state_root / "tool-executions.sqlite3"
        )
        connections = observe(monkeypatch, path, events, failure="close")
        try:
            with pytest.raises(ToolLedgerPersistenceError):
                if which == "mcp":
                    service.mcp_ledger._begin("offline", "one", "write", {})
                else:
                    await service._patch_ledger_service()
            assert fault.cleanup_uncertain and service._owner.held
            if which == "manual_constructor":
                assert service._patch_ledger is None
            original = next(iter(fault._cleanup_sources.values()))
            frame = next(iter(original._unresolved_connections.values()))
            with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                await service.close()
            closing = service._close_task
            assert not service.cleanup_complete and service._owner.held
            with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                await service.close()
            assert service._close_task is closing and service._owner.held
            assert next(iter(original._unresolved_connections.values())) is frame
            assert len(connections) == 1 and connections[0].closes == 1
        finally:
            for connection in connections:
                connection.raw.close()
            service._owner.release()  # Explicit disposable test teardown, NOT production recovery.

    asyncio.run(scenario())


def test_actual_original_api_tool_ledger_failure_fixed_503_no_store_without_runtime_start(tmp_path):
    import httpx
    from doppel_agent.api.app import create_app
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    async def scenario():
        app = create_app(tmp_path)

        @app.get("/fixture-ledger-lifetime")
        async def unknown():
            raise ToolLedgerPersistenceError()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/fixture-ledger-lifetime")
        assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
        assert response.json() == {"error": "tool_ledger_unresolved"}

    asyncio.run(scenario())


def test_original_default_ledger_cancellation_stops_owner_before_gated_sql_close_and_retains_failed_original(
    tmp_path, monkeypatch
):
    import doppel_agent.persistence.tool_ledger as module

    async def scenario():
        path = tmp_path / "offline.sqlite3"
        fault = ProviderReceiptFault()
        events = []
        ledger = ToolExecutionLedger(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
        connections = observe(monkeypatch, path, events, failure="close")
        waiting, release = threading.Event(), threading.Event()
        # Observe the actual proxy's original close, not replace original _begin,
        # transaction, worker or SQL connection with a second executor.
        original_connect = module.sqlite3.connect

        def gated_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            original_close = connection.close

            def close():
                waiting.set()
                assert release.wait(3)
                return original_close()

            connection.close = close
            return connection

        monkeypatch.setattr(module.sqlite3, "connect", gated_connect)
        invoked = []

        async def operation():
            invoked.append("forbidden SDK/effect")
            return "known"

        task = asyncio.create_task(ledger.aexecute_once("offline", "one", "write", {}, operation))
        try:
            async with asyncio.timeout(2):
                while not waiting.is_set():
                    await asyncio.sleep(0)
            task.cancel()
            async with asyncio.timeout(2):
                while not fault.broken:
                    await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert ledger.failed and not task.done() and not fault.cleanup_uncertain and not invoked
        finally:
            release.set()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert ledger.cleanup_uncertain and fault.cleanup_uncertain and not invoked
            frame = next(iter(ledger._unresolved_connections.values()))
            assert frame.connection is connections[0] and connections[0].closes == 1
            assert next(iter(fault._cleanup_sources.values())) is ledger
        finally:
            for connection in connections:
                connection.raw.close()

    asyncio.run(scenario())


def test_actual_mcp_reservation_sql_close_failure_is_local_publication_plus_sql_cleanup_not_remote_or_sdk_failure(
    tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from mcp import types
    import doppel_agent.runtime.service as module
    from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
    from doppel_agent.mcp.client_manager import MCPPublicationError
    from doppel_agent.mcp.executor import MCPToolExecutor
    from doppel_agent.permissions import PermissionManager

    config = MCPConfig(
        {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")},
        tmp_path / "offline-config.json",
    )
    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config)

    async def scenario():
        events = []
        service = module.RunService(tmp_path)

        class Session:
            async def list_tools(self, *, params=None):
                return types.ListToolsResult(tools=[types.Tool(name="write", inputSchema={"type": "object"})])

            async def call_tool(self, *_args, **_kwargs):
                events.append("forbidden remote dispatch")
                return types.CallToolResult(content=[])

        @asynccontextmanager
        async def connector(_server):
            events.append("original SDK enter")
            try:
                yield Session(), SimpleNamespace(capabilities={})
            finally:
                events.append("original SDK exit")

        service.mcp_manager.connector = connector
        await service.start()
        connections = observe(monkeypatch, service.mcp_ledger.database, events, failure="close")
        executor = MCPToolExecutor(
            service.mcp_manager,
            service.mcp_catalog,
            PermissionManager(frozenset({"mcp_execute"})),
            ledger=service.mcp_ledger,
        )
        try:
            with pytest.raises(MCPPublicationError):
                await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
            assert service.mcp_manager.publication_failed and not service.mcp_manager.execution_failed
            assert (
                not service.mcp_manager.cleanup_failed and service._provider_receipt_fault.cleanup_uncertain
            )
            assert service.mcp_ledger.cleanup_uncertain and "forbidden remote dispatch" not in events
            with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                await service.close()
            assert service._owner.held and not service.cleanup_complete
            assert events.count("original SDK exit") == 1 and not service.mcp_manager.cleanup_failed
            frame = next(iter(service.mcp_ledger._unresolved_connections.values()))
            assert frame.connection is connections[0] and connections[0].closes == 1
        finally:
            for connection in connections:
                connection.raw.close()
            service._owner.release()  # Disposable teardown, not recovery authorization.

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["close", "commit"])
def test_actual_original_kernel_app_constructor_ledger_failure_retention_before_service_assignment(
    tmp_path, monkeypatch, phase
):
    """Actual create_app/RunService/ledger; synthetic Legacy host, no native/UI proof."""
    import doppel_agent.desktop as desktop
    from doppel_agent.projects.desktop_kernel import DesktopKernel
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    class Legacy:
        server_port = 1

        def __init__(self, *_args, **_kwargs):
            pass

        def serve_forever(self):
            pass

        def shutdown(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(desktop, "ConsoleServer", Legacy)
    events = []
    path = tmp_path / ".doppel-agent" / "mcp-tool-executions.sqlite3"
    connections = observe(monkeypatch, path, events, failure=phase)
    kernel = DesktopKernel(tmp_path)
    try:
        if phase == "close":
            with pytest.raises(RuntimeError, match="desktop startup cleanup is unresolved"):
                kernel.start()
            assert kernel.app is None and kernel.owner.held and not kernel._closed
            original = next(iter(kernel._startup_cleanup_sources.values()))
            frame = next(iter(original._unresolved_connections.values()))
            assert frame.connection is connections[0] and original.cleanup_uncertain
            with pytest.raises(RuntimeError, match="desktop startup cleanup is unresolved"):
                kernel.close()
            assert kernel.owner.held and next(iter(original._unresolved_connections.values())) is frame
        else:
            with pytest.raises(ToolLedgerPersistenceError):
                kernel.start()
            assert not kernel.owner.held and kernel._closed and not kernel._startup_cleanup_sources
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        for connection in connections:
            connection.raw.close()
        if kernel.owner.held:
            kernel.owner.release()  # Explicit disposable teardown only.
