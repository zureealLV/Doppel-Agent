"""C2c2g4 FIRST SAME original MCP call/session lifetime definitions, ALL UNRUN.

These coroutine/context/connector observations do not prove hidden SDK request,
cancel-scope, native process/port, outer owner or provider billing closure.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from doppel_agent.mcp.client_manager import MCPAdmissionError, MCPClientManager, MCPCleanupError
from doppel_agent.mcp.config import MCPConfig, MCPServerConfig


def manager_fixture(path, *, maximum=1):
    events = []
    sessions = []

    @asynccontextmanager
    async def connector(_server):
        session = SimpleNamespace(identity=len(sessions) + 1)
        sessions.append(session)
        events.append(("open", session.identity, asyncio.current_task()))
        try:
            yield session, SimpleNamespace(capabilities={})
        finally:
            events.append(("exit", session.identity, asyncio.current_task()))

    server = MCPServerConfig(
        "demo", "streamable_http", url="http://127.0.0.1/offline", max_concurrency=maximum
    )
    manager = MCPClientManager(MCPConfig({"demo": server}, path / "offline-config.json"), connector=connector)
    return manager, sessions, events


class ObservedSemaphore:
    """Same original semaphore; signal actual pending acquire, not elapsed time."""

    def __init__(self, original):
        self.original = original
        self.waiting = asyncio.Event()

    async def __aenter__(self):
        self.waiting.set()
        return await self.original.__aenter__()

    async def __aexit__(self, *exc):
        return await self.original.__aexit__(*exc)


def test_original_close_waits_exact_entered_call_before_same_sdk_exit_and_refuses_new_call(tmp_path):
    async def scenario():
        manager, sessions, events = manager_fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        operation_events = []

        async def operation(session):
            operation_events.append(("entered", session.identity))
            entered.set()
            try:
                await release.wait()
                return "original return"
            finally:
                operation_events.append(("finished", session.identity))

        running = asyncio.create_task(manager.call("demo", operation))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.wait_for(original.release.wait(), 2)
            assert not closing.done() and not original.task.done() and not original.operations_idle.is_set()
            assert running in original.borrowers and manager._connections["demo"] is original
            assert [item[0] for item in events] == ["open"]
            calls = []

            async def forbidden(_session):
                calls.append("forbidden")

            with pytest.raises(MCPAdmissionError, match="^mcp_admission_closed$"):
                await manager.call("demo", forbidden)
            assert calls == [] and len(sessions) == 1 and not manager.cleanup_failed
        finally:
            release.set()
        assert await asyncio.wait_for(running, 2) == "original return"
        await asyncio.wait_for(closing, 2)
        assert operation_events == [("entered", 1), ("finished", 1)]
        assert events[0][2] is events[1][2] is original.task
        assert (
            original.borrowers == {} and original.operations_idle.is_set() and original.lifetime.known_closed
        )
        assert manager._connections == {} and not manager.cleanup_failed

    asyncio.run(scenario())


def test_original_direct_session_body_finally_is_part_of_lease_not_only_semaphore_return(tmp_path):
    async def scenario():
        manager, _sessions, events = manager_fixture(tmp_path)
        entered, body_release, cleanup_entered, cleanup_release = (asyncio.Event() for _ in range(4))

        async def original_body():
            async with manager.session("demo"):
                entered.set()
                try:
                    await body_release.wait()
                finally:
                    cleanup_entered.set()
                    await cleanup_release.wait()

        running = asyncio.create_task(original_body())
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.wait_for(original.release.wait(), 2)
            body_release.set()
            await asyncio.wait_for(cleanup_entered.wait(), 2)
            assert running in original.borrowers and not closing.done() and len(events) == 1
            assert not original.operations_idle.is_set() and not original.task.done()
        finally:
            body_release.set()
            cleanup_release.set()
        await asyncio.wait_for(running, 2)
        await asyncio.wait_for(closing, 2)
        assert len(events) == 2 and original.borrowers == {} and original.lifetime.known_closed

    asyncio.run(scenario())


def test_original_invalidate_drains_call_and_stale_semaphore_waiter_cannot_reconnect_or_enter_replacement(
    tmp_path,
):
    async def scenario():
        manager, sessions, events = manager_fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def active(session):
            calls.append(("active", session.identity))
            entered.set()
            await release.wait()
            return "original"

        running = asyncio.create_task(manager.call("demo", active))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        managed = manager._clients["demo"]
        observed = ObservedSemaphore(managed.semaphore)
        managed.semaphore = observed

        async def forbidden(session):
            calls.append(("forbidden", session.identity))

        waiting = asyncio.create_task(manager.call("demo", forbidden))
        await asyncio.wait_for(observed.waiting.wait(), 2)
        invalidating = asyncio.create_task(manager.invalidate("demo"))
        try:
            await asyncio.wait_for(original.release.wait(), 2)
            assert not managed.valid and not invalidating.done() and len(events) == 1
            assert manager._connections["demo"] is original and running in original.borrowers
            assert waiting not in original.borrowers
        finally:
            release.set()
        assert await asyncio.wait_for(running, 2) == "original"
        with pytest.raises(MCPAdmissionError):
            await asyncio.wait_for(waiting, 2)
        await asyncio.wait_for(invalidating, 2)
        assert calls == [("active", 1)] and len(sessions) == 1 and not manager.cleanup_failed
        fresh = await manager.get("demo")
        replacement = manager._connections["demo"]
        assert fresh is not managed and replacement is not original and manager.generation("demo") == 2
        assert [item[:2] for item in events] == [("open", 1), ("exit", 1), ("open", 2)]
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["close", "invalidate"])
def test_original_repeated_cancelled_closer_waits_same_active_lease_preserves_known_exit_cancellation(
    tmp_path, action
):
    async def scenario():
        manager, _sessions, events = manager_fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def operation(_session):
            entered.set()
            await release.wait()
            return "original"

        running = asyncio.create_task(manager.call("demo", operation))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        closing = asyncio.create_task(manager.close() if action == "close" else manager.invalidate("demo"))
        try:
            await asyncio.wait_for(original.release.wait(), 2)
            closing.cancel()
            await asyncio.sleep(0)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done() and manager._connections["demo"] is original and len(events) == 1
            assert running in original.borrowers and not original.task.done()
        finally:
            release.set()
        assert await asyncio.wait_for(running, 2) == "original"
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
        assert not manager.cleanup_failed and original.borrowers == {} and original.lifetime.known_closed
        assert manager._connections == {} and len(events) == 2
        await manager.close()
        assert len(events) == 2

    asyncio.run(scenario())


def test_original_cancelled_call_body_cleanup_finishes_before_lease_release_and_sdk_exit(tmp_path):
    async def scenario():
        manager, _sessions, events = manager_fixture(tmp_path)
        entered, cancelled_cleanup, allow_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def operation(_session):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled_cleanup.set()
                await allow_cleanup.wait()

        running = asyncio.create_task(manager.call("demo", operation))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        running.cancel()
        await asyncio.wait_for(cancelled_cleanup.wait(), 2)
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.wait_for(original.release.wait(), 2)
            assert (
                not running.done()
                and not closing.done()
                and running in original.borrowers
                and len(events) == 1
            )
        finally:
            allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, 2)
        await asyncio.wait_for(closing, 2)
        assert original.borrowers == {} and len(events) == 2
        # Source-level context completion only, not hidden cancelled SDK work proof.

    asyncio.run(scenario())


def test_original_failed_call_releases_lease_before_invalidation_waits_sibling_then_known_retry(tmp_path):
    async def scenario():
        manager, sessions, events = manager_fixture(tmp_path, maximum=2)
        sibling_entered, sibling_release, failed_entered, fail = (asyncio.Event() for _ in range(4))
        invocations = []

        async def sibling(_session):
            sibling_entered.set()
            await sibling_release.wait()
            return "sibling"

        async def operation(session):
            invocations.append(session.identity)
            if len(invocations) == 1:
                failed_entered.set()
                await fail.wait()
                raise ValueError("offline original operation failure")
            return "healthy source retry"

        other = asyncio.create_task(manager.call("demo", sibling))
        await asyncio.wait_for(sibling_entered.wait(), 2)
        running = asyncio.create_task(manager.call("demo", operation))
        await asyncio.wait_for(failed_entered.wait(), 2)
        original = manager._connections["demo"]
        try:
            fail.set()
            await asyncio.wait_for(original.release.wait(), 2)
            assert other in original.borrowers and running not in original.borrowers
            assert not running.done() and not original.task.done() and len(sessions) == 1 and len(events) == 1
        finally:
            fail.set()
            sibling_release.set()
        assert await asyncio.wait_for(other, 2) == "sibling"
        assert await asyncio.wait_for(running, 2) == "healthy source retry"
        assert invocations == [1, 2] and original.lifetime.known_closed and not manager.cleanup_failed
        assert [item[:2] for item in events] == [("open", 1), ("exit", 1), ("open", 2)]
        await manager.close()

    asyncio.run(scenario())


def test_actual_call_error_invalidation_is_bound_to_original_managed_identity_not_new_generation(
    tmp_path, monkeypatch
):
    async def scenario():
        manager, sessions, events = manager_fixture(tmp_path)
        original_invalidate = manager.invalidate
        requested, allow_invalidation = asyncio.Event(), asyncio.Event()
        expected_values = []

        async def gated(name, *, expected=None):
            expected_values.append(expected)
            requested.set()
            await allow_invalidation.wait()
            await original_invalidate(name, expected=expected)

        monkeypatch.setattr(manager, "invalidate", gated)
        original_managed = await manager.get("demo")
        original = manager._connections["demo"]

        async def operation(_session):
            raise ValueError("offline original operation failure")

        running = asyncio.create_task(manager.call("demo", operation, reconnect=False))
        try:
            await asyncio.wait_for(requested.wait(), 2)
            assert original.borrowers == {} and expected_values == [original_managed]
            await original_invalidate("demo")
            fresh = await manager.get("demo")
            replacement = manager._connections["demo"]
            assert fresh is not original_managed
        finally:
            allow_invalidation.set()
        with pytest.raises(ValueError, match="^offline original operation failure$"):
            await asyncio.wait_for(running, 2)
        assert manager._connections["demo"] is replacement and fresh.valid and len(sessions) == 2
        assert [item[:2] for item in events] == [("open", 1), ("exit", 1), ("open", 2)]
        await manager.close()

    asyncio.run(scenario())


def test_original_pending_semaphore_cancel_never_acquires_lease_or_calls_sdk(tmp_path):
    async def scenario():
        manager, _sessions, events = manager_fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        forbidden_calls = []

        async def operation(_session):
            entered.set()
            await release.wait()

        running = asyncio.create_task(manager.call("demo", operation))
        await asyncio.wait_for(entered.wait(), 2)
        managed = manager._clients["demo"]
        original = manager._connections["demo"]
        observed = ObservedSemaphore(managed.semaphore)
        managed.semaphore = observed

        async def forbidden(_session):
            forbidden_calls.append("forbidden")

        pending = asyncio.create_task(manager.call("demo", forbidden))
        try:
            await asyncio.wait_for(observed.waiting.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 2)
            assert (
                forbidden_calls == [] and pending not in original.borrowers and running in original.borrowers
            )
            assert len(events) == 1 and not manager.cleanup_failed
        finally:
            release.set()
        await asyncio.wait_for(running, 2)
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["get", "close", "invalidate", "session"])
def test_original_reentrant_lifecycle_refuses_self_wait_without_new_task_fault_or_sdk_exit(tmp_path, action):
    async def scenario():
        manager, _sessions, events = manager_fixture(tmp_path)
        async with manager.session("demo"):
            original = manager._connections["demo"]
            with pytest.raises(MCPAdmissionError, match="^mcp_admission_closed$"):
                if action == "close":
                    await manager.close()
                elif action == "invalidate":
                    await manager.invalidate("demo")
                elif action == "get":
                    await manager.get("demo")
                else:
                    async with manager.session("demo"):
                        raise AssertionError("forbidden nested entry")
            assert manager._close_task is None and not manager._closed and not original.release.is_set()
            assert (
                not manager.cleanup_failed
                and len(events) == 1
                and asyncio.current_task() in original.borrowers
            )
        assert original.borrowers == {}
        await manager.close()
        assert original.lifetime.known_closed and len(events) == 2

    asyncio.run(scenario())


def test_original_pending_semaphore_cleanup_fault_refuses_entry_and_does_not_retry(tmp_path):
    async def scenario():
        manager, sessions, _events = manager_fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def active(_session):
            entered.set()
            await release.wait()
            return "late raw value"

        running = asyncio.create_task(manager.call("demo", active))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        managed = manager._clients["demo"]
        observed = ObservedSemaphore(managed.semaphore)
        managed.semaphore = observed

        async def forbidden(_session):
            calls.append("forbidden")

        waiting = asyncio.create_task(manager.call("demo", forbidden))
        try:
            await asyncio.wait_for(observed.waiting.wait(), 2)
            manager._mark_cleanup_failed()  # Same monotonic fault fixture, no SDK effect.
        finally:
            release.set()
        with pytest.raises(MCPCleanupError):
            await asyncio.wait_for(running, 2)
        with pytest.raises(MCPCleanupError):
            await asyncio.wait_for(waiting, 2)
        assert calls == [] and len(sessions) == 1 and original.borrowers == {}
        with pytest.raises(MCPCleanupError):
            await manager.close()

    asyncio.run(scenario())


def test_original_api_known_mcp_admission_refusal_is_fixed_503_no_store_not_cleanup_fault(tmp_path):
    import httpx
    from doppel_agent.api.app import create_app

    async def scenario():
        app = create_app(tmp_path)

        @app.get("/fixture-mcp-admission")
        async def refused():
            raise MCPAdmissionError()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/fixture-mcp-admission")
        assert response.status_code == 503 and response.json() == {"error": "mcp_admission_closed"}
        assert response.headers["Cache-Control"] == "no-store" and not app.state.run_service._owner.held
        await app.state.run_service.close()

    asyncio.run(scenario())


def test_actual_executor_known_admission_refusal_not_ordinary_transport_error_or_success_audit(
    tmp_path, monkeypatch
):
    from doppel_agent.mcp.executor import MCPToolExecutor
    from doppel_agent.mcp.types import MCPToolDescriptor

    async def scenario():
        manager, sessions, events = manager_fixture(tmp_path)
        await manager.close()
        original_close = manager._close_task
        descriptor = MCPToolDescriptor("mcp__demo__offline", "demo", "offline", "", "", {"type": "object"})

        class Catalog:
            async def get(self, _name):
                return descriptor

            def source_for_descriptor(self, _descriptor):
                return None  # Synthetic descriptor; preserves original closed-manager gate only.

        permissions = SimpleNamespace(check=lambda *_args: SimpleNamespace(allowed=True))
        audits = []
        executor = MCPToolExecutor(manager, Catalog(), permissions, audit=audits.append)

        def forbidden(*_args):
            raise AssertionError("admission refusal must not normalize success")

        monkeypatch.setattr(executor, "_normalize", forbidden)
        with pytest.raises(MCPAdmissionError, match="^mcp_admission_closed$"):
            await executor.execute(descriptor.logical_name, {}, run_id="offline", tool_call_id="one")
        assert not sessions and not events and not audits and not manager.cleanup_failed
        assert manager._close_task is original_close

    asyncio.run(scenario())


def test_original_entered_call_error_during_global_close_is_preserved_without_retry_or_extra_sdk_owner(
    tmp_path,
):
    async def scenario():
        manager, sessions, events = manager_fixture(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def operation(_session):
            calls.append("original")
            entered.set()
            await release.wait()
            raise ValueError("offline original operation failure")

        running = asyncio.create_task(manager.call("demo", operation))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.wait_for(original.release.wait(), 2)
            assert not closing.done() and running in original.borrowers and len(events) == 1
        finally:
            release.set()
        with pytest.raises(ValueError, match="^offline original operation failure$"):
            await asyncio.wait_for(running, 2)
        await asyncio.wait_for(closing, 2)
        assert (
            calls == ["original"] and len(sessions) == 1 and len(events) == 2 and not manager.cleanup_failed
        )

    asyncio.run(scenario())


def test_original_close_during_accepted_startup_drains_same_owner_without_publishing_managed_session(
    tmp_path,
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        events = []

        @asynccontextmanager
        async def connector(_server):
            events.append(("enter", asyncio.current_task()))
            entered.set()
            await release.wait()
            try:
                yield object(), SimpleNamespace(capabilities={})
            finally:
                events.append(("exit", asyncio.current_task()))

        server = MCPServerConfig("demo", "streamable_http", url="http://127.0.0.1/offline")
        manager = MCPClientManager(
            MCPConfig({"demo": server}, tmp_path / "offline-config.json"), connector=connector
        )
        opening = asyncio.create_task(manager.get("demo"))
        await asyncio.wait_for(entered.wait(), 2)
        original = manager._connections["demo"]
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.sleep(0)  # Let original close seal admission/create its SAME close task.
            assert manager._closed and not opening.done() and not closing.done()
            assert manager._connections["demo"] is original and len(events) == 1
        finally:
            release.set()
        with pytest.raises(MCPAdmissionError):
            await asyncio.wait_for(opening, 2)
        await asyncio.wait_for(closing, 2)
        assert manager.generation("demo") == 0 and manager._clients == {} and manager._connections == {}
        assert events[0][1] is events[1][1] is original.task and original.lifetime.known_closed
        assert not manager.cleanup_failed and original.borrowers == {}

    asyncio.run(scenario())
