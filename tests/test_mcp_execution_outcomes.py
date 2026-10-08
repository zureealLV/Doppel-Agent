"""C2c2g5b FIRST entered MCP effect-outcome definitions, ALL UNRUN.

FIRST definitions before original execution-failure/error/hook/Core source.
These definitions do not prove remote side effects, model quality, SDK/native
drain or billing. Known original SDK exit does not clear remote uncertainty.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.permissions import PermissionManager


def config(path):
    return MCPConfig(
        {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")},
        path / "offline-config.json",
    )


def fixture(events, *, kind, ready_delay=0, dispatch_entered=None):
    class Session:
        async def list_tools(self, *, params=None):
            return types.ListToolsResult(tools=[types.Tool(name="write", inputSchema={"type": "object"})])

        async def call_tool(self, *_args, **_kwargs):
            events.append("original SDK dispatch")
            if dispatch_entered is not None:
                dispatch_entered.set()
            if kind == "transport":
                raise ConnectionError("PRIVATE_ACCEPTED_TRANSPORT")
            if kind == "cancel":
                raise asyncio.CancelledError()
            if kind == "deadline":
                await asyncio.Event().wait()
            if kind == "publication":
                return types.CallToolResult(content=[], structuredContent={"value": float("nan")})
            return types.CallToolResult(
                content=[types.TextContent(text="known protocol error")], isError=True
            )

    @asynccontextmanager
    async def connector(_server):
        events.append("original SDK enter")
        try:
            if ready_delay:
                await asyncio.sleep(ready_delay)
            yield Session(), SimpleNamespace(capabilities={})
        finally:
            events.append("original SDK exit")

    return connector


@pytest.mark.parametrize("kind", ["transport", "cancel"])
def test_actual_entered_tool_failure_is_fatal_sticky_execution_unknown_not_fake_resource_cleanup_failure(
    tmp_path, kind
):
    from doppel_agent.mcp.client_manager import MCPInvocationError

    async def scenario():
        events = []
        manager = MCPClientManager(
            config(tmp_path),
            connector=fixture(events, kind=kind),
            execution_failure=lambda: events.append("same execution fault"),
        )
        catalog = MCPToolCatalog(manager)
        audits = []
        executor = MCPToolExecutor(
            manager, catalog, PermissionManager(frozenset({"mcp_execute"})), audit=audits.append
        )
        await catalog.list_server("demo")
        original = manager._connections["demo"]
        with pytest.raises(MCPInvocationError, match="^mcp_execution_unresolved$"):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert manager.execution_failed and not manager.cleanup_failed and not audits
        # Preserve original manager's cleanup boundary after failed call. The
        # SAME SDK owner may close normally, but manager execution stays unknown.
        assert events == [
            "original SDK enter",
            "original SDK dispatch",
            "same execution fault",
            "original SDK exit",
        ]
        assert manager._connections == {} and original.lifetime.known_closed
        with pytest.raises(MCPInvocationError):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="two")
        assert events.count("original SDK dispatch") == 1 and manager._connections == {}
        await manager.close()  # Known actual source cleanup may succeed; remote outcome stays UNKNOWN.
        closing = manager._close_task
        await manager.close()
        assert original.lifetime.known_closed and manager.execution_failed and not manager.cleanup_failed
        assert events[-1] == "original SDK exit" and manager._close_task is closing
        with pytest.raises(MCPInvocationError):
            await manager.get("demo")
        assert events.count("original SDK enter") == 1 and events.count("original SDK exit") == 1

    asyncio.run(scenario())


def test_actual_known_protocol_is_error_response_does_not_fabricate_unknown_execution_or_cleanup(tmp_path):
    async def scenario():
        events = []
        manager = MCPClientManager(
            config(tmp_path),
            connector=fixture(events, kind="known"),
            execution_failure=lambda: events.append("forbidden fault"),
        )
        executor = MCPToolExecutor(
            manager, MCPToolCatalog(manager), PermissionManager(frozenset({"mcp_execute"}))
        )
        result = await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert not result.success and result.error == "known protocol error"
        assert not manager.execution_failed and not manager.cleanup_failed and "forbidden fault" not in events
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("ready_delay", [0, 0.04])
def test_actual_original_core_bridge_deadline_cannot_become_recoverable_tool_error_or_second_model_turn(
    tmp_path, monkeypatch, ready_delay
):
    from doppel_agent.core import Core
    from doppel_agent.mcp.client_manager import MCPInvocationError
    from doppel_agent.provider import ModelTurn, ToolCall
    import doppel_agent.mcp_bridge as module

    events = []
    managers = []
    model_calls = []
    fault = []
    original_manager = module.MCPClientManager
    original_wait_for = asyncio.wait_for
    dispatch_entered = asyncio.Event()
    connector = fixture(events, kind="deadline", ready_delay=ready_delay,
                        dispatch_entered=dispatch_entered)

    def manager(configuration, **kwargs):
        instance = original_manager(configuration, connector=connector, **kwargs)
        managers.append(instance)
        return instance

    async def deadline(awaitable, timeout):
        assert timeout == 45  # Preserve original production deadline; only disposable fixture shortens it.
        # This test targets an ENTERED effect timeout, not a cold catalog/open
        # deadline. Run the SAME original invocation and observe actual dispatch
        # before imposing the short fixture deadline. The watchdog is not an SLA.
        original = asyncio.ensure_future(awaitable)
        try:
            await original_wait_for(dispatch_entered.wait(), timeout=3)
            return await original_wait_for(original, timeout=0.02)
        finally:
            if not original.done():
                original.cancel()
            await asyncio.gather(original, return_exceptions=True)

    class Provider:
        def next_turn(self, _messages, _tools):
            model_calls.append("original model turn")
            return ModelTurn(
                "",
                (
                    ToolCall(
                        "offline", "mcp_call", {"server": "demo", "tool": "write", "arguments_json": "{}"}
                    ),
                ),
            )

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))
    monkeypatch.setattr(module, "MCPClientManager", manager)
    monkeypatch.setattr(module.asyncio, "wait_for", deadline)
    core = Core(
        tmp_path,
        Provider(),
        allow_mcp=True,
        approver=lambda *_args: True,
        mcp_execution_failure=lambda: fault.append("same original execution fault"),
    )
    result = core.run("offline")
    assert result["status"] == "failed" and "mcp_execution_unresolved" in result["answer"]
    assert fault and model_calls == ["original model turn"] and len(managers) == 1
    assert events.count("original SDK dispatch") == 1
    bridge = core._mcp_bridge
    original = managers[0]
    assert core.mcp_execution_failed and not core.mcp_cleanup_failed and bridge._manager is original
    assert original.execution_failed and not original.cleanup_failed
    assert original._close_task.done() and original._close_task.exception() is None
    with pytest.raises(MCPInvocationError):
        core.run("forbidden replacement")
    assert core._mcp_bridge is bridge and len(managers) == 1 and len(model_calls) == 1


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("kind", ["transport", "publication"])
def test_actual_legacy_factory_core_marks_original_shared_fault_and_retains_unknown_source(
    tmp_path, monkeypatch, reviewed, kind
):
    """FIRST original factory/Core/Bridge path, UNRUN; not SDK/native drain proof."""
    import doppel_agent.mcp_bridge as module
    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.base import ResumeCommand, RunRequest
    from doppel_agent.runtime.factory import create_runtime
    from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault

    events = []
    managers = []
    turns = []
    original_manager = module.MCPClientManager

    def manager(configuration, **kwargs):
        instance = original_manager(configuration, connector=fixture(events, kind=kind), **kwargs)
        managers.append(instance)
        return instance

    class Provider:
        def next_turn(self, _messages, _tools):
            turns.append("original model turn")
            return ModelTurn(
                "",
                (
                    ToolCall(
                        "offline", "mcp_call", {"server": "demo", "tool": "write", "arguments_json": "{}"}
                    ),
                ),
            )

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))
    monkeypatch.setattr(module, "MCPClientManager", manager)

    async def scenario():
        fault = ProviderReceiptFault()
        runtime = create_runtime(
            "legacy",
            tmp_path,
            Provider(),
            reviewed_legacy=reviewed,
            core_options={"allow_mcp": True, "approver": lambda *_args: True},
            provider_receipt_fault=fault,
        )
        request = RunRequest("offline fixture", run_id="a" * 32, thread_id="b" * 32)
        if reviewed:
            pending = await runtime.run(request)
            assert pending.status == "interrupted" and not managers and not fault.broken
            command = ResumeCommand(
                request.run_id,
                request.thread_id,
                {"action": "approve", "_legacy_interrupt_id": pending.metadata["interrupts"][0]["id"]},
            )
            with pytest.raises(ProviderReceiptError):
                await runtime.resume(command)
        else:
            with pytest.raises(ProviderReceiptError):
                await runtime.run(request)
        core = next(iter(runtime._unresolved_mcp_cores.values()))
        assert fault.broken and not core.mcp_cleanup_failed
        assert core.mcp_execution_failed is (kind == "transport")
        assert core.mcp_publication_failed is (kind == "publication")
        assert core._mcp_bridge._manager is managers[0]
        assert managers[0]._close_task.done() and managers[0]._close_task.exception() is None
        assert events.count("original SDK dispatch") == 1 and turns == ["original model turn"]
        with pytest.raises(ProviderReceiptError):
            await runtime.run(RunRequest("forbidden reuse", run_id="c" * 32, thread_id="d" * 32))
        assert next(iter(runtime._unresolved_mcp_cores.values())) is core
        assert len(managers) == 1 and len(turns) == 1

    asyncio.run(scenario())


def test_actual_pending_semaphore_cancel_is_not_entered_remote_execution_unknown(tmp_path):
    async def scenario():
        events = []
        faults = []
        original_config = config(tmp_path)
        from dataclasses import replace

        configured = MCPConfig(
            {"demo": replace(original_config.servers["demo"], max_concurrency=1)}, original_config.source
        )
        manager = MCPClientManager(
            configured,
            connector=fixture(events, kind="known"),
            execution_failure=lambda: faults.append("forbidden"),
        )
        catalog = MCPToolCatalog(manager)
        await catalog.list_server("demo")
        managed = manager._clients["demo"]
        entered, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_semaphore = managed.semaphore

        class Semaphore:
            async def __aenter__(self):
                waiting.set()
                return await original_semaphore.__aenter__()

            async def __aexit__(self, *exc):
                return await original_semaphore.__aexit__(*exc)

        async def original_body():
            async with manager.session("demo"):
                entered.set()
                await release.wait()

        running = asyncio.create_task(original_body())
        await asyncio.wait_for(entered.wait(), 2)
        managed.semaphore = Semaphore()
        executor = MCPToolExecutor(manager, catalog, PermissionManager(frozenset({"mcp_execute"})))
        pending = asyncio.create_task(
            executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="pending")
        )
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 2)
            assert not manager.execution_failed and not manager.cleanup_failed and not faults
            assert events == ["original SDK enter"]
        finally:
            release.set()
        await asyncio.wait_for(running, 2)
        await manager.close()

    asyncio.run(scenario())


def test_actual_original_ledger_records_fixed_entered_execution_failure_not_success_or_private_transport(
    tmp_path,
):
    from doppel_agent.mcp.client_manager import MCPInvocationError
    from doppel_agent.persistence.tool_ledger import ToolExecutionLedger

    async def scenario():
        events = []
        manager = MCPClientManager(config(tmp_path), connector=fixture(events, kind="transport"))
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        executor = MCPToolExecutor(
            manager, MCPToolCatalog(manager), PermissionManager(frozenset({"mcp_execute"})), ledger=ledger
        )
        with pytest.raises(MCPInvocationError, match="^mcp_execution_unresolved$"):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        with ledger._connect() as connection:
            row = connection.execute("SELECT status,result,error FROM tool_executions").fetchone()
            assert row["status"] == "failed" and row["result"] == ""
            assert row["error"] == "MCPInvocationError: tool execution failed"
        assert (
            events.count("original SDK dispatch") == 1
            and manager.execution_failed
            and not manager.cleanup_failed
        )
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("adapter", ["langchain", "doppel"])
def test_actual_original_gateway_adapter_cannot_convert_entered_unknown_to_model_view(tmp_path, adapter):
    from doppel_agent.mcp.client_manager import MCPInvocationError
    from doppel_agent.mcp.tool_adapter import langchain_mcp_tools, doppel_mcp_tools

    async def scenario():
        events = []
        manager = MCPClientManager(config(tmp_path), connector=fixture(events, kind="transport"))
        catalog = MCPToolCatalog(manager)
        descriptors = await catalog.list_server("demo")
        executor = MCPToolExecutor(manager, catalog, PermissionManager(frozenset({"mcp_execute"})))
        if adapter == "langchain":
            gateway = langchain_mcp_tools(descriptors, executor)[0]
            # Strengthened original SDK envelope entry, not an unbound private call.
            invoke = gateway.ainvoke({"type": "tool_call", "id": "one", "name": gateway.name, "args": {}})
        else:
            gateway = doppel_mcp_tools(descriptors, executor)[0]
            invoke = gateway.async_handler({}, "offline", "one")
        with pytest.raises(MCPInvocationError):
            await invoke
        assert (
            manager.execution_failed
            and not manager.cleanup_failed
            and events.count("original SDK dispatch") == 1
        )
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("concurrent", [False, True])
def test_langchain_original_distinct_call_ids_same_arguments_do_not_alias_ledger(tmp_path, concurrent):
    """FIRST 2026-10-07 ALL UNRUN; protocol fixture, not real MCP/process proof."""
    from doppel_agent.mcp.tool_adapter import langchain_mcp_tools, set_mcp_run_id, reset_mcp_run_id
    from doppel_agent.persistence.tool_ledger import ToolExecutionLedger

    async def scenario():
        events, audits = [], []
        manager = MCPClientManager(config(tmp_path), connector=fixture(events, kind="known_tool_error"))
        catalog = MCPToolCatalog(manager)
        executor = MCPToolExecutor(
            manager,
            catalog,
            PermissionManager(frozenset({"mcp_execute"})),
            ledger=ToolExecutionLedger(tmp_path / "original-ledger.sqlite3"),
            audit=audits.append,
        )
        descriptors = await catalog.list_server("demo")
        gateway = langchain_mcp_tools(descriptors, executor)[0]
        call = lambda identifier: {"type": "tool_call", "name": gateway.name, "id": identifier, "args": {}}
        token = set_mcp_run_id("original-run")
        try:
            if concurrent:
                first, second = await asyncio.gather(
                    gateway.ainvoke(call("original-one")), gateway.ainvoke(call("original-two"))
                )
            else:
                first = await gateway.ainvoke(call("original-one"))
                second = await gateway.ainvoke(call("original-two"))
            assert first.tool_call_id == "original-one" and second.tool_call_id == "original-two"
            assert first.content == second.content == "MCP tool error: known protocol error"
            assert (
                events.count("original SDK dispatch") == 2
            )  # Distinct original calls are not cached argument hashes.
            assert {row["tool_call_id"] for row in audits} == {"original-one", "original-two"}
            assert all(row["run_id"] == "original-run" and row["success"] is False for row in audits)
            replay = await gateway.ainvoke(call("original-one"))
            assert replay.content == first.content and replay.tool_call_id == "original-one"
            assert events.count("original SDK dispatch") == 2 and len(audits) == 2
            with pytest.raises(ValueError, match="^mcp_tool_call_scope_unavailable$"):
                await gateway._arun()
            assert (
                events.count("original SDK dispatch") == 2
            )  # Context reset after original BaseTool completion.
            assert not manager.execution_failed and not manager.cleanup_failed
        finally:
            reset_mcp_run_id(token)
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_id", [None, "", 0, False, "x\x00y", "x" * 1025, "\ud800"])
def test_langchain_missing_unusable_original_call_identity_fails_before_remote_dispatch(tmp_path, bad_id):
    from doppel_agent.mcp.tool_adapter import langchain_mcp_tools

    async def scenario():
        events, audits = [], []
        manager = MCPClientManager(config(tmp_path), connector=fixture(events, kind="known_tool_error"))
        catalog = MCPToolCatalog(manager)
        executor = MCPToolExecutor(
            manager, catalog, PermissionManager(frozenset({"mcp_execute"})), audit=audits.append
        )
        gateway = langchain_mcp_tools(await catalog.list_server("demo"), executor)[0]
        try:
            with pytest.raises(ValueError, match="^mcp_tool_call_scope_unavailable$"):
                await gateway.ainvoke({"type": "tool_call", "name": gateway.name, "id": bad_id, "args": {}})
            assert events.count("original SDK dispatch") == 0 and audits == []
            with pytest.raises(ValueError, match="^mcp_tool_call_scope_unavailable$"):
                await gateway._arun()
            with pytest.raises(ValueError, match="^mcp_tool_call_scope_unavailable$"):
                await gateway.ainvoke({})
            assert events.count("original SDK dispatch") == 0 and not manager.execution_failed
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["transport", "publication"])
def test_actual_original_native_worker_retains_unknown_core_bridge_manager_but_not_fake_cleanup_failure(
    tmp_path, monkeypatch, kind
):
    import doppel_agent.mcp_bridge as module
    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.provider_recording import ProviderReceiptFault
    from doppel_agent.web.server import JobManager, ApprovalBroker

    fault = ProviderReceiptFault()
    events = []
    managers = []
    turns = []
    original_manager = module.MCPClientManager

    def manager(configuration, **kwargs):
        instance = original_manager(configuration, connector=fixture(events, kind=kind), **kwargs)
        managers.append(instance)
        return instance

    class Provider:
        def next_turn(self, _messages, _tools):
            turns.append("original turn")
            return ModelTurn(
                "",
                (
                    ToolCall(
                        "offline", "mcp_call", {"server": "demo", "tool": "write", "arguments_json": "{}"}
                    ),
                ),
            )

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))
    monkeypatch.setattr(module, "MCPClientManager", manager)
    native = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    monkeypatch.setattr(native, "_provider", lambda _config: Provider())
    monkeypatch.setattr(ApprovalBroker, "request", lambda *_args: True)
    futures = []
    original_submit = native.pool.submit

    def submit(operation):
        future = original_submit(operation)
        futures.append(future)
        return future

    monkeypatch.setattr(native.pool, "submit", submit)
    accepted = native.submit({"prompt": "offline", "config": {"provider": "mock"}, "allow_mcp": True})
    assert len(futures) == 1
    futures[0].result(timeout=3)
    core = native._cores[accepted["run_id"]]
    original = managers[0]
    assert fault.broken and not core.mcp_cleanup_failed
    assert core.mcp_execution_failed is (kind == "transport")
    assert core.mcp_publication_failed is (kind == "publication")
    assert core._mcp_bridge._manager is original and not original.cleanup_failed
    assert original.execution_failed is (kind == "transport")
    assert original.publication_failed is (kind == "publication")
    assert turns == ["original turn"] and native.status(accepted["run_id"])["status"] == "failed"
    assert not native._operation_cleanup_uncertain.is_set()
    with pytest.raises(RuntimeError):
        native.submit({"prompt": "forbidden", "config": {"provider": "mock"}})
    native.close_owned()  # Healthy source SDK/pool close is NOT remote outcome resolution.
    assert native._cores[accepted["run_id"]] is core and len(managers) == 1
    assert core.mcp_execution_failed or core.mcp_publication_failed


@pytest.mark.parametrize("kind", ["transport", "publication"])
def test_actual_original_service_manager_marks_same_execution_fault_before_model_readmission(
    tmp_path, monkeypatch, kind
):
    import doppel_agent.runtime.service as module

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))
    from doppel_agent.mcp.client_manager import MCPInvocationError, MCPPublicationError

    async def scenario():
        service = module.RunService(tmp_path)
        events = []
        service.mcp_manager.connector = fixture(events, kind=kind)
        await service.start()
        executor = MCPToolExecutor(
            service.mcp_manager, service.mcp_catalog, PermissionManager(frozenset({"mcp_execute"}))
        )
        with pytest.raises(MCPInvocationError if kind == "transport" else MCPPublicationError):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert service._provider_receipt_fault.broken
        assert service.mcp_manager.execution_failed is (kind == "transport")
        assert service.mcp_manager.publication_failed is (kind == "publication")
        assert not service.mcp_manager.cleanup_failed
        with pytest.raises(RuntimeError):
            service._assert_provider_admission()
        await service.close()
        assert service.cleanup_complete and not service._owner.held
        assert service.mcp_manager.execution_failed or service.mcp_manager.publication_failed
        assert events.count("original SDK dispatch") == 1 and events.count("original SDK exit") == 1
        # Disposable original source cleanup only; no native/remote/charge claim.

    asyncio.run(scenario())


def test_actual_original_api_execution_unknown_fixed_503_no_store_without_runtime_start(tmp_path):
    import httpx
    from doppel_agent.api.app import create_app
    from doppel_agent.mcp.client_manager import MCPInvocationError

    async def scenario():
        app = create_app(tmp_path)

        @app.get("/fixture-mcp-execution")
        async def unknown():
            raise MCPInvocationError()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/fixture-mcp-execution")
        assert response.status_code == 503 and response.json() == {"error": "mcp_execution_unresolved"}
        assert response.headers["Cache-Control"] == "no-store" and not app.state.run_service._owner.held
        await app.state.run_service.close()

    asyncio.run(scenario())


def test_actual_original_standalone_native_sibling_approval_cannot_dispatch_after_shared_execution_unknown(
    tmp_path, monkeypatch
):
    import threading
    import doppel_agent.mcp_bridge as module
    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.web.server import JobManager, ApprovalBroker

    waiting, release = threading.Event(), threading.Event()
    approvals = []
    turns = []
    managers = []
    events = []
    original_manager = module.MCPClientManager

    def manager(configuration, **kwargs):
        instance = original_manager(configuration, connector=fixture(events, kind="transport"), **kwargs)
        managers.append(instance)
        return instance

    class Provider:
        def next_turn(self, _messages, _tools):
            turns.append("original turn")
            return ModelTurn(
                "",
                (
                    ToolCall(
                        "offline", "mcp_call", {"server": "demo", "tool": "write", "arguments_json": "{}"}
                    ),
                ),
            )

    def approval(broker, *_args):
        approvals.append(broker)
        if len(approvals) == 1:
            waiting.set()
            if not release.wait(3):
                raise AssertionError("original approval fixture was not released")
        return True

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config(tmp_path))
    monkeypatch.setattr(module, "MCPClientManager", manager)
    monkeypatch.setattr(ApprovalBroker, "request", approval)
    native = JobManager(tmp_path)
    monkeypatch.setattr(native, "_provider", lambda _config: Provider())
    futures = []
    original_submit = native.pool.submit

    def submit(operation):
        future = original_submit(operation)
        futures.append(future)
        return future

    monkeypatch.setattr(native.pool, "submit", submit)
    first = native.submit({"prompt": "original waiting", "config": {"provider": "mock"}, "allow_mcp": True})
    try:
        assert waiting.wait(2)
        second = native.submit(
            {"prompt": "original failing", "config": {"provider": "mock"}, "allow_mcp": True}
        )
        assert len(futures) == 2
        futures[1].result(timeout=3)
        assert native._operation_fault.is_set() and not native._operation_cleanup_uncertain.is_set()
        assert events.count("original SDK dispatch") == 1 and len(managers) == 1
    finally:
        release.set()
    futures[0].result(timeout=3)
    assert native.status(first["run_id"])["status"] == native.status(second["run_id"])["status"] == "failed"
    assert len(turns) == 2 and len(managers) == 1 and events.count("original SDK dispatch") == 1
    assert native._cores[second["run_id"]].mcp_execution_failed
    native.close_owned()
