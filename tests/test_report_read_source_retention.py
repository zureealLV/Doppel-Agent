"""C/D FIRST original report SQL resource definitions, ALL UNRUN.

Disposable actual SQLite connection/cursor proxies only. Test raw connections
disable thread affinity solely for fixture teardown, never production recovery.
Original owner/worker references do not prove native handle/port/lock closure.
"""

import asyncio
import sqlite3
import threading

import pytest

from doppel_agent.api import create_app
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.reporting.queries import RunReportQueries
from doppel_agent.reporting.evidence import ReportEvidenceQueries
from doppel_agent.reporting.read_lifetime import ReportReadCleanupError, ReportReadSource
from doppel_agent.runtime.provider_recording import ProviderReceiptError, ProviderReceiptFault
from doppel_agent.provider import MockProvider


RUN = "a" * 32


def observer(monkeypatch, database, phase, *, ordinal=1, close_hook=None, cursor_close_hook=None):
    original, connections, cursors, calls = sqlite3.connect, [], [], []
    uri, matched = database.absolute().as_uri() + "?mode=ro", 0

    class Cursor:
        def __init__(self, raw):
            self.raw = raw
            cursors.append(self)

        def __getattr__(self, name):
            return getattr(self.raw, name)

        def __iter__(self):
            return iter(self.raw)

        def close(self):
            calls.append(("cursor close", self))
            if cursor_close_hook is not None:
                cursor_close_hook()
            if phase == "cursor_close" and self is cursors[0]:
                raise OSError("PRIVATE_ORIGINAL_REPORT_CURSOR_CLOSE")
            result = self.raw.close()
            if phase == "cursor_closed_then_throw" and self is cursors[0]:
                raise OSError("PRIVATE_ORIGINAL_REPORT_CURSOR_ALREADY_CLOSED")
            return result

    class Connection:
        def __init__(self, raw):
            self.raw = raw
            connections.append(self)

        def __getattr__(self, name):
            return getattr(self.raw, name)

        @property
        def row_factory(self):
            return self.raw.row_factory

        @row_factory.setter
        def row_factory(self, value):
            if phase in {"setup", "setup_close"}:
                raise OSError("PRIVATE_ORIGINAL_REPORT_SETUP")
            self.raw.row_factory = value

        def execute(self, *args, **kwargs):
            cursor = Cursor(self.raw.execute(*args, **kwargs))
            if phase == "cursor_opaque" and cursor is cursors[0]:
                raise OSError("PRIVATE_ORIGINAL_REPORT_CURSOR_LOST_RETURN")
            if phase == "cursor_none" and cursor is cursors[0]:
                return None
            return cursor

        def close(self):
            calls.append(("connection close", self))
            if close_hook is not None:
                close_hook()
            if phase in {"connection_close", "setup_close"}:
                raise OSError("PRIVATE_ORIGINAL_REPORT_CONNECTION_CLOSE")
            result = self.raw.close()
            if phase == "connection_closed_then_throw":
                raise OSError("PRIVATE_ORIGINAL_REPORT_CONNECTION_ALREADY_CLOSED")
            return result

    def connect(target, *args, **kwargs):
        nonlocal matched
        if target != uri:
            return original(target, *args, **kwargs)
        matched += 1
        if matched != ordinal:
            return original(target, *args, **kwargs)
        connection = Connection(original(target, *args, **kwargs, check_same_thread=False))
        if phase == "connect_opaque":
            raise OSError("PRIVATE_ORIGINAL_REPORT_CONNECTION_LOST_RETURN")
        return None if phase == "connect_none" else connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    return connections, cursors, calls, lambda: matched


def dispose(connections, cursors):
    for cursor in cursors:
        try:
            cursor.raw.close()
        except sqlite3.Error:
            pass
    for connection in connections:
        connection.raw.close()  # Fixture ONLY; no production retry/reset/foreign-thread close.


def fixture(tmp_path, reader_kind, fault):
    database, ledger = tmp_path / "runtime.sqlite3", tmp_path / "tool-executions.sqlite3"
    RuntimeRunStore(database).create(RUN, "b" * 32, "graph", {"prompt": "fixture"}, None)
    if reader_kind == "events":
        reader = RunReportQueries(database, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    else:
        reader = ReportEvidenceQueries(
            database, ledger, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup
        )
    return database, reader


@pytest.mark.parametrize("reader_kind", ["events", "evidence"])
@pytest.mark.parametrize(
    "phase",
    [
        "connect_opaque",
        "connect_none",
        "cursor_opaque",
        "cursor_none",
        "cursor_close",
        "cursor_closed_then_throw",
        "connection_close",
        "connection_closed_then_throw",
        "setup_close",
    ],
)
def test_original_report_sql_unknown_retains_exact_frame_and_no_second_read_or_close(
    tmp_path, monkeypatch, reader_kind, phase
):
    fault = ProviderReceiptFault()
    database, reader = fixture(tmp_path, reader_kind, fault)
    connections, cursors, calls, matched = observer(monkeypatch, database, phase)
    try:
        with pytest.raises(ReportReadCleanupError) as error:
            reader.read(RUN)
        source = error.value.source
        assert source.connect_attempted and source.cleanup_uncertain
        assert source.connect_returned is (phase != "connect_opaque")
        assert source.connection is (None if phase in {"connect_opaque", "connect_none"} else connections[0])
        assert fault.broken and fault.cleanup_uncertain and fault._cleanup_sources[id(source)] is source
        assert reader._unresolved_reads[id(source)] is source and "PRIVATE_" not in str(error.value)
        before = list(calls)
        for action in (lambda: reader.read(RUN), source.close_original, source.close_original):
            with pytest.raises(ReportReadCleanupError):
                action()
        assert calls == before and matched() == 1
        if phase.startswith("connect_"):
            assert calls == [] and not source.close_attempted
        else:
            assert source.close_attempted and calls.count(("connection close", connections[0])) == 1
            for cursor in cursors:
                assert calls.count(("cursor close", cursor)) == (
                    0 if phase in {"cursor_opaque", "cursor_none"} else 1
                )
        if phase.startswith("cursor_"):
            original = source.cursors[0]
            assert original.execute_attempted
            assert original.execute_returned is (phase != "cursor_opaque")
            assert original.cursor is (None if phase in {"cursor_opaque", "cursor_none"} else cursors[0])
    finally:
        dispose(connections, cursors)


@pytest.mark.parametrize("reader_kind", ["events", "evidence"])
def test_original_known_closed_report_setup_failure_is_not_resource_quarantine(
    tmp_path, monkeypatch, reader_kind
):
    fault = ProviderReceiptFault()
    database, reader = fixture(tmp_path, reader_kind, fault)
    connections, cursors, calls, _ = observer(monkeypatch, database, "setup")
    try:
        if reader_kind == "events":
            with pytest.raises(ValueError, match="^report_evidence_unavailable$"):
                reader.read(RUN)
        else:
            assert reader.read(RUN)["work_orders"]["state"] == "unknown"
        assert not fault.broken and not fault.cleanup_uncertain and not reader._unresolved_reads
        assert calls == [("connection close", connections[0])]
    finally:
        dispose(connections, cursors)


@pytest.mark.parametrize("layer", ["events", "evidence_runtime", "ledger"])
def test_original_completed_runtime_report_failed_close_reaches_same_owner_before_release(
    tmp_path, monkeypatch, layer
):
    async def scenario():
        app = create_app(tmp_path, provider=MockProvider())
        service = app.state.run_service
        await service.start()
        record, created = await service.create({"prompt": "offline", "mode": "graph", "permissions": {}})
        assert created
        result = await asyncio.wait_for(service.scheduler.wait(record["run_id"]), 30)
        assert result.status == "completed"
        database = service.state_root / (
            "tool-executions.sqlite3" if layer == "ledger" else "runtime.sqlite3"
        )
        connections, cursors, calls, matched = observer(
            monkeypatch, database, "connection_close", ordinal=2 if layer == "evidence_runtime" else 1
        )
        try:
            with pytest.raises(ReportReadCleanupError) as error:
                await service.run_report(record["run_id"])
            source = error.value.source
            assert source.connection is connections[0]
            assert service._provider_receipt_fault._cleanup_sources[id(source)] is source
            assert service._unresolved_report_reads[id(source)] is source
            assert service._owner.held and not service.cleanup_complete
            with pytest.raises(ProviderReceiptError):
                await service.create({"prompt": "must not enter", "mode": "graph", "permissions": {}})
            before, allocated = list(calls), matched()
            with pytest.raises(RuntimeError, match="^report_read_cleanup_unresolved$"):
                await service.run_report(record["run_id"])
            for _ in range(2):
                with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                    await service.close()
            assert calls == before and matched() == allocated
            assert service._owner.held and not service.cleanup_complete
            assert service._close_task.done() and service._close_task.exception() is not None
        finally:
            dispose(connections, cursors)
            # Isolated test teardown AFTER original workers/pumps have joined;
            # source references/latches remain unchanged, no production recovery.
            service._owner.release()

    asyncio.run(scenario())


@pytest.mark.parametrize("layer", ["cursor", "connection"])
def test_same_original_report_close_reentry_does_not_clear_fault_on_late_return(tmp_path, monkeypatch, layer):
    fault = ProviderReceiptFault()
    database, _reader = fixture(tmp_path, "events", fault)
    source = ReportReadSource(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    reentries = []

    def reenter():
        with pytest.raises(ReportReadCleanupError) as error:
            source.close_original()
        reentries.append(error.value.source)

    connections, cursors, calls, _ = observer(
        monkeypatch,
        database,
        "normal",
        **({"cursor_close_hook": reenter} if layer == "cursor" else {"close_hook": reenter}),
    )
    try:
        source.open_original(sqlite3.connect, database.absolute().as_uri() + "?mode=ro")
        assert source.execute("SELECT 1").fetchone()[0] == 1
        with pytest.raises(ReportReadCleanupError):
            source.close_original()
        assert reentries == [source] and source.close_returned and source.cursors[0].close_returned
        assert source.cleanup_uncertain and fault._cleanup_sources[id(source)] is source
        before = list(calls)
        with pytest.raises(ReportReadCleanupError):
            source.close_original()
        assert calls == before == [("cursor close", cursors[0]), ("connection close", connections[0])]
    finally:
        dispose(connections, cursors)


def test_foreign_thread_report_close_does_not_touch_original_sql_or_reset_fault(tmp_path, monkeypatch):
    fault = ProviderReceiptFault()
    database, _reader = fixture(tmp_path, "events", fault)
    source = ReportReadSource(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    connections, cursors, calls, _ = observer(monkeypatch, database, "normal")
    failures = []
    try:
        source.open_original(sqlite3.connect, database.absolute().as_uri() + "?mode=ro")
        source.execute("SELECT 1")

        def foreign_close():
            try:
                source.close_original()
            except ReportReadCleanupError as error:
                failures.append(error.source)

        worker = threading.Thread(target=foreign_close)
        worker.start()
        worker.join(5)
        assert not worker.is_alive() and failures == [source] and calls == []
        # Owning thread still performs its first independent known disposal;
        # returned receipt never resets the foreign-thread uncertainty latch.
        with pytest.raises(ReportReadCleanupError):
            source.close_original()
        assert source.close_returned and source.cursors[0].close_returned
        assert fault._cleanup_sources[id(source)] is source and source.cleanup_uncertain
    finally:
        dispose(connections, cursors)


def test_already_queued_original_report_rechecks_failed_source_under_owner_lock(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(tmp_path, provider=MockProvider())
        service = app.state.run_service
        await service.start()
        service.runs.create(RUN, "b" * 32, "graph", {"prompt": "fixture"}, None)
        connections, cursors, calls, matched = observer(
            monkeypatch, service.state_root / "runtime.sqlite3", "connection_close"
        )
        queued = []
        try:
            async with service._lifecycle_lock:
                queued = [asyncio.create_task(service.run_report(RUN)) for _ in range(2)]
                await asyncio.sleep(0)  # Both original readers reach the held-owner lock.
                assert all(not item.done() for item in queued)
            failures = await asyncio.wait_for(asyncio.gather(*queued, return_exceptions=True), 30)
            assert isinstance(failures[0], ReportReadCleanupError)
            assert type(failures[1]) is RuntimeError and str(failures[1]) == "report_read_cleanup_unresolved"
            assert matched() == 1 and service._owner.held
            assert calls.count(("connection close", connections[0])) == 1
            with pytest.raises(RuntimeError, match="^owner_cleanup_unresolved$"):
                await service.close()
        finally:
            await asyncio.gather(*queued, return_exceptions=True)
            dispose(connections, cursors)
            service._owner.release()  # Fixture ONLY after original close joined workers.

    asyncio.run(scenario())
