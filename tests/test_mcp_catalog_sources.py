"""C2c2g5a FIRST original SDK discovery/probe/tool source bindings, ALL UNRUN.

Ephemeral source identity is not a durable execution/provider/billing receipt,
and fixture coroutine/SDK types do not prove native/physical transport closure.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp import types

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.client_manager import MCPAdmissionError, MCPClientManager
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig


def tool(name):
    return types.Tool(name=name, inputSchema={"type": "object", "properties": {}})


def fixture(path, *, transient=False):
    sessions = []
    events = []

    class Session:
        def __init__(self, identity):
            self.identity = identity
            self.tools = [tool("old" if identity == 1 else "new")]
            self.list_calls = 0
            self.tool_calls = []

        async def list_tools(self, *, params=None):
            self.list_calls += 1
            if transient and self.identity == 1 and self.list_calls == 1:
                raise ConnectionError("offline read failure")
            return types.ListToolsResult(tools=self.tools)

        async def call_tool(self, name, arguments=None):
            self.tool_calls.append((name, arguments))
            return types.CallToolResult(content=[types.TextContent(text="offline result")])

    @asynccontextmanager
    async def connector(_server):
        session = Session(len(sessions) + 1)
        sessions.append(session)
        events.append(("open", session.identity, asyncio.current_task()))
        try:
            yield (
                session,
                SimpleNamespace(
                    protocol_version="offline",
                    capabilities={},
                    server_info=SimpleNamespace(name="same-server", version="same-version"),
                ),
            )
        finally:
            events.append(("exit", session.identity, asyncio.current_task()))

    config = MCPConfig(
        {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")},
        path / "offline-config.json",
    )
    manager = MCPClientManager(config, connector=connector)
    return manager, MCPToolCatalog(manager), sessions, events


def test_actual_call_observation_binds_result_to_same_original_session_metadata_and_generation(tmp_path):
    async def scenario():
        manager, _catalog, sessions, events = fixture(tmp_path)

        async def operation(session):
            return session.identity

        observation = await manager.call("demo", operation, with_source=True)
        original = manager._clients["demo"]
        connection = manager._connections["demo"]
        assert observation.value == 1 and observation.source.managed is original
        assert observation.source.metadata is original.metadata and observation.source.generation == 1
        assert (
            observation.source.cache_key == original.metadata.cache_key and observation.source.name == "demo"
        )
        manager.check_source(observation.source)
        assert connection.borrowers == {} and len(sessions) == 1 and len(events) == 1
        await manager.close()
        with pytest.raises(MCPAdmissionError):
            manager.check_source(observation.source)
        assert len(events) == 2 and len(sessions) == 1 and not manager.cleanup_failed

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["catalog", "health"])
def test_actual_returned_old_discovery_or_probe_cannot_be_paired_with_later_replacement_metadata(
    tmp_path, monkeypatch, kind
):
    async def scenario():
        manager, catalog, sessions, events = fixture(tmp_path)
        original_call = manager.call
        returned, release = asyncio.Event(), asyncio.Event()
        observations = []

        async def gated(name, operation, **kwargs):
            result = await original_call(name, operation, **kwargs)
            observations.append(result)
            returned.set()
            await release.wait()
            return result

        monkeypatch.setattr(manager, "call", gated)
        pending = asyncio.create_task(
            catalog.list_server("demo") if kind == "catalog" else manager.health("demo")
        )
        try:
            await asyncio.wait_for(returned.wait(), 2)
            old = manager._clients["demo"]
            await manager.invalidate("demo")
            fresh = await manager.get("demo")
            replacement = manager._connections["demo"]
            assert fresh is not old and manager.generation("demo") == 2 and len(sessions) == 2
        finally:
            release.set()
        with pytest.raises(MCPAdmissionError):
            await asyncio.wait_for(pending, 2)
        assert observations[0].source.managed is old and observations[0].source.generation == 1
        assert manager._connections["demo"] is replacement and fresh.valid and sessions[1].list_calls == 0
        assert catalog._cache == {} and catalog._logical == {} and catalog._sources == {}
        assert [item[:2] for item in events] == [("open", 1), ("exit", 1), ("open", 2)]
        await manager.close()

    asyncio.run(scenario())


def test_actual_original_read_retry_binds_successful_second_source_not_first_get_generation(tmp_path):
    async def scenario():
        manager, catalog, sessions, events = fixture(tmp_path, transient=True)
        descriptors = await catalog.list_server("demo")
        assert [descriptor.remote_name for descriptor in descriptors] == ["new"]
        source = catalog.source_for_descriptor(descriptors[0])
        assert source.generation == 2 and source.managed.session is sessions[1]
        assert catalog._cache["demo"][1] == 2 and catalog._cache["demo"][0] == source.cache_key
        assert [session.list_calls for session in sessions] == [1, 1] and not manager.cleanup_failed
        assert [item[:2] for item in events] == [("open", 1), ("exit", 1), ("open", 2)]
        await manager.close()

    asyncio.run(scenario())


def test_actual_expected_source_retry_cannot_allocate_replacement_after_original_known_invalidation(tmp_path):
    async def scenario():
        manager, _catalog, sessions, events = fixture(tmp_path)

        async def read(session):
            return session.identity

        observed = await manager.call("demo", read, with_source=True)
        calls = []

        async def fail(_session):
            calls.append("original read")
            raise ConnectionError("offline original read failure")

        with pytest.raises(MCPAdmissionError):
            await manager.call("demo", fail, reconnect=True, expected_source=observed.source)
        assert calls == ["original read"] and len(sessions) == 1 and manager._connections == {}
        assert [item[:2] for item in events] == [("open", 1), ("exit", 1)] and not manager.cleanup_failed
        await manager.close()

    asyncio.run(scenario())


def test_actual_cached_logical_lookup_refreshes_source_and_removes_renamed_same_schema_tool(tmp_path):
    async def scenario():
        manager, catalog, sessions, _events = fixture(tmp_path)
        old = (await catalog.list_server("demo"))[0]
        old_source = catalog.source_for_descriptor(old)
        await manager.invalidate("demo")
        with pytest.raises(KeyError):
            await catalog.get(old.logical_name)
        fresh = await catalog.get("mcp__demo__new")
        assert fresh.schema_hash == old.schema_hash and old.logical_name not in catalog._logical
        assert catalog.source_for_descriptor(fresh).generation == 2
        assert catalog._sources["demo"] is not old_source and [
            session.list_calls for session in sessions
        ] == [1, 1]
        with pytest.raises(MCPAdmissionError):
            catalog.source_for_descriptor(old)
        assert all(not session.tool_calls for session in sessions)
        await manager.close()

    asyncio.run(scenario())


def test_actual_invalid_refresh_retains_historical_maps_but_never_grants_old_descriptor_invocation(tmp_path):
    async def scenario():
        manager, catalog, sessions, _events = fixture(tmp_path)
        old = (await catalog.list_server("demo"))[0]
        cache = catalog._cache["demo"]
        logical = catalog._logical
        source = catalog._sources["demo"]
        sessions[0].tools = [tool("collision name"), tool("collision_name")]
        with pytest.raises(ValueError, match="collision"):
            await catalog.list_server("demo", refresh=True)
        assert (
            catalog._cache["demo"] is cache
            and catalog._logical is logical
            and catalog._sources["demo"] is source
        )
        # Existing memory-only historical projection is NOT invocation permission.
        assert catalog.cached_server("demo") == ("cached", cache[2])
        with pytest.raises(MCPAdmissionError):
            catalog.source_for_descriptor(old)
        assert manager.generation("demo") == 1 and not manager.cleanup_failed and not sessions[0].tool_calls
        sessions[0].tools = [tool("healthy")]
        fresh = (await catalog.list_server("demo"))[0]
        assert old.logical_name not in catalog._logical and fresh.remote_name == "healthy"
        catalog.source_for_descriptor(fresh)
        await manager.close()

    asyncio.run(scenario())


def test_actual_original_catalog_serializes_same_server_refresh_and_keeps_active_refresh_unavailable(
    tmp_path, monkeypatch
):
    async def scenario():
        manager, catalog, sessions, _events = fixture(tmp_path)
        old = (await catalog.list_server("demo"))[0]
        first_entered, second_entered, first_release, second_release = (asyncio.Event() for _ in range(4))
        requests = []

        async def pages(*, params=None):
            requests.append("original discovery")
            ordinal = len(requests)
            if ordinal == 1:
                first_entered.set()
                await first_release.wait()
                return types.ListToolsResult(tools=[tool("middle")])
            second_entered.set()
            await second_release.wait()
            return types.ListToolsResult(tools=[tool("final")])

        monkeypatch.setattr(sessions[0], "list_tools", pages)
        first = asyncio.create_task(catalog.list_server("demo", refresh=True))
        await asyncio.wait_for(first_entered.wait(), 2)
        original_lock = catalog._locks["demo"]
        pending_lock = asyncio.Event()

        class Lock:
            async def __aenter__(self):
                pending_lock.set()
                return await original_lock.__aenter__()

            async def __aexit__(self, *exc):
                return await original_lock.__aexit__(*exc)

        catalog._locks["demo"] = Lock()  # Observe same original lock, not a new executor/discovery.
        second = asyncio.create_task(catalog.list_server("demo", refresh=True))
        try:
            await asyncio.wait_for(pending_lock.wait(), 2)
            assert not second_entered.is_set() and not second.done() and requests == ["original discovery"]
            with pytest.raises(MCPAdmissionError):
                catalog.source_for_descriptor(old)
            first_release.set()
            middle = (await asyncio.wait_for(first, 2))[0]
            await asyncio.wait_for(second_entered.wait(), 2)
            assert not second.done() and middle.logical_name in catalog._logical
            with pytest.raises(MCPAdmissionError):
                catalog.source_for_descriptor(middle)
        finally:
            first_release.set()
            second_release.set()
        fresh = (await asyncio.wait_for(second, 2))[0]
        assert fresh.remote_name == "final" and old.logical_name not in catalog._logical
        assert "mcp__demo__middle" not in catalog._logical and requests == ["original discovery"] * 2
        catalog.source_for_descriptor(fresh)
        assert len(sessions) == 1 and not manager.cleanup_failed and not sessions[0].tool_calls
        await manager.close()

    asyncio.run(scenario())


def test_actual_same_source_refresh_failure_cannot_invoke_descriptor_selected_before_ledger_wait(
    tmp_path, monkeypatch
):
    from doppel_agent.mcp.executor import MCPToolExecutor
    from doppel_agent.permissions import PermissionManager
    from doppel_agent.persistence.tool_ledger import ToolExecutionLedger

    async def scenario():
        manager, catalog, sessions, _events = fixture(tmp_path)
        old = (await catalog.list_server("demo"))[0]
        audits = []
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        original_begin = ledger._begin
        reserved = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def begin(*args):
            result = original_begin(*args)  # SAME original durable running row.
            loop.call_soon_threadsafe(reserved.set)
            if not release.wait(3):
                raise AssertionError("fixture original ledger gate not released")
            return result

        monkeypatch.setattr(ledger, "_begin", begin)
        executor = MCPToolExecutor(
            manager,
            catalog,
            PermissionManager(frozenset({"mcp_execute"})),
            ledger=ledger,
            audit=audits.append,
        )
        pending = asyncio.create_task(
            executor.execute(old.logical_name, {}, run_id="offline", tool_call_id="one")
        )
        try:
            await asyncio.wait_for(reserved.wait(), 2)
            sessions[0].tools = [tool("collision name"), tool("collision_name")]
            with pytest.raises(ValueError, match="collision"):
                await catalog.list_server("demo", refresh=True)
        finally:
            release.set()
        with pytest.raises(MCPAdmissionError):
            await asyncio.wait_for(pending, 2)
        assert not sessions[0].tool_calls and not audits and not manager.cleanup_failed
        with ledger._connect() as connection:
            row = connection.execute("SELECT status,result,error FROM tool_executions").fetchone()
            assert (
                row["status"] == "failed"
                and row["result"] == ""
                and row["error"] == "MCPAdmissionError: tool execution failed"
            )
        await manager.close()

    asyncio.run(scenario())


def test_actual_original_ledger_wait_cannot_invoke_new_generation_under_old_schema_permission_snapshot(
    tmp_path, monkeypatch
):
    from doppel_agent.mcp.executor import MCPToolExecutor
    from doppel_agent.permissions import PermissionManager
    from doppel_agent.persistence.tool_ledger import ToolExecutionLedger

    async def scenario():
        manager, catalog, sessions, _events = fixture(tmp_path)
        old = (await catalog.list_server("demo"))[0]
        original = manager._connections["demo"]
        audits = []
        ledger = ToolExecutionLedger(tmp_path / "offline-ledger.sqlite3")
        original_begin = ledger._begin
        reserved = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def begin(*args):
            result = original_begin(*args)
            loop.call_soon_threadsafe(reserved.set)
            if not release.wait(3):
                raise AssertionError("fixture original ledger gate not released")
            return result

        monkeypatch.setattr(ledger, "_begin", begin)
        executor = MCPToolExecutor(
            manager,
            catalog,
            PermissionManager(frozenset({"mcp_execute"})),
            ledger=ledger,
            audit=audits.append,
        )
        pending = asyncio.create_task(
            executor.execute(old.logical_name, {}, run_id="offline", tool_call_id="one")
        )
        try:
            await asyncio.wait_for(reserved.wait(), 2)
            await manager.invalidate("demo")
            fresh = await manager.get("demo")
            replacement = manager._connections["demo"]
            assert replacement is not original
        finally:
            release.set()
        with pytest.raises(MCPAdmissionError):
            await asyncio.wait_for(pending, 2)
        assert manager._connections["demo"] is replacement and fresh.valid and len(sessions) == 2
        assert (
            all(not session.tool_calls for session in sessions) and not audits and not manager.cleanup_failed
        )
        with ledger._connect() as connection:
            row = connection.execute("SELECT status,result,error FROM tool_executions").fetchone()
            assert (
                row["status"] == "failed"
                and row["result"] == ""
                and row["error"] == "MCPAdmissionError: tool execution failed"
            )
        await manager.close()

    asyncio.run(scenario())


def test_actual_bridge_known_admission_error_not_downgraded_to_ordinary_value_error(tmp_path, monkeypatch):
    import doppel_agent.mcp_bridge as module
    from doppel_agent.mcp_bridge import MCPBridge

    config = MCPConfig(
        {"demo": MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")},
        tmp_path / "offline-config.json",
    )
    managers = []
    faults = []

    class Manager:
        def __init__(self, *_args, **_kwargs):
            managers.append(self)

        async def close(self):
            pass  # Synthetic known normal close; not SDK drain evidence.

    class Catalog:
        def __init__(self, _manager):
            pass

        async def list_server(self, _name):
            raise MCPAdmissionError()

    monkeypatch.setattr(module, "load_mcp_config", lambda _path: config)
    monkeypatch.setattr(module, "MCPClientManager", Manager)
    monkeypatch.setattr(module, "MCPToolCatalog", Catalog)
    bridge = MCPBridge(tmp_path, failure=lambda: faults.append("forbidden"))
    with pytest.raises(MCPAdmissionError, match="^mcp_admission_closed$"):
        bridge.list_tools({"server": "demo"})
    assert len(managers) == 1 and not bridge.cleanup_failed and bridge._manager is None and not faults
