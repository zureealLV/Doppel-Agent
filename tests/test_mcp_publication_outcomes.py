"""C2c2g5c FIRST acknowledgement/publication definitions, ALL UNRUN.

Actual original gateway/ledger/source with disposable offline sessions. None of
these definitions proves remote side effects, native drain or provider billing.
"""

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPClientManager
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig
from doppel_agent.mcp.executor import MCPToolExecutor
from doppel_agent.permissions import PermissionManager
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger


def configuration(path):
    return MCPConfig(
        {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")},
        path / "offline-config.json",
    )


def connector_for(events, reply):
    class Session:
        async def list_tools(self, *, params=None):
            return types.ListToolsResult(tools=[types.Tool(name="write", inputSchema={"type": "object"})])

        async def call_tool(self, *_args, **_kwargs):
            events.append("original SDK reply")
            return reply

    @asynccontextmanager
    async def connector(_server):
        events.append("original SDK enter")
        try:
            yield Session(), SimpleNamespace(capabilities={})
        finally:
            events.append("original SDK exit")

    return connector


def setup(path, events, reply, *, ledger=None, audit=None):
    manager = MCPClientManager(
        configuration(path),
        connector=connector_for(events, reply),
        execution_failure=lambda: events.append("remote unknown"),
        publication_failure=lambda: events.append("local publication unknown"),
    )
    executor = MCPToolExecutor(
        manager,
        MCPToolCatalog(manager),
        PermissionManager(frozenset({"mcp_execute"})),
        ledger=ledger,
        audit=audit,
    )
    return manager, executor


@pytest.mark.parametrize("bad", ["none", "dict", "namespace", "content", "is_error", "nonterminal"])
def test_original_unusable_reply_cannot_be_default_empty_success_or_success_audit(tmp_path, bad):
    from doppel_agent.mcp.client_manager import MCPInvocationError

    async def scenario():
        reply = types.CallToolResult(content=[])
        if bad == "none":
            reply = None
        elif bad == "dict":
            reply = {"content": [], "isError": False}
        elif bad == "namespace":
            reply = SimpleNamespace(content=[], is_error=False)
        elif bad == "content":
            reply.content = [None]
        elif bad == "is_error":
            reply.is_error = "false"
        elif bad == "nonterminal":
            reply.result_type = "task"
        events = []
        audits = []
        manager, executor = setup(tmp_path, events, reply, audit=audits.append)
        with pytest.raises(MCPInvocationError):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert manager.execution_failed and not manager.publication_failed and not manager.cleanup_failed
        assert not audits and events.count("original SDK reply") == 1
        assert events.index("remote unknown") < events.index("original SDK exit")
        await manager.close()

    asyncio.run(scenario())


def test_original_typed_empty_terminal_reply_remains_known_result_not_fake_unknown(tmp_path):
    async def scenario():
        events = []
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]))
        result = await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert result.success and result.application_view["content"] == []
        assert not manager.execution_failed and not manager.publication_failed and not manager.cleanup_failed
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["normalize", "audit_sync", "audit_async", "audit_cancel", "serialize"])
def test_original_known_reply_publication_failure_stops_same_owner_without_fake_remote_or_cleanup_fault(
    tmp_path, monkeypatch, phase
):
    from doppel_agent.mcp.client_manager import MCPPublicationError

    async def scenario():
        events = []
        reply = types.CallToolResult(content=[types.TextContent(text="known reply")])
        if phase == "serialize":
            reply.structured_content = {"value": float("nan")}

        def sync_failure(_payload):
            raise OSError("PRIVATE_PUBLISH_FAILURE")

        async def async_failure(_payload):
            raise OSError("PRIVATE_PUBLISH_FAILURE")

        async def cancelled(_payload):
            raise asyncio.CancelledError()

        audits = {"audit_sync": sync_failure, "audit_async": async_failure, "audit_cancel": cancelled}
        manager, executor = setup(tmp_path, events, reply, audit=audits.get(phase))
        if phase == "normalize":

            def normalize(*_args):
                raise ValueError("PRIVATE_PUBLISH_FAILURE")

            monkeypatch.setattr(executor, "_normalize", normalize)
        original = await manager.get("demo")
        with pytest.raises(MCPPublicationError, match="^mcp_publication_unresolved$"):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert manager.publication_failed and not manager.execution_failed and not manager.cleanup_failed
        assert events.count("original SDK reply") == 1 and "remote unknown" not in events
        with pytest.raises(MCPPublicationError):
            await manager.get("demo")
        with pytest.raises(MCPPublicationError):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="two")
        assert manager._clients["demo"] is original and events.count("original SDK reply") == 1
        await manager.close()
        closing = manager._close_task
        await manager.close()
        assert closing is manager._close_task and manager.publication_failed and not manager.cleanup_failed
        assert events.index("local publication unknown") < events.index("original SDK exit")

    asyncio.run(scenario())


def test_original_ledger_finish_late_failure_after_actual_commit_is_not_retry_or_remote_unknown(
    tmp_path, monkeypatch
):
    from doppel_agent.mcp.client_manager import MCPPublicationError

    async def scenario():
        events = []
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]), ledger=ledger)
        original_finish = ledger._finish

        def late_failure(*args, **kwargs):
            original_finish(*args, **kwargs)
            events.append("original commit returned")
            raise OSError("PRIVATE_LEDGER_FINISH")

        monkeypatch.setattr(ledger, "_finish", late_failure)
        with pytest.raises(MCPPublicationError):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        import sqlite3
        from contextlib import closing

        # Independent disposable row oracle, not failed-ledger readmission or
        # another connection as proof the original SQL handle was closed.
        with closing(sqlite3.connect(ledger.database)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT status,result FROM tool_executions").fetchone()
            assert row["status"] == "completed" and json.loads(row["result"])["success"] is True
        assert manager.publication_failed and not manager.execution_failed and not manager.cleanup_failed
        assert events.count("original SDK reply") == 1
        await manager.close()

    asyncio.run(scenario())


def test_original_entered_audit_repeated_caller_cancel_stops_owner_before_same_audit_drain(tmp_path):
    from doppel_agent.mcp.client_manager import MCPPublicationError

    async def scenario():
        entered, release, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def audit(_payload):
            entered.set()
            await release.wait()
            drained.set()

        events = []
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]), audit=audit)
        pending = asyncio.create_task(
            executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            async with asyncio.timeout(2):
                while not manager.publication_failed:
                    await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done() and not drained.is_set()
            assert not manager.execution_failed and not manager.cleanup_failed
            assert events.count("original SDK reply") == 1
        finally:
            release.set()
        with pytest.raises(MCPPublicationError):
            await pending
        assert drained.is_set()
        await manager.close()

    asyncio.run(scenario())


def test_original_committed_reservation_cancel_fences_before_same_worker_drain_without_sdk_entry(
    tmp_path, monkeypatch
):
    from doppel_agent.mcp.client_manager import MCPPublicationError

    async def scenario():
        events = []
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]), ledger=ledger)
        committed = threading.Event()
        release = threading.Event()
        drained = threading.Event()
        original_begin = ledger._begin

        def gated(*args):
            result = original_begin(*args)
            committed.set()
            assert release.wait(3)
            drained.set()
            return result

        monkeypatch.setattr(ledger, "_begin", gated)
        pending = asyncio.create_task(
            executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        )
        try:
            async with asyncio.timeout(2):
                while not committed.is_set():
                    await asyncio.sleep(0)
            pending.cancel()
            async with asyncio.timeout(2):
                while not manager.publication_failed:
                    await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done() and not drained.is_set() and not manager.execution_failed
            assert "original SDK reply" not in events and not manager.cleanup_failed
            with pytest.raises(MCPPublicationError):
                await manager.get("demo")
        finally:
            release.set()
        with pytest.raises(MCPPublicationError):
            await pending
        assert drained.is_set()
        assert ledger.failed
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(ledger.database)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT status,result FROM tool_executions").fetchone()
            assert row["status"] == "running" and row["result"] == ""
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "forged", ["type", "scope", "duplicate", "running", "budget", "exponent", "surrogate"]
)
def test_original_replay_missing_or_invalid_result_refuses_new_effect_without_fabricated_remote_entry(
    tmp_path, forged
):
    from doppel_agent.mcp.client_manager import MCPPublicationError

    async def scenario():
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        ledger._begin("offline", "one", "mcp__demo__write", {})
        if forged != "running":
            value = {
                "success": True,
                "logical_name": "mcp__demo__write",
                "model_view": "known",
                "application_view": {"content": [], "structured_content": None, "is_error": False},
                "replayed": False,
                "audit_id": "a" * 32,
                "error": None,
            }
            if forged == "type":
                value["success"] = "true"
            if forged == "scope":
                value["logical_name"] = "mcp__other__write"
            if forged == "exponent":
                value["application_view"]["structured_content"] = {"x": 0}
            if forged == "surrogate":
                value["model_view"] = "\ud800"
            payload = json.dumps(value)
            if forged == "exponent":
                payload = payload.replace('"x": 0', '"x": 1e999')
            if forged == "duplicate":
                payload = payload[:-1] + ',"success":false}'
            if forged == "budget":
                payload = "x" * (8 * 1024 * 1024 + 1)
            ledger._finish("offline", "one", result=payload)
        events = []
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]), ledger=ledger)
        with pytest.raises(MCPPublicationError):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert manager.publication_failed and not manager.execution_failed and not manager.cleanup_failed
        assert "original SDK reply" not in events
        await manager.close()

    asyncio.run(scenario())


def test_original_ledger_known_input_conflict_is_not_fabricated_unknown_receipt(tmp_path):
    async def scenario():
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        ledger._begin("offline", "one", "mcp__other__write", {})
        events = []
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]), ledger=ledger)
        with pytest.raises(ValueError, match="different input"):
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert not manager.publication_failed and not manager.execution_failed and not manager.cleanup_failed
        assert "original SDK reply" not in events
        await manager.close()

    asyncio.run(scenario())


def test_original_ledger_setup_failure_still_closes_same_connection(tmp_path, monkeypatch):
    import doppel_agent.persistence.tool_ledger as module
    from doppel_agent.persistence.tool_ledger import ToolLedgerPersistenceError

    events = []

    class Connection:
        def execute(self, _sql):
            events.append("original setup")
            raise OSError("PRIVATE_PRAGMA")

        def close(self):
            events.append("original close")

    original = Connection()
    monkeypatch.setattr(module.sqlite3, "connect", lambda *_args, **_kwargs: original)
    # SAME setup failure/close input, now actual constructor initializes lifetime
    # before original setup. No synthetic incomplete object bypasses that source.
    with pytest.raises(ToolLedgerPersistenceError):
        ToolExecutionLedger(tmp_path / "fixture-only.sqlite3")
    assert events == ["original setup", "original close"]


def test_original_known_previous_failure_refuses_replay_without_echoing_untrusted_stored_error(tmp_path):
    async def scenario():
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        ledger._begin("offline", "one", "mcp__demo__write", {})
        ledger._finish("offline", "one", error="PRIVATE_PREVIOUS_LEDGER_ERROR")
        events = []
        manager, executor = setup(tmp_path, events, types.CallToolResult(content=[]), ledger=ledger)
        with pytest.raises(ValueError) as failure:
            await executor.execute("mcp__demo__write", {}, run_id="offline", tool_call_id="one")
        assert str(failure.value) == "previous tool execution failed"
        assert not manager.publication_failed and not manager.execution_failed and not manager.cleanup_failed
        assert "original SDK reply" not in events
        await manager.close()

    asyncio.run(scenario())


def test_original_core_bridge_known_reply_overflow_retains_same_closed_manager_and_stops_next_model(
    tmp_path, monkeypatch
):
    import doppel_agent.mcp_bridge as module
    from doppel_agent.core import Core
    from doppel_agent.mcp.client_manager import MCPPublicationError
    from doppel_agent.provider import ModelTurn, ToolCall

    events = []
    managers = []
    turns = []
    faults = []
    original_manager = module.MCPClientManager

    def manager(config, **kwargs):
        instance = original_manager(
            config,
            connector=connector_for(
                events, types.CallToolResult(content=[types.TextContent(text="x" * (65 * 1024))])
            ),
            **kwargs,
        )
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

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: configuration(tmp_path))
    monkeypatch.setattr(module, "MCPClientManager", manager)
    core = Core(
        tmp_path,
        Provider(),
        allow_mcp=True,
        approver=lambda *_args: True,
        mcp_publication_failure=lambda: faults.append("same owner"),
    )
    result = core.run("offline fixture")
    assert result["status"] == "failed" and "mcp_publication_unresolved" in result["answer"]
    assert core.mcp_publication_failed and not core.mcp_execution_failed and not core.mcp_cleanup_failed
    assert core._mcp_bridge._manager is managers[0] and faults == ["same owner"]
    assert managers[0]._close_task.done() and managers[0]._close_task.exception() is None
    with pytest.raises(MCPPublicationError):
        core.run("forbidden reuse")
    assert turns == ["original turn"] and len(managers) == 1 and events.count("original SDK reply") == 1


def test_original_api_publication_unknown_fixed_503_no_store_without_runtime_start(tmp_path):
    import httpx
    from doppel_agent.api.app import create_app
    from doppel_agent.mcp.client_manager import MCPPublicationError

    async def scenario():
        app = create_app(tmp_path)

        @app.get("/fixture-mcp-publication")
        async def unknown():
            raise MCPPublicationError()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/fixture-mcp-publication")
        assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
        assert response.json() == {"error": "mcp_publication_unresolved"}

    asyncio.run(scenario())
