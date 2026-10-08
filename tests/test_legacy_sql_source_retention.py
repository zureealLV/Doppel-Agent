"""C2c2g5e FIRST original Legacy SQL/constructor source definitions, ALL UNRUN.

Actual disposable SQLite + observed original socket APIs only. No native window,
physical handle/drain/remote SDK/billing proof; explicit teardown isn't recovery.
"""

import asyncio
import sqlite3
from http.server import ThreadingHTTPServer

import pytest

from doppel_agent.conversations import ConversationPersistenceError, ConversationStore
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.web.server import JobManager


def observe_sql(monkeypatch, database, events, phase):
    """Observe SAME actual original connection, never a second production reader."""
    import doppel_agent.conversations as module

    connect = module.sqlite3.connect
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
            if phase == "row_factory":
                raise ValueError("PRIVATE_ROW_FACTORY")
            self.raw.row_factory = value

        def execute(self, sql, *args):
            if phase in {"setup", "setup_close"} and sql.startswith("PRAGMA"):
                raise OSError("PRIVATE_PRAGMA")
            if phase == "body" and sql.lstrip().startswith("SELECT"):
                raise sqlite3.OperationalError("PRIVATE_SELECT")
            return self.raw.execute(sql, *args)

        def executescript(self, *args):
            return self.raw.executescript(*args)

        def __enter__(self):
            events.append("original transaction enter")
            if phase == "enter":
                raise ValueError("PRIVATE_ENTER")
            self.raw.__enter__()
            return self

        def __exit__(self, kind, error, trace):
            result = self.raw.__exit__(kind, error, trace)
            events.append("original rollback" if kind else "original commit")
            if phase == "commit" and kind is None:
                raise OSError("PRIVATE_COMMIT_RETURN")
            if phase == "rollback" and kind is not None:
                raise ValueError("PRIVATE_ROLLBACK_RETURN")
            if phase == "same_error_rollback" and kind is not None:
                raise error
            return result

        def close(self):
            self.closes += 1
            events.append("original close attempted")
            if phase in {"close", "setup_close"}:
                raise ValueError("PRIVATE_CLOSE")
            self.raw.close()
            if phase == "closed_then_throw":
                raise OSError("PRIVATE_CLOSED_THEN_THROW")
            events.append("original close returned")

    def original(path, *args, **kwargs):
        if str(path) != str(database):
            return connect(path, *args, **kwargs)
        connection = Connection(connect(path, *args, **kwargs))
        connections.append(connection)
        events.append("original factory entered")
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", original)
    return connections


@pytest.mark.parametrize(
    "phase", ["row_factory", "setup", "enter", "body", "commit", "rollback", "same_error_rollback"]
)
def test_original_metadata_unknown_fences_before_known_close_without_fake_resource_uncertainty(
    tmp_path, monkeypatch, phase
):
    events = []
    notifications = []
    fault = ProviderReceiptFault()
    path = tmp_path / "offline.sqlite3"

    def failed():
        events.append("same fault")
        notifications.append("fault")
        fault.mark_failed()

    store = ConversationStore(path, failure=failed, cleanup_failure=fault.retain_cleanup)
    connections = observe_sql(monkeypatch, path, events, phase)
    with pytest.raises(
        ConversationPersistenceError, match="^legacy_metadata_persistence_unavailable$"
    ) as error:
        if phase in {"rollback", "same_error_rollback"}:
            store.save_selection("a" * 32)
        else:
            store.list()
    assert error.value.source is store and error.value.__suppress_context__
    assert store.failed and store.cleanup_uncertain and fault.broken
    assert not store.resource_cleanup_uncertain and not fault.cleanup_uncertain
    assert events.index("same fault") < events.index("original close attempted")
    assert len(connections) == 1 and connections[0].closes == 1 and notifications == ["fault"]
    assert store._unresolved_connections == {}
    store.check_resource_cleanup()
    fault.check_cleanup()
    with pytest.raises(ConversationPersistenceError):
        store.list()
    with pytest.raises(ConversationPersistenceError):
        store._connect()
    assert len(connections) == 1 and connections[0].closes == 1 and notifications == ["fault"]


@pytest.mark.parametrize("phase", ["close", "setup_close", "closed_then_throw"])
def test_original_failed_sql_close_retains_same_handle_source_and_never_second_close_or_admission(
    tmp_path, monkeypatch, phase
):
    path = tmp_path / "offline.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    store = ConversationStore(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    connections = observe_sql(monkeypatch, path, events, phase)
    try:
        with pytest.raises(ConversationPersistenceError) as error:
            store.list()
        frame = next(iter(store._unresolved_connections.values()))
        assert error.value.source is store and frame.connection is connections[0]
        assert frame.connect_attempted and not frame.close_returned
        assert store.cleanup_uncertain and store.resource_cleanup_uncertain and fault.cleanup_uncertain
        assert next(iter(fault._cleanup_sources.values())) is store
        with pytest.raises(ConversationPersistenceError):
            store.check_resource_cleanup()
        with pytest.raises(ConversationPersistenceError):
            store.create("forbidden replacement")
        assert next(iter(store._unresolved_connections.values())) is frame
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        for connection in connections:
            connection.raw.close()  # Disposable teardown only, no reset.


def test_original_known_validation_and_cancellation_keep_healthy_transaction_and_close_contract(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.sqlite3"
    events = []
    notifications = []
    store = ConversationStore(path, failure=lambda: notifications.append("fault"))
    connections = observe_sql(monkeypatch, path, events, "healthy")
    with pytest.raises(ValueError, match="conversation not found"):
        store.save_selection("a" * 32)
    cancellation = asyncio.CancelledError("original caller")
    with pytest.raises(asyncio.CancelledError) as error:
        with store._database():
            raise cancellation
    assert error.value is cancellation and notifications == [] and not store.failed
    assert not store.cleanup_uncertain and not store.resource_cleanup_uncertain
    assert len(connections) == 2 and all(connection.closes == 1 for connection in connections)
    assert events.count("original rollback") == 2 and store._unresolved_connections == {}


def test_original_unreturned_sql_factory_retains_original_attempt_not_handle_absence_as_closed(
    tmp_path, monkeypatch
):
    import doppel_agent.conversations as module

    path = tmp_path / "offline.sqlite3"
    store = ConversationStore(path)
    raw_connect = module.sqlite3.connect
    handles = []
    calls = []

    def opaque(*args, **kwargs):
        handles.append(raw_connect(*args, **kwargs))
        calls.append("original allocation")
        raise OSError("PRIVATE_ALLOCATED_NO_RETURN")

    monkeypatch.setattr(module.sqlite3, "connect", opaque)
    try:
        with pytest.raises(ConversationPersistenceError) as error:
            store.list()
        frame = next(iter(store._unresolved_connections.values()))
        assert error.value.source is store and frame.connection is None and frame.connect_attempted
        assert not frame.close_returned and store.resource_cleanup_uncertain
        with pytest.raises(ConversationPersistenceError):
            store.list()
        assert calls == ["original allocation"]
    finally:
        for handle in handles:
            handle.close()  # Only fixture knows external original handle.


def test_actual_unpublished_job_manager_retains_original_failed_constructor_before_conversations_assignment(
    tmp_path, monkeypatch
):
    path = tmp_path / ".doppel-agent" / "conversations.sqlite3"
    events = []
    fault = ProviderReceiptFault()
    connections = observe_sql(monkeypatch, path, events, "close")
    try:
        with pytest.raises(ConversationPersistenceError) as error:
            JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
        source = error.value.source
        original_manager = source._cleanup_failure.__self__
        assert not hasattr(original_manager, "conversations") and not hasattr(original_manager, "pool")
        assert next(iter(original_manager._unresolved_metadata_sources.values())) is source
        assert (
            original_manager._operation_fault.is_set()
            and original_manager._operation_cleanup_uncertain.is_set()
        )
        assert (
            fault.broken and next(iter(source._unresolved_connections.values())).connection is connections[0]
        )
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        for connection in connections:
            connection.raw.close()


def test_actual_metadata_request_retains_source_but_known_sql_close_keeps_conservative_original_manager_gate(
    tmp_path, monkeypatch
):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    connections = observe_sql(monkeypatch, manager.conversations.path, [], "commit")
    try:
        with pytest.raises(ConversationPersistenceError):
            manager.conversations.create("offline actual committed")
        assert fault.broken and manager.conversations.cleanup_uncertain
        assert (
            not manager.conversations.resource_cleanup_uncertain and not manager._unresolved_metadata_sources
        )
        # Preserve inherited conservative metadata gate, not physical SQL-close claim.
        with pytest.raises(RuntimeError, match="^legacy_operation_cleanup_unresolved$"):
            manager.close_owned()
        assert len(connections) == 1 and connections[0].closes == 1
    finally:
        manager.pool.shutdown(wait=True)


@pytest.mark.parametrize(
    "sql_phase,socket_phase",
    [
        ("close", "known"),
        ("commit", "known"),
        ("row_factory", "known"),
        ("close", "before_close"),
        ("close", "closed_then_throw"),
        ("commit", "before_close"),
    ],
)
def test_actual_kernel_legacy_constructor_retains_original_sql_socket_and_outer_owner_before_assignment(
    tmp_path, monkeypatch, sql_phase, socket_phase
):
    from doppel_agent.projects.desktop_kernel import DesktopKernel
    from doppel_agent.web.server import LegacyServerCleanupError

    events = []
    database = tmp_path / ".doppel-agent" / "conversations.sqlite3"
    connections = observe_sql(monkeypatch, database, events, sql_phase)
    original_close = ThreadingHTTPServer.server_close
    servers = []
    close_calls = []

    def close(server):
        servers.append(server)
        close_calls.append(server.socket)
        if socket_phase == "before_close":
            raise OSError("PRIVATE_SOCKET_CLOSE")
        original_close(server)
        if socket_phase == "closed_then_throw":
            raise OSError("PRIVATE_SOCKET_CLOSED_THROW")

    monkeypatch.setattr(ThreadingHTTPServer, "server_close", close)
    kernel = DesktopKernel(tmp_path)
    try:
        resource_unknown = sql_phase == "close" or socket_phase != "known"
        if resource_unknown:
            with pytest.raises(RuntimeError, match="desktop startup cleanup is unresolved"):
                kernel.start()
            assert kernel.owner.held and not kernel._closed and kernel.legacy is None and kernel.app is None
            original = next(iter(kernel._startup_cleanup_sources.values()))
            if socket_phase == "known":
                assert original.resource_cleanup_uncertain
                assert next(iter(original._unresolved_connections.values())).connection is connections[0]
                assert servers[0].socket.fileno() == -1  # Known socket API close, not failed SQL proof.
            else:
                assert original is servers[0] and original.cleanup_uncertain
                assert original._socket_close_attempted and not original._socket_close_returned
                if sql_phase == "close":
                    sql_source = next(iter(original._startup_cleanup_sources.values()))
                    assert (
                        next(iter(sql_source._unresolved_connections.values())).connection is connections[0]
                    )
                with pytest.raises(LegacyServerCleanupError):
                    original.server_close()
            with pytest.raises(RuntimeError, match="desktop startup cleanup is unresolved"):
                kernel.close()
            assert kernel.owner.held and next(iter(kernel._startup_cleanup_sources.values())) is original
        else:
            with pytest.raises(ConversationPersistenceError):
                kernel.start()
            assert kernel._closed and not kernel.owner.held and not kernel._startup_cleanup_sources
        assert len(connections) == 1 and connections[0].closes == 1 and len(close_calls) == 1
    finally:
        for connection in connections:
            connection.raw.close()
        for server in servers:
            server.socket.close()  # Explicit disposable fixture teardown, never production retry.
        if kernel.owner.held:
            kernel.owner.release()


def test_original_bound_socket_then_bind_failure_closes_once_and_keeps_known_owner_release(
    tmp_path, monkeypatch
):
    from doppel_agent.projects.desktop_kernel import DesktopKernel

    sockets = []
    closes = []
    original_close = ThreadingHTTPServer.server_close

    def bind(server):
        sockets.append(server.socket)
        raise OSError("offline original bind failure")

    def close(server):
        closes.append(server.socket)
        original_close(server)

    monkeypatch.setattr(ThreadingHTTPServer, "server_bind", bind)
    monkeypatch.setattr(ThreadingHTTPServer, "server_close", close)
    kernel = DesktopKernel(tmp_path)
    with pytest.raises(OSError, match="offline original bind failure"):
        kernel.start()
    assert sockets == closes and len(sockets) == 1 and sockets[0].fileno() == -1
    assert kernel._closed and not kernel.owner.held and not kernel._startup_cleanup_sources
    assert kernel.legacy is None and not (tmp_path / ".doppel-agent" / "conversations.sqlite3").exists()


def test_original_opaque_server_constructor_allocation_keeps_same_attempt_and_outer_owner(
    tmp_path, monkeypatch
):
    import socket
    from doppel_agent.projects.desktop_kernel import DesktopKernel

    sockets = []
    calls = []

    def opaque(server, *args, **kwargs):
        sockets.append(socket.socket())
        calls.append(server)
        raise OSError("PRIVATE_SOCKET_ALLOCATED_NO_RETURN")

    monkeypatch.setattr(ThreadingHTTPServer, "__init__", opaque)
    kernel = DesktopKernel(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="desktop startup cleanup is unresolved"):
            kernel.start()
        source = next(iter(kernel._startup_cleanup_sources.values()))
        assert source is calls[0] and source._socket_start_attempted
        assert source.cleanup_uncertain and not source._socket_close_attempted
        with pytest.raises(RuntimeError, match="desktop startup cleanup is unresolved"):
            kernel.close()
        assert kernel.owner.held and kernel.legacy is None and len(calls) == 1
    finally:
        for handle in sockets:
            handle.close()  # External original allocation known ONLY to fixture.
        if kernel.owner.held:
            kernel.owner.release()
