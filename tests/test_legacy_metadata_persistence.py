"""Remaining C FIRST original SQLite/HTTP definitions, ALL UNRUN.

Disposable original store and same request/connection gates only; no native proof.
"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from io import BytesIO
from types import SimpleNamespace

import pytest

from doppel_agent.conversations import ConversationStore, ConversationPersistenceError
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.web.server import ConsoleHandler, JobManager


IDENTITY = "a" * 32


def handler_for(manager, path, body=None):
    handler = object.__new__(ConsoleHandler)
    raw = json.dumps(body).encode() if body is not None else b""
    handler.server = SimpleNamespace(manager=manager, server_port=8766)
    handler.path = path
    handler.headers = {
        "Host": "127.0.0.1:8766",
        "Content-Type": "application/json",
        "X-Doppel-UI": "1",
        "Content-Length": str(len(raw)),
    }
    handler.rfile = BytesIO(raw)
    replies = []
    handler._json = lambda status, payload: replies.append((status, payload))
    return handler, replies


@pytest.mark.parametrize("phase", ["enter", "commit", "rollback", "close"])
def test_original_connection_failure_fixed_after_same_close_not_new_connection(tmp_path, monkeypatch, phase):
    notifications = []
    store = ConversationStore(tmp_path / "fixture.sqlite3", failure=lambda: notifications.append("fault"))
    calls = []

    class Connection:
        def __enter__(self):
            calls.append("enter")
            if phase == "enter":
                raise sqlite3.OperationalError("PRIVATE_ENTER")
            return self

        def __exit__(self, kind, error, trace):
            calls.append("rollback" if kind else "commit")
            if phase == "commit" and not kind:
                raise sqlite3.OperationalError("PRIVATE_COMMIT")
            if phase == "rollback" and kind:
                raise sqlite3.OperationalError("PRIVATE_ROLLBACK")

        def close(self):
            calls.append("close original")
            if phase == "close":
                raise ValueError("PRIVATE_HANDLE")

    connection = Connection()
    connections = []

    def connect():
        connections.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    with pytest.raises(ConversationPersistenceError, match="^legacy_metadata_persistence_unavailable$"):
        with store._database() as original:
            assert original is connection
            if phase == "rollback":
                raise ValueError("original validation")
    assert connections == [connection] and calls.count("close original") == 1
    assert notifications == ["fault"] and store.cleanup_uncertain


def test_original_sqlite_connect_failure_does_not_invent_allocated_handle_or_retry(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "fixture.sqlite3")
    calls = []

    def connect():
        calls.append("original allocation")
        raise sqlite3.OperationalError("PRIVATE_DATABASE")

    monkeypatch.setattr(store, "_connect", connect)
    with pytest.raises(ConversationPersistenceError):
        store.list()
    assert calls == ["original allocation"] and store.cleanup_uncertain


def test_original_committed_create_then_projection_failure_is_not_retry_or_false_success(
    tmp_path, monkeypatch
):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    original_connect = manager.conversations._connect
    allocations = []

    def connect():
        allocations.append("original connection")
        if len(allocations) == 2:
            raise sqlite3.OperationalError("PRIVATE_POST_COMMIT_PROJECTION")
        return original_connect()

    monkeypatch.setattr(manager.conversations, "_connect", connect)
    handler, replies = handler_for(manager, "/api/conversations", {"title": "original committed"})
    handler.do_POST()
    assert replies == [(503, {"error": "legacy_metadata_persistence_unavailable"})]
    assert allocations == ["original connection", "original connection"] and fault.broken
    # Read only via exact disposable fixture connection, not a retry/new grant.
    # Independent disposable row oracle, NOT failed original store re-admission,
    # worker/connection drain, reset, recovery or a second production connection.
    connection = sqlite3.connect(manager.conversations.path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT title FROM conversations").fetchall()
        assert [row["title"] for row in rows] == ["original committed"]
    finally:
        connection.close()
    replacement, refused = handler_for(manager, "/api/conversations", {"title": "replacement forbidden"})
    replacement.do_POST()
    assert refused == [(503, {"error": "legacy_operation_evidence_unavailable"})]
    assert allocations == ["original connection", "original connection"]
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/workspace-selection", {"conversation_id": IDENTITY}),
        ("/api/groups", {"name": "offline"}),
        (f"/api/groups/{IDENTITY}/rename", {"name": "offline"}),
        (f"/api/groups/{IDENTITY}/delete", {}),
        ("/api/conversations", {"title": "offline"}),
        ("/api/conversations/review-draft", {}),
        ("/api/conversations/new-draft", {}),
        (f"/api/conversations/{IDENTITY}/rename", {"title": "offline"}),
        (f"/api/conversations/{IDENTITY}/delete", {}),
        (f"/api/conversations/{IDENTITY}/archive", {"archived": True}),
        (f"/api/conversations/{IDENTITY}/group", {"group_id": None}),
        (f"/api/conversations/{IDENTITY}/profile", {"profile_id": None}),
    ],
)
def test_original_mutating_routes_sqlite_failure_fixed_not_input_error_and_same_native_fault(
    tmp_path, monkeypatch, path, body
):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    counts = []

    def connect():
        counts.append(manager._pending_requests)
        raise sqlite3.OperationalError("PRIVATE_SQL_OR_PATH")

    monkeypatch.setattr(manager.conversations, "_connect", connect)
    handler, replies = handler_for(manager, path, body)
    handler.do_POST()
    assert replies == [(503, {"error": "legacy_metadata_persistence_unavailable"})]
    assert counts == [1] and manager._pending_requests == 0 and fault.broken
    assert manager.conversations.cleanup_uncertain
    with pytest.raises(RuntimeError, match="legacy_operation_evidence_unavailable"):
        manager.probe({"profile_id": "no new lookup"})
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()


@pytest.mark.parametrize(
    "path",
    [
        "/api/workspace-selection",
        "/api/groups",
        "/api/conversations",
        "/api/conversations/search?q=offline",
        f"/api/conversations/{IDENTITY}",
    ],
)
def test_original_get_database_failure_fixed_no_empty_or_missing_fallback(tmp_path, monkeypatch, path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    counts = []

    def connect():
        counts.append(manager._pending_requests)
        raise sqlite3.OperationalError("PRIVATE_READ")

    monkeypatch.setattr(manager.conversations, "_connect", connect)
    handler, replies = handler_for(manager, path)
    handler.do_GET()
    assert replies == [(503, {"error": "legacy_metadata_persistence_unavailable"})]
    assert counts == [1] and manager._pending_requests == 0 and fault.broken
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()


def test_original_validation_rolls_back_and_closes_without_metadata_fault(tmp_path):
    calls = []
    store = ConversationStore(tmp_path / "fixture.sqlite3", failure=lambda: calls.append("fault"))
    with pytest.raises(ValueError, match="conversation not found"):
        store.save_selection(IDENTITY)
    assert (
        calls == []
        and not store.cleanup_uncertain
        and store.selection() == {"saved": False, "conversation_id": None}
    )


def test_original_get_reply_failure_not_database_failure_or_second_write(tmp_path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    handler, replies = handler_for(manager, "/api/conversations")
    attempts = []

    def reply(*args):
        attempts.append(args)
        raise BrokenPipeError("PRIVATE_HTTP")

    handler._json = reply
    try:
        with pytest.raises(BrokenPipeError):
            handler.do_GET()
        assert len(attempts) == 1 and replies == [] and not fault.broken
        assert manager._pending_requests == 0 and not manager.conversations.cleanup_uncertain
    finally:
        manager.close_owned()


def test_original_get_close_waits_same_connection_and_reply_request_not_timeout_replacement(
    tmp_path, monkeypatch
):
    manager = JobManager(tmp_path)
    handler, replies = handler_for(manager, "/api/groups")
    entered, release = threading.Event(), threading.Event()
    counts = []
    original_connect = manager.conversations._connect

    def connect():
        counts.append(manager._pending_requests)
        entered.set()
        release.wait()
        return original_connect()

    monkeypatch.setattr(manager.conversations, "_connect", connect)
    with ThreadPoolExecutor(max_workers=2) as fixture:
        request = fixture.submit(handler.do_GET)
        close = None
        try:
            assert entered.wait(2)
            close = fixture.submit(manager.close_owned)
            with pytest.raises(FutureTimeoutError):
                close.result(timeout=0.05)
            assert counts == [1] and manager._pending_requests == 1 and not request.done()
        finally:
            release.set()
        request.result(timeout=3)
        assert close is not None
        close.result(timeout=3)
    assert replies == [(200, [])] and manager._pending_requests == 0


def test_original_entered_connection_close_keeps_same_request_until_return(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    handler, replies = handler_for(manager, "/api/groups")
    original_connect = manager.conversations._connect
    entered, release = threading.Event(), threading.Event()
    connections = []
    closes = []

    class Connection:
        def __init__(self, original):
            self.original = original

        def __enter__(self):
            self.original.__enter__()
            return self

        def __exit__(self, *args):
            return self.original.__exit__(*args)

        def execute(self, *args):
            return self.original.execute(*args)

        def close(self):
            closes.append(self.original)
            entered.set()
            release.wait()
            self.original.close()

    def connect():
        original = original_connect()
        connections.append(original)
        return Connection(original)

    monkeypatch.setattr(manager.conversations, "_connect", connect)
    with ThreadPoolExecutor(max_workers=2) as fixture:
        request = fixture.submit(handler.do_GET)
        close = None
        try:
            assert entered.wait(2)
            close = fixture.submit(manager.close_owned)
            with pytest.raises(FutureTimeoutError):
                close.result(timeout=0.05)
            assert manager._pending_requests == 1 and replies == [] and not request.done()
            assert len(connections) == 1 and closes == connections
        finally:
            release.set()
        request.result(timeout=3)
        assert close is not None
        close.result(timeout=3)
    assert replies == [(200, [])] and manager._pending_requests == 0


def test_metadata_only_get_still_readable_under_original_provider_quarantine(tmp_path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    fault.mark_failed()
    handler, replies = handler_for(manager, "/api/groups")
    try:
        handler.do_GET()
        assert replies == [(200, [])] and manager._pending_requests == 0
    finally:
        manager.close_owned()


def test_original_close_failure_overrides_validation_without_private_cause(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path / "fixture.sqlite3")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            raise OSError("PRIVATE_CLOSE")

    monkeypatch.setattr(store, "_connect", Connection)
    with pytest.raises(ConversationPersistenceError) as failure:
        with store._database():
            raise ValueError("PRIVATE_VALIDATION")
    assert str(failure.value) == "legacy_metadata_persistence_unavailable"
    assert failure.value.__suppress_context__ and store.cleanup_uncertain
