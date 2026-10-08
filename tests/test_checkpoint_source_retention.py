"""FIRST original checkpoint lifetime definitions, 2026-10-07, ALL UNRUN.

Exact aiosqlite proxy/task/raw-handle references, disposable DB only. Private
fixture disposal AFTER worker drain is not native proof or production recovery.
"""

import asyncio

import aiosqlite
import pytest

from doppel_agent.persistence import checkpoints
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.runtime.graph import GraphRuntime
from doppel_agent.provider import MockProvider


def original_proxy(monkeypatch, phase, *, close_hook=None):
    factory = checkpoints.aiosqlite.connect
    proxies, calls = [], []

    class Proxy:
        def __init__(self, path):
            self.raw = factory(path)
            proxies.append(self)

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def __await__(self):
            async def opening():
                await self.raw
                if phase == "open_opaque":
                    raise OSError("PRIVATE_OPEN_LOST_RETURN")
                return None if phase == "open_none" else self

            return opening().__await__()

        async def close(self):
            calls.append(self)
            if close_hook is not None:
                await close_hook()
            if phase == "close_failed":
                raise OSError("PRIVATE_CHECKPOINT_CLOSE")
            await self.raw.close()
            if phase == "closed_then_throw":
                raise OSError("PRIVATE_CLOSED_CHECKPOINT")

    def connect(path):
        if phase == "factory_opaque":
            Proxy(path)  # Unstarted original proxy; no invented opening/close authority.
            raise OSError("PRIVATE_FACTORY_LOST_RETURN")
        return None if phase == "factory_none" else Proxy(path)

    monkeypatch.setattr(checkpoints.aiosqlite, "connect", connect)
    return proxies, calls


@pytest.mark.parametrize(
    "phase",
    ["factory_opaque", "factory_none", "open_opaque", "open_none", "close_failed", "closed_then_throw"],
)
def test_checkpoint_unknown_retains_original_allocation_tasks_and_once_close(tmp_path, monkeypatch, phase):
    async def scenario():
        fault = ProviderReceiptFault()
        proxies, calls = original_proxy(monkeypatch, phase)
        entered = []
        source = None
        try:
            with pytest.raises(checkpoints.CheckpointCleanupError) as error:
                async with checkpoints.sqlite_checkpointer(
                    tmp_path / "original.sqlite3",
                    failure=fault.mark_failed,
                    cleanup_failure=fault.retain_cleanup,
                ):
                    entered.append(True)
            source = error.value.source
            assert source.factory_attempted and source.cleanup_uncertain
            assert source.factory_returned is (phase != "factory_opaque")
            assert source.proxy is (None if phase.startswith("factory_") else proxies[0])
            assert fault.broken and fault._cleanup_sources[id(source)] is source
            assert "PRIVATE_" not in str(error.value)
            if phase.startswith("factory_"):
                assert not entered and calls == [] and source.opening is None and not source.close_attempted
            else:
                assert source.opening.done() and source.close_attempted and source.closing.done()
                assert calls == [proxies[0]]
                assert source.native_connection is not None  # BEFORE original SDK drops its native reference.
            before = list(calls)
            for _ in range(2):
                with pytest.raises(checkpoints.CheckpointCleanupError):
                    await source.close_original()
            assert calls == before
        finally:
            if source is not None and source.closing is not None:
                await asyncio.gather(source.closing, return_exceptions=True)
            # Only an actual opened, joined fixture proxy can be disposed here.
            for proxy in proxies:
                if proxy.raw._connection is not None:
                    await proxy.raw.close()

    asyncio.run(scenario())


def test_checkpoint_close_reentry_late_return_preserves_same_original_fault(tmp_path, monkeypatch):
    async def scenario():
        fault = ProviderReceiptFault()
        source = None

        async def reenter():
            with pytest.raises(checkpoints.CheckpointCleanupError) as error:
                await source.close_original()
            assert error.value.source is source

        proxies, calls = original_proxy(monkeypatch, "normal", close_hook=reenter)
        # Capture before closure WITHOUT substituting original saver/connection.
        constructor = checkpoints.CheckpointSource

        def capture(**kwargs):
            nonlocal source
            source = constructor(**kwargs)
            return source

        monkeypatch.setattr(checkpoints, "CheckpointSource", capture)
        with pytest.raises(checkpoints.CheckpointCleanupError):
            async with checkpoints.sqlite_checkpointer(
                tmp_path / "original.sqlite3", failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup
            ):
                pass
        assert source.close_returned and source.cleanup_uncertain and calls == [proxies[0]]
        assert fault._cleanup_sources[id(source)] is source
        assert proxies[0].raw._connection is None  # Method return is still NOT native acceptance.

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["setup", "close"])
def test_original_checkpoint_task_factory_lost_return_keeps_same_operation_without_retry(
    tmp_path, monkeypatch, phase
):
    async def scenario():
        fault = ProviderReceiptFault()
        task_factory, allocated = asyncio.create_task, []

        def lost_return(operation):
            task = task_factory(operation)
            allocated.append(task)
            if len(allocated) == (1 if phase == "setup" else 2):
                raise OSError("PRIVATE_ORIGINAL_TASK_LOST_RETURN")
            return task

        monkeypatch.setattr(checkpoints.asyncio, "create_task", lost_return)
        source = None
        try:
            with pytest.raises(checkpoints.CheckpointCleanupError) as error:
                async with checkpoints.sqlite_checkpointer(
                    tmp_path / "original.sqlite3",
                    failure=fault.mark_failed,
                    cleanup_failure=fault.retain_cleanup,
                ):
                    pass
            source = error.value.source
            assert fault._cleanup_sources[id(source)] is source and source.cleanup_uncertain
            assert source.setup_operation is not None
            if phase == "setup":
                assert source.setup is None and not source.close_attempted and source.closing is None
            else:
                assert source.setup.done() and source.closing is None and source.close_attempted
                assert source.close_operation is not None and source.native_connection is not None
            before = len(allocated)
            with pytest.raises(checkpoints.CheckpointCleanupError):
                await source.close_original()
            assert len(allocated) == before
        finally:
            await asyncio.gather(*allocated, return_exceptions=True)  # Observer's originals, fixture ONLY.
            if source is not None and source.proxy._connection is not None:
                await source.proxy.close()

    asyncio.run(scenario())


def test_original_graph_state_after_checkpoint_close_fault_cannot_allocate_another_connection(
    tmp_path, monkeypatch
):
    async def scenario():
        fault = ProviderReceiptFault()
        runtime = GraphRuntime(tmp_path, MockProvider(), provider_receipt_fault=fault)
        proxies, calls = original_proxy(monkeypatch, "closed_then_throw")
        with pytest.raises(checkpoints.CheckpointCleanupError) as error:
            await runtime.state("original-thread")
        assert (
            error.value.source.proxy is proxies[0]
            and fault._cleanup_sources[id(error.value.source)] is error.value.source
        )
        with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
            await runtime.state("original-thread")
        assert len(proxies) == len(calls) == 1 and proxies[0].raw._connection is None

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", ["cursor", "execute", "executemany", "executescript"])
@pytest.mark.parametrize("after_effect", [False, True])
def test_sdk_original_cursor_close_fault_retains_same_cursor_result_and_owner(
    tmp_path, monkeypatch, factory, after_effect
):
    """Actual SDK cursor and worker; only the original close failure is injected."""

    async def scenario():
        fault = ProviderReceiptFault()
        original_close = aiosqlite.Cursor.close
        calls, returned = [], []
        source = None
        target = None

        async def failed_close(cursor):
            if cursor is target:
                calls.append(cursor)
                if after_effect:
                    await original_close(cursor)
                raise OSError("PRIVATE_SDK_CURSOR_CLOSE")
            await original_close(cursor)

        monkeypatch.setattr(aiosqlite.Cursor, "close", failed_close)
        constructor = checkpoints.CheckpointSource

        def capture(**kwargs):
            nonlocal source
            source = constructor(**kwargs)
            return source

        monkeypatch.setattr(checkpoints, "CheckpointSource", capture)
        try:
            with pytest.raises(checkpoints.CheckpointCleanupError) as error:
                async with checkpoints.sqlite_checkpointer(
                    tmp_path / "original.sqlite3",
                    failure=fault.mark_failed,
                    cleanup_failure=fault.retain_cleanup,
                ) as saver:
                    assert type(saver) is checkpoints.AsyncSqliteSaver
                    assert saver.conn is source.connection is source.proxy
                    await saver.conn.execute("CREATE TABLE fixture(value INTEGER)")
                    if factory == "cursor":
                        result = saver.conn.cursor()
                    elif factory == "execute":
                        result = saver.conn.execute("SELECT 17")
                    elif factory == "executemany":
                        result = saver.conn.executemany("INSERT INTO fixture VALUES (?)", [(17,)])
                    else:
                        result = saver.conn.executescript("INSERT INTO fixture VALUES (17);")
                    async with result as cursor:
                        target = cursor
                        returned.append(cursor)
                        assert type(cursor) is aiosqlite.Cursor
                        frame = source.sdk_cursors[-1]
                        assert frame["result"] is result and frame["cursor"] is cursor
                        assert frame["factory"] == factory and frame["acquire_returned"]
                        assert frame["operation"] is not None
            assert error.value.source is source and str(error.value) == "checkpoint_cleanup_unresolved"
            frame = source.sdk_cursors[-1]
            assert frame["cursor"] is target is returned[0] and frame["native_cursor"] is target._cursor
            assert (
                frame["close_attempted"]
                and not frame["close_returned"]
                and frame["close_operation"] is not None
            )
            assert calls == [target] and fault._cleanup_sources[id(source)] is source
            assert source.close_returned and source.closing.done() and source.cleanup_uncertain
            assert source.worker_thread is source.proxy._thread and source.worker_queue is source.proxy._tx
            with pytest.raises(checkpoints.CheckpointCleanupError):
                await target.close()
            with pytest.raises(checkpoints.CheckpointCleanupError):
                await source.close_original()
            assert calls == [target]  # No SDK cursor or connection disposal retry.
        finally:
            if source is not None and source.closing is not None:
                await asyncio.gather(source.closing, return_exceptions=True)
            if source is not None and source.proxy._connection is not None:
                await source.proxy_close()  # Fixture only, after original cleanup joins.

    asyncio.run(scenario())


def test_original_saver_schema_cursor_close_failure_is_not_graph_fallback(tmp_path, monkeypatch):
    async def scenario():
        fault = ProviderReceiptFault()
        runtime = GraphRuntime(tmp_path, MockProvider(), provider_receipt_fault=fault)
        original_close = aiosqlite.Cursor.close
        calls = []

        async def failed_schema_close(cursor):
            # The three application PRAGMA cursors are closed before SDK schema setup.
            calls.append(cursor)
            await original_close(cursor)
            if len(calls) == 4:
                raise OSError("PRIVATE_SAVER_SCHEMA_CURSOR_CLOSED")

        monkeypatch.setattr(aiosqlite.Cursor, "close", failed_schema_close)
        with pytest.raises(checkpoints.CheckpointCleanupError) as error:
            await runtime.state("original-thread")
        source = error.value.source
        assert source.saver.conn is source.proxy is source.connection
        assert len(source.sdk_cursors) == 1 and source.sdk_cursors[0]["factory"] == "executescript"
        assert source.sdk_cursors[0]["cursor"] is calls[3] and source.close_returned
        assert fault._cleanup_sources[id(source)] is source
        with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
            await runtime.state("original-thread")
        assert len(calls) == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["reentry", "cancelled_close", "acquire_lost_return"])
def test_original_sdk_cursor_unknown_is_sticky_after_late_connection_close(tmp_path, monkeypatch, phase):
    async def scenario():
        fault = ProviderReceiptFault()
        original_cursor, original_close = aiosqlite.Connection.cursor, aiosqlite.Cursor.close
        source, target = None, None
        calls, allocated = [], []
        closing, release = asyncio.Event(), asyncio.Event()

        def lost_return(connection):
            result = original_cursor(connection)
            if phase != "acquire_lost_return":
                return result
            original_operation = result._coro

            async def lost():
                cursor = await original_operation
                allocated.append(cursor)  # Fixture observer ONLY; production cannot invent its identity.
                raise OSError("PRIVATE_SDK_CURSOR_ACQUIRED_LOST_RETURN")

            result._coro = lost()
            return result

        monkeypatch.setattr(aiosqlite.Connection, "cursor", lost_return)

        async def close(cursor):
            if cursor is target:
                calls.append(cursor)
                if phase == "reentry":
                    with pytest.raises(checkpoints.CheckpointCleanupError):
                        await cursor.close()
                if phase == "cancelled_close":
                    closing.set()
                    await release.wait()
            await original_close(cursor)

        monkeypatch.setattr(aiosqlite.Cursor, "close", close)
        constructor = checkpoints.CheckpointSource

        def capture(**kwargs):
            nonlocal source
            source = constructor(**kwargs)
            return source

        monkeypatch.setattr(checkpoints, "CheckpointSource", capture)

        async def operation():
            nonlocal target
            async with checkpoints.sqlite_checkpointer(
                tmp_path / "original.sqlite3", failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup
            ) as saver:
                async with saver.conn.cursor() as cursor:
                    target = cursor
                    await cursor.execute("SELECT 17")

        task = asyncio.create_task(operation())
        try:
            if phase == "cancelled_close":
                await asyncio.wait_for(closing.wait(), timeout=3)  # Deadlock watchdog, not SLA.
                task.cancel()
            with pytest.raises(checkpoints.CheckpointCleanupError) as error:
                await asyncio.wait_for(task, timeout=3)
            assert error.value.source is source
            frame = source.sdk_cursors[-1]
            assert source.cleanup_uncertain and source.close_returned
            assert fault._cleanup_sources[id(source)] is source
            if phase == "acquire_lost_return":
                assert (
                    frame["cursor"] is None and frame["acquire_attempted"] and not frame["acquire_returned"]
                )
                assert frame["result"] is not None and len(allocated) == 1 and calls == []
            else:
                assert frame["cursor"] is target and calls == [target]
                assert frame["close_returned"] is (phase == "reentry")
                with pytest.raises(checkpoints.CheckpointCleanupError):
                    await target.close()
                assert calls == [target]
            with pytest.raises(checkpoints.CheckpointCleanupError):
                await source.close_original()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if source is not None and source.proxy._connection is not None:
                await source.proxy_close()

    asyncio.run(scenario())


def test_sdk_ordinary_query_error_closes_original_cursor_without_cleanup_quarantine(tmp_path, monkeypatch):
    async def scenario():
        import sqlite3

        fault = ProviderReceiptFault()
        source = None
        constructor = checkpoints.CheckpointSource

        def capture(**kwargs):
            nonlocal source
            source = constructor(**kwargs)
            return source

        monkeypatch.setattr(checkpoints, "CheckpointSource", capture)
        with pytest.raises(sqlite3.OperationalError):
            async with checkpoints.sqlite_checkpointer(
                tmp_path / "original.sqlite3", failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup
            ) as saver:
                async with saver.conn.cursor() as cursor:
                    await cursor.execute("SELECT * FROM absent_fixture_table")
        frame = source.sdk_cursors[0]
        assert frame["cursor"] is cursor and frame["close_returned"] and source.close_returned
        assert not source.cleanup_uncertain and not fault.broken and fault._cleanup_sources == {}

    asyncio.run(scenario())


def test_sdk_left_open_cursors_each_close_once_even_when_first_close_is_unknown(tmp_path, monkeypatch):
    async def scenario():
        fault = ProviderReceiptFault()
        source, first, second = None, None, None
        original_close = aiosqlite.Cursor.close
        calls = []

        async def close(cursor):
            calls.append(cursor)
            if cursor is first:
                raise OSError("PRIVATE_FIRST_ABANDONED_CURSOR_CLOSE")
            await original_close(cursor)

        monkeypatch.setattr(aiosqlite.Cursor, "close", close)
        constructor = checkpoints.CheckpointSource

        def capture(**kwargs):
            nonlocal source
            source = constructor(**kwargs)
            return source

        monkeypatch.setattr(checkpoints, "CheckpointSource", capture)
        with pytest.raises(checkpoints.CheckpointCleanupError):
            async with checkpoints.sqlite_checkpointer(
                tmp_path / "original.sqlite3", failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup
            ) as saver:
                first = await saver.conn.cursor()
                second = await saver.conn.cursor()
                await first.execute("SELECT 17")
                await second.execute("SELECT 23")
                # Known returned identities deliberately left open, no iterator substitution.
        assert calls[-2:] == [first, second] and source.close_returned
        assert source.sdk_cursors[0]["close_attempted"] and not source.sdk_cursors[0]["close_returned"]
        assert source.sdk_cursors[1]["close_returned"] and source.cleanup_uncertain
        assert fault._cleanup_sources[id(source)] is source
        with pytest.raises(checkpoints.CheckpointCleanupError):
            await source.close_original()
        assert calls[-2:] == [first, second]

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["factory_opaque", "factory_none"])
def test_sdk_cursor_factory_bad_return_cannot_become_no_allocation(tmp_path, monkeypatch, phase):
    async def scenario():
        fault = ProviderReceiptFault()
        original = aiosqlite.Connection.cursor
        allocated = []

        def factory(connection):
            allocated.append(original(connection))  # Only fixture observes unreturned Result.
            if phase == "factory_opaque":
                raise OSError("PRIVATE_CURSOR_FACTORY_LOST_RETURN")
            return None

        monkeypatch.setattr(aiosqlite.Connection, "cursor", factory)
        try:
            with pytest.raises(checkpoints.CheckpointCleanupError) as error:
                async with checkpoints.sqlite_checkpointer(
                    tmp_path / "original.sqlite3",
                    failure=fault.mark_failed,
                    cleanup_failure=fault.retain_cleanup,
                ) as saver:
                    async with saver.conn.cursor():
                        pass
            source = error.value.source
            frame = source.sdk_cursors[0]
            assert frame["factory_attempted"] and frame["factory_returned"] is (phase == "factory_none")
            assert frame["result"] is None and frame["cursor"] is None and not frame["acquire_attempted"]
            assert source.close_returned and source.cleanup_uncertain
            assert fault._cleanup_sources[id(source)] is source
            before = len(allocated)
            with pytest.raises(checkpoints.CheckpointCleanupError):
                saver.conn.cursor()
            assert len(allocated) == before
        finally:
            for result in allocated:
                result.close()  # Never-started original coroutine; fixture only.

    asyncio.run(scenario())
