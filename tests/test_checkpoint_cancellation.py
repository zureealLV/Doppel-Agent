"""A cancelled checkpoint acquisition must not orphan a SQLite worker handle."""

import asyncio
import sqlite3
import threading
import time

import aiosqlite.core
import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.persistence import sqlite_checkpointer
from doppel_agent.persistence import checkpoints


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_cancel_during_checkpoint_connection_open_closes_acquired_handle(tmp_path, monkeypatch, cancel_count):
    opened = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    connections = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            super().close()
            closed.set()

    def delayed_connect(*args, **kwargs):
        kwargs.update(factory=TrackedConnection, check_same_thread=False)
        connection = connect(*args, **kwargs)
        connections.append(connection)
        opened.set()
        if not release.wait(timeout=5):
            connection.close()
            raise TimeoutError("checkpoint open fixture was not released")
        return connection

    monkeypatch.setattr(aiosqlite.core.sqlite3, "connect", delayed_connect)
    entered = []

    async def scenario():
        async def operation():
            async with sqlite_checkpointer(tmp_path / "checkpoints.sqlite3"):
                entered.append(True)

        task = asyncio.create_task(operation())
        try:
            assert await asyncio.to_thread(opened.wait, 3)
            task.cancel()
            # Let cancellation reach the pending connection future before the
            # thread returns its actual acquired handle.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            if cancel_count == 2:
                task.cancel()
                await asyncio.sleep(0)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
            assert not entered
            assert closed.is_set(), "cancelled acquisition orphaned a live SQLite handle"
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connections[0].execute("SELECT 1")
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for connection in connections:
                connection.close()

    asyncio.run(scenario())


class GatedConnection:
    def __init__(self):
        self.closing = asyncio.Event()
        self.release_close = asyncio.Event()
        self.closed = False
        self.fail_setup = False

    def __await__(self):
        async def connect():
            return self
        return connect().__await__()

    async def execute(self, _sql):
        if self.fail_setup:
            raise sqlite3.OperationalError("fixture setup failed")

    async def commit(self):
        pass

    async def close(self):
        self.closing.set()
        await self.release_close.wait()
        self.closed = True


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_cancellation_during_close_waits_for_cleanup_and_stays_cancelled(tmp_path, monkeypatch, cancel_count):
    async def scenario():
        connection = GatedConnection()
        monkeypatch.setattr(checkpoints.aiosqlite, "connect", lambda _path: connection)

        async def operation():
            async with sqlite_checkpointer(tmp_path / "close.sqlite3"):
                pass

        task = asyncio.create_task(operation())
        await asyncio.wait_for(connection.closing.wait(), timeout=3)
        try:
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done(), "caller cancellation abandoned checkpoint close"
        finally:
            connection.release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3)
        assert connection.closed

    asyncio.run(scenario())


def test_setup_failure_still_closes_checkpoint_connection(tmp_path, monkeypatch):
    async def scenario():
        connection = GatedConnection()
        connection.fail_setup = True
        connection.release_close.set()
        monkeypatch.setattr(checkpoints.aiosqlite, "connect", lambda _path: connection)
        with pytest.raises(sqlite3.OperationalError, match="setup failed"):
            async with sqlite_checkpointer(tmp_path / "setup.sqlite3"):
                pytest.fail("failed setup entered checkpoint body")
        assert connection.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_open_failure_is_observed_and_preserves_cancellation(tmp_path, monkeypatch, cancelled):
    async def scenario():
        opening = asyncio.Event()
        release = asyncio.Event()

        async def failing_open():
            opening.set()
            await release.wait()
            raise sqlite3.OperationalError("fixture opening failed")

        monkeypatch.setattr(checkpoints.aiosqlite, "connect", lambda _path: failing_open())

        async def operation():
            async with sqlite_checkpointer(tmp_path / "failed.sqlite3"):
                pytest.fail("failed opening entered checkpoint body")

        task = asyncio.create_task(operation())
        await asyncio.wait_for(opening.wait(), timeout=3)
        if cancelled:
            task.cancel()
            await asyncio.sleep(0)
        release.set()
        error = asyncio.CancelledError if cancelled else sqlite3.OperationalError
        with pytest.raises(error):
            await asyncio.wait_for(task, timeout=3)

    asyncio.run(scenario())


def test_api_cancel_during_open_releases_handle_before_terminal_state(tmp_path, monkeypatch):
    opened, release, closed = (threading.Event() for _ in range(3))
    connections = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            super().close()
            closed.set()

    def gated_checkpoint_connect(database, *args, **kwargs):
        if str(database).endswith("checkpoints.sqlite3"):
            kwargs.update(factory=TrackedConnection, check_same_thread=False)
            connection = connect(database, *args, **kwargs)
            connections.append(connection)
            opened.set()
            if not release.wait(timeout=5):
                connection.close()
                raise TimeoutError("API checkpoint fixture not released")
            return connection
        return connect(database, *args, **kwargs)

    class SlowProvider:
        async def anext_turn(self, _messages, _tools):
            await asyncio.sleep(30)

    monkeypatch.setattr(aiosqlite.core.sqlite3, "connect", gated_checkpoint_connect)
    try:
        with TestClient(create_app(tmp_path, provider=SlowProvider())) as client:
            run_id = client.post("/api/v1/runs", json={"prompt": "cancel while opening"}).json()["run_id"]
            assert opened.wait(timeout=3)
            assert client.post(f"/api/v1/runs/{run_id}/cancel").json()["cancel_requested"]
            # A not-yet-owned worker handle cannot be abandoned as "cancelled".
            assert client.get(f"/api/v1/runs/{run_id}").json()["status"] != "cancelled"
            release.set()
            for _ in range(300):
                record = client.get(f"/api/v1/runs/{run_id}").json()
                if record["status"] == "cancelled":
                    break
                time.sleep(0.01)
            assert record["status"] == "cancelled"
            assert closed.is_set()
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            assert sum(event["type"] == "run.cancelled" for event in events) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[0].execute("SELECT 1")
        (tmp_path / ".doppel-agent/checkpoints.sqlite3").unlink()
    finally:
        release.set()
        for connection in connections:
            connection.close()
