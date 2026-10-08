"""Secure, WAL-backed SQLite checkpointers for local LangGraph runs."""

from __future__ import annotations

import asyncio
import os
import inspect
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from aiosqlite.context import Result
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


async def _drain_owned_task(task: asyncio.Future):
    """Finish acquired-resource work despite repeated caller cancellation."""
    cancelled = None
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError as error:
            if task.cancelled():
                raise
            if cancelled is None:
                cancelled = error


class CheckpointCleanupError(RuntimeError):
    def __init__(self, source):
        super().__init__("checkpoint_cleanup_unresolved")
        self.source = source  # Original private proxy/task/native refs, never event/HTTP payload.


class CheckpointSource:
    """Bookkeeping around SAME SDK connection/saver, not another checkpoint engine."""

    def __init__(self, *, failure=None, cleanup_failure=None):
        self.loop = asyncio.get_running_loop()
        self.proxy = self.connection = self.native_connection = None
        self.factory_attempted = self.factory_returned = False
        self.opening = self.setup = self.closing = None
        self.setup_operation = None
        self.proxy_close = self.connection_close = None
        self.opening_attempted = self.opening_returned = False
        self.setup_attempted = False
        self.close_attempted = self.close_returned = False
        self.cleanup_uncertain = False
        self.close_operation = self.saver = None
        self.setup_cursors = []
        self.worker_thread = self.worker_queue = None
        self.sdk_cursors = []
        self.sdk_factories = {}
        self.sdk_binding_attempted = False
        self._failure, self._cleanup_failure = failure, cleanup_failure

    def _retain(self):
        first = not self.cleanup_uncertain
        self.cleanup_uncertain = True
        if first:
            for callback, args in ((self._failure, ()), (self._cleanup_failure, (self,))):
                if callback is not None:
                    try:
                        callback(*args)
                    except BaseException:
                        pass

    def _unavailable(self):
        self._retain()
        raise CheckpointCleanupError(self) from None

    @staticmethod
    def _usable(value):
        try:
            return all(callable(getattr(value, name, None)) for name in ("execute", "commit", "close"))
        except BaseException:
            return False

    def _check_task(self, task):
        if not isinstance(task, asyncio.Future) or task.get_loop() is not self.loop:
            self._unavailable()

    async def open_original(self, database):
        if (
            asyncio.get_running_loop() is not self.loop
            or self.factory_attempted
            or self.cleanup_uncertain
            or self.close_attempted
        ):
            self._unavailable()
        self.factory_attempted = True  # BEFORE original proxy factory/worker allocation.
        try:
            self.proxy = aiosqlite.connect(str(database))
            self.factory_returned = True
            if self._usable(self.proxy):
                self.proxy_close = self.proxy.close  # Capture ORIGINAL method before async allocation.
            if not inspect.isawaitable(self.proxy):
                self._unavailable()
            self.worker_thread = getattr(self.proxy, "_thread", None)
            self.worker_queue = getattr(self.proxy, "_tx", None)
            self.opening_attempted = True
            self.opening = asyncio.ensure_future(self.proxy)
            self._check_task(self.opening)
            self.connection, cancelled = await _drain_owned_task(self.opening)
            self.opening_returned = True
            if not self._usable(self.connection):
                self._unavailable()
            self.connection_close = self.connection.close
            if self.proxy_close is not None and self.connection is not self.proxy:
                self._unavailable()  # Never silently substitute foreign returned identity.
            return cancelled
        except BaseException:
            self._unavailable()  # Lost/failed return isn't proof no worker/native allocation.

    async def setup_original(self):
        if (
            asyncio.get_running_loop() is not self.loop
            or self.setup_attempted
            or self.cleanup_uncertain
            or self.close_attempted
        ):
            self._unavailable()

        async def setup():
            for sql in ("PRAGMA journal_mode=WAL", "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000"):
                frame = {
                    "execute_attempted": True,
                    "cursor": None,
                    "execute_returned": False,
                    "close_attempted": False,
                    "close_returned": False,
                }
                self.setup_cursors.append(frame)
                try:
                    frame["cursor"] = await self.connection.execute(sql)
                    frame["execute_returned"] = True
                    if not callable(getattr(frame["cursor"], "close", None)):
                        self._unavailable()
                except BaseException:
                    self._unavailable()
                frame["close_attempted"] = True
                try:
                    await frame["cursor"].close()
                    frame["close_returned"] = True
                except BaseException:
                    self._unavailable()
            await self.connection.commit()

        self.setup_attempted = True
        try:
            self.setup_operation = setup()  # BEFORE task factory can schedule then lose its return.
            self.setup = asyncio.create_task(self.setup_operation)
            self._check_task(self.setup)
        except BaseException:
            self._unavailable()
        # Original setup also queues SDK worker IO. Shield its SAME task through
        # repeated caller cancellation before permitting connection close.
        _, cancelled = await _drain_owned_task(self.setup)
        return cancelled

    def bind_sdk_cursors_original(self):
        """Observe instance-local SDK factories without replacing saver/results/cursors.

        aiosqlite 0.22.1's Result delegates to _coro and its context exit calls the
        SAME returned Cursor.close. Retain that original coroutine, instrument
        its allocation/close boundaries and leave SQL/data operations to the SDK.
        Non-SDK protocol fixtures retain their existing setup/connection coverage;
        they are not evidence for this actual SDK binding.
        """
        if (
            asyncio.get_running_loop() is not self.loop
            or self.sdk_binding_attempted
            or self.close_attempted
            or self.cleanup_uncertain
        ):
            self._unavailable()
        self.sdk_binding_attempted = True
        if not isinstance(self.connection, aiosqlite.Connection):
            return
        try:
            for name in ("cursor", "execute", "executemany", "executescript"):
                original = getattr(self.connection, name)
                if not callable(original):
                    self._unavailable()
                self.sdk_factories[name] = original

                def observed(*args, _original=original, _name=name, **kwargs):
                    if (
                        asyncio.get_running_loop() is not self.loop
                        or self.close_attempted
                        or self.cleanup_uncertain
                    ):
                        self._unavailable()
                    frame = {
                        "factory": _name,
                        "factory_method": _original,
                        "factory_attempted": True,
                        "factory_returned": False,
                        "result": None,
                        "operation": None,
                        "observer_operation": None,
                        "acquire_attempted": False,
                        "acquire_returned": False,
                        "acquire_task": None,
                        "cursor": None,
                        "native_cursor": None,
                        "close_method": None,
                        "close_operation": None,
                        "close_task": None,
                        "close_attempted": False,
                        "close_returned": False,
                    }
                    self.sdk_cursors.append(frame)  # BEFORE original SDK cursor factory.
                    try:
                        frame["result"] = result = _original(*args, **kwargs)
                        frame["factory_returned"] = True
                        if not isinstance(result, Result):
                            self._unavailable()
                        frame["operation"] = result._coro
                        if not inspect.iscoroutine(frame["operation"]):
                            self._unavailable()
                        frame["observer_operation"] = self._acquire_sdk_cursor(frame)
                        result._coro = frame["observer_operation"]
                        return result  # EXACT original SDK Result; never a fake cursor/result.
                    except BaseException:
                        self._unavailable()

                setattr(self.connection, name, observed)
        except BaseException:
            self._unavailable()

    async def _acquire_sdk_cursor(self, frame):
        if (
            asyncio.get_running_loop() is not self.loop
            or frame["acquire_attempted"]
            or self.close_attempted
            or self.cleanup_uncertain
        ):
            self._unavailable()
        frame["acquire_attempted"] = True
        frame["acquire_task"] = asyncio.current_task()  # Task.done is NOT native-worker drain.
        try:
            frame["cursor"] = cursor = await frame["operation"]
            frame["acquire_returned"] = True
            if not isinstance(cursor, aiosqlite.Cursor):
                self._unavailable()
            frame["native_cursor"] = cursor._cursor
            frame["close_method"] = cursor.close  # BEFORE SDK automatic context-exit disposal.
            if not callable(frame["close_method"]):
                self._unavailable()

            async def close():
                return await self._close_sdk_cursor(frame)

            cursor.close = close
            return cursor  # SAME cursor, SQL methods, rows and iterator remain original.
        except BaseException:
            self._unavailable()  # Queued allocation may have effected despite lost/cancelled return.

    async def _close_sdk_cursor(self, frame):
        if asyncio.get_running_loop() is not self.loop:
            self._unavailable()
        if frame["close_attempted"]:
            if self.cleanup_uncertain or not frame["close_returned"]:
                self._unavailable()
            return None
        frame["close_attempted"] = True  # BEFORE opaque/after-effect/cancelled original close.
        frame["close_task"] = asyncio.current_task()
        try:
            frame["close_operation"] = frame["close_method"]()
            result = await frame["close_operation"]
            frame["close_returned"] = True
        except BaseException:
            self._unavailable()
        if self.cleanup_uncertain:
            raise CheckpointCleanupError(self) from None
        return result  # Original return value, not a cleanup/physical-drain oracle.

    async def close_original(self):
        if asyncio.get_running_loop() is not self.loop:
            self._unavailable()  # No foreign-loop attempt/retry of thread-affine source.
        if self.close_attempted:
            if self.cleanup_uncertain or not self.close_returned:
                self._unavailable()
            return None
        # Lost task-factory return may still have queued a producer. Never close
        # its proxy early or manufacture a replacement opening/setup task.
        if (
            self.opening_attempted
            and (not isinstance(self.opening, asyncio.Future) or not self.opening.done())
            or self.setup_attempted
            and (not isinstance(self.setup, asyncio.Future) or not self.setup.done())
        ):
            self._unavailable()
        target = self.proxy if self.proxy_close is not None else self.connection
        close = self.proxy_close if self.proxy_close is not None else self.connection_close
        if target is None or close is None:
            if self.cleanup_uncertain:
                raise CheckpointCleanupError(self) from None
            return None  # Nothing was attempted, e.g. a path refusal before source creation.
        self.close_attempted = True  # BEFORE original SDK drops _connection even on close failure.
        try:
            self.native_connection = getattr(target, "_connection", None)
        except BaseException:
            self._retain()  # Still independently close the known original proxy once.
        # SDK context exit normally closes these. Independently dispose any
        # known returned cursor left by an abandoned iterator/body; never retry
        # an attempted/unknown cursor close or invent an unreturned identity.
        for frame in self.sdk_cursors:
            if frame["close_method"] is not None and not frame["close_attempted"]:
                try:
                    await self._close_sdk_cursor(frame)
                except BaseException:
                    self._retain()  # Continue other known cursors and SAME connection close.
        try:
            self.close_operation = close()
            self.closing = asyncio.create_task(self.close_operation)
            self._check_task(self.closing)
            _, cancelled = await _drain_owned_task(self.closing)
            self.close_returned = True
        except BaseException:
            self._unavailable()
        if self.cleanup_uncertain:
            raise CheckpointCleanupError(self) from None
        return cancelled


@asynccontextmanager
async def sqlite_checkpointer(database: Path, *, failure=None, cleanup_failure=None, cleanup_check=None):
    if cleanup_check is not None:
        cleanup_check()  # SAME original owner latch, BEFORE path/proxy/SDK allocation.
    database.parent.mkdir(parents=True, exist_ok=True)
    # Checkpoints contain application-owned plain state. Strict msgpack prevents
    # an altered database from requesting arbitrary Python module loading.
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    source = CheckpointSource(failure=failure, cleanup_failure=cleanup_failure)
    body_error = None
    try:
        cancelled = await source.open_original(database)
        if cancelled is not None:
            raise cancelled
        source.saver = AsyncSqliteSaver(source.connection)
        cancelled = await source.setup_original()
        if cancelled is not None:
            raise cancelled
        source.bind_sdk_cursors_original()
        yield source.saver  # SAME original saver/connection/Result/cursor identities.
    except BaseException as error:
        body_error = error
        raise
    finally:
        cancelled = await source.close_original()
        if cancelled is not None and body_error is None:
            raise cancelled
