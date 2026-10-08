"""Remaining C FIRST original history/file/task definitions, ALL UNRUN."""

import json
import sqlite3
from io import BytesIO
from types import SimpleNamespace

import pytest

from doppel_agent.storage import RunStore, LegacyStorageReadError
from doppel_agent.tasks.manager import TaskManager, TaskPersistenceError
from doppel_agent.web.server import ConsoleHandler, JobManager
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


IDENTITY = "a" * 32


def session(store):
    store.write_session(
        IDENTITY, {"run_id": IDENTITY, "status": "completed", "prompt": "offline", "answer": "original"}
    )
    return store.root / "runs" / IDENTITY / "session.json"


def handler_for(manager, path):
    handler = object.__new__(ConsoleHandler)
    handler.server = SimpleNamespace(manager=manager, server_port=8766)
    handler.path = path
    handler.headers = {"Host": "127.0.0.1:8766"}
    handler.rfile = BytesIO()
    replies = []
    handler._json = lambda status, payload: replies.append((status, payload))
    return handler, replies


@pytest.mark.parametrize(
    "raw",
    [
        "PRIVATE_BROKEN",
        "[]",
        "{}",
        '{"run_id":"' + "b" * 32 + '","status":"completed"}',
        '{"run_id":"' + IDENTITY + '","status":[]}',
    ],
)
def test_corrupt_original_session_fixed_not_missing_filtered_history_or_file_repair(tmp_path, raw):
    faults = []
    store = RunStore(tmp_path / "state", failure=lambda: faults.append("fault"))
    path = session(store)
    path.write_text(raw, encoding="utf-8")
    for read in (lambda: store.read_session(IDENTITY), store.list_sessions):
        with pytest.raises(LegacyStorageReadError, match="^legacy_storage_evidence_unavailable$"):
            read()
        assert path.read_text(encoding="utf-8") == raw
    assert faults and not store.cleanup_uncertain


def test_missing_original_history_stays_missing_without_creating_state(tmp_path):
    root = tmp_path / "absent"
    store = RunStore(root)
    assert store.read_session(IDENTITY) is None and store.list_sessions() == [] and not root.exists()


def test_original_session_handle_failure_fixed_and_close_remains_uncertain(tmp_path, monkeypatch):
    from pathlib import Path

    store = RunStore(tmp_path / "state")
    path = session(store)
    original = Path.open

    def unavailable(value, *args, **kwargs):
        if value == path:
            raise OSError("PRIVATE_PATH_OR_HANDLE")
        return original(value, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unavailable)
    with pytest.raises(LegacyStorageReadError):
        store.read_session(IDENTITY)
    assert store.cleanup_uncertain


def test_original_read_handle_missing_error_after_open_is_not_missing_file(tmp_path, monkeypatch):
    from pathlib import Path

    store = RunStore(tmp_path / "state")
    path = session(store)
    closes = []
    original = Path.open

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closes.append("same original handle closed")

        def read(self, *_args):
            raise FileNotFoundError("PRIVATE_ENTERED_READ")

    def open_file(value, *args, **kwargs):
        return Stream() if value == path else original(value, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(LegacyStorageReadError):
        store.read_session(IDENTITY)
    assert closes == ["same original handle closed"] and store.cleanup_uncertain


def test_original_directory_iteration_failure_after_open_not_empty_history(tmp_path, monkeypatch):
    import doppel_agent.storage as module

    store = RunStore(tmp_path / "state")
    session(store)
    closes = []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closes.append("same original iterator closed")

        def __iter__(self):
            raise FileNotFoundError("PRIVATE_ENTERED_ITERATOR")

    monkeypatch.setattr(module.os, "scandir", lambda *_args: Entries())
    with pytest.raises(LegacyStorageReadError):
        store.list_sessions()
    assert closes == ["same original iterator closed"] and store.cleanup_uncertain


def frame(sequence=1):
    return {
        "run_id": IDENTITY,
        "sequence": sequence,
        "timestamp": "2026-10-06T00:00:00+00:00",
        "kind": "model_turn",
        "payload": {"tool_call_count": 0},
    }


@pytest.mark.parametrize(
    "bad",
    [
        "PRIVATE_BAD\n",
        json.dumps(frame())[:-1],
        "\n",
        "[]\n",
        json.dumps({**frame(), "run_id": "b" * 32}) + "\n",
    ],
)
def test_original_events_corrupt_or_partial_never_skipped_as_healthy_tail(tmp_path, bad):
    store = RunStore(tmp_path / "state")
    directory = session(store).parent
    path = directory / "events.jsonl"
    raw = json.dumps(frame()) + "\n" + bad
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(LegacyStorageReadError):
        store.read_events(IDENTITY)
    assert path.read_text(encoding="utf-8") == raw and not store.cleanup_uncertain


def test_original_event_tail_validates_omitted_prefix_and_bound_not_just_display_rows(tmp_path):
    store = RunStore(tmp_path / "state")
    directory = session(store).parent
    path = directory / "events.jsonl"
    frames = [frame(index) for index in range(1, 305)]
    path.write_text("".join(json.dumps(item) + "\n" for item in frames), encoding="utf-8")
    assert store.read_events(IDENTITY) == frames[-300:]
    raw = path.read_text(encoding="utf-8")
    path.write_text("PRIVATE_OMITTED_BAD\n" + raw, encoding="utf-8")
    with pytest.raises(LegacyStorageReadError):
        store.read_events(IDENTITY)


def test_original_event_missing_for_known_session_is_unknown_not_empty_success(tmp_path):
    store = RunStore(tmp_path / "state")
    session(store)
    with pytest.raises(LegacyStorageReadError):
        store.read_events(IDENTITY)


def test_original_file_and_event_limits_refuse_instead_of_successful_truncation(tmp_path, monkeypatch):
    import doppel_agent.storage as module

    store = RunStore(tmp_path / "state")
    path = session(store)
    before = path.read_bytes()
    monkeypatch.setattr(module, "MAX_READ_BYTES", 8)
    with pytest.raises(LegacyStorageReadError):
        store.read_session(IDENTITY)
    assert path.read_bytes() == before
    monkeypatch.setattr(module, "MAX_READ_BYTES", 1024 * 1024)
    monkeypatch.setattr(module, "MAX_EVENT_ROWS", 2)
    events = path.parent / "events.jsonl"
    events.write_text("".join(json.dumps(frame(i)) + "\n" for i in (1, 2, 3)), encoding="utf-8")
    with pytest.raises(LegacyStorageReadError):
        store.read_events(IDENTITY)


def test_original_history_directory_limit_is_unknown_not_partial_list(tmp_path, monkeypatch):
    import doppel_agent.storage as module

    store = RunStore(tmp_path / "state")
    session(store)
    store.write_session("b" * 32, {"run_id": "b" * 32, "status": "completed"})
    monkeypatch.setattr(module, "MAX_HISTORY_ENTRIES", 1)
    with pytest.raises(LegacyStorageReadError):
        store.list_sessions()


def test_original_operation_markers_and_core_frames_keep_original_private_payload_unchanged(tmp_path):
    store = RunStore(tmp_path / "state")
    session(store)
    store.append_operation(IDENTITY, "run")
    original = {**frame(), "payload": {"original private legacy field": "not a redacted report"}}
    store.append_event(IDENTITY, original)
    store.append_operation(IDENTITY, "run", outcome="returned")
    values = store.read_events(IDENTITY)
    assert len(values) == 3 and values[1] == original
    assert (
        values[0]["kind"] == "legacy.operation_started" and values[2]["kind"] == "legacy.operation_finished"
    )


@pytest.mark.parametrize("path", ["/api/runs", f"/api/runs/{IDENTITY}", f"/api/runs/{IDENTITY}/events"])
def test_original_history_get_typed_failure_same_fault_no_defaults_or_private_echo(tmp_path, path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    stored = session(manager.store)
    stored.write_text("PRIVATE_CORRUPTION", encoding="utf-8")
    handler, replies = handler_for(manager, path)
    try:
        handler.do_GET()
        assert replies == [(503, {"error": "legacy_storage_evidence_unavailable"})]
        assert fault.broken and manager._pending_requests == 0
        with pytest.raises(RuntimeError):
            manager.probe({"profile_id": "no new lookup"})
    finally:
        manager.close_owned()


def test_original_task_initialization_failure_closes_exact_allocated_connection(tmp_path, monkeypatch):
    import doppel_agent.tasks.manager as module

    calls = []

    class Connection:
        def execute(self, sql):
            calls.append(sql)
            raise sqlite3.OperationalError("PRIVATE_PRAGMA")

        def close(self):
            calls.append("close original")

    connection = Connection()
    monkeypatch.setattr(module.sqlite3, "connect", lambda *args, **kwargs: connection)
    with pytest.raises(TaskPersistenceError, match="^legacy_task_persistence_unavailable$"):
        TaskManager(tmp_path / "fixture.sqlite3")
    assert calls == ["PRAGMA foreign_keys=ON", "close original"]


def test_original_task_get_missing_database_never_initializes_or_creates_store(tmp_path):
    manager = JobManager(tmp_path)
    session(manager.store)
    path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
    handler, replies = handler_for(manager, f"/api/runs/{IDENTITY}/tasks")
    try:
        handler.do_GET()
        assert replies == [(200, [])] and not path.exists() and manager._pending_requests == 0
    finally:
        manager.close_owned()


def test_original_readonly_task_get_retains_original_list_query_without_schema_repair(tmp_path, monkeypatch):
    import doppel_agent.tasks.manager as module

    manager = JobManager(tmp_path)
    session(manager.store)
    path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
    writer = TaskManager(path)
    task_id = writer.create(IDENTITY, "original offline task")
    allocations = []
    original = module.sqlite3.connect

    def connect(database, *args, **kwargs):
        allocations.append((database, kwargs.get("uri")))
        return original(database, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    handler, replies = handler_for(manager, f"/api/runs/{IDENTITY}/tasks")
    try:
        handler.do_GET()
        assert replies[0][0] == 200 and replies[0][1][0]["id"] == task_id
        assert len(allocations) == 1 and "?mode=ro" in allocations[0][0] and allocations[0][1] is True
    finally:
        manager.close_owned()


def test_original_task_get_corrupt_existing_database_fixed_not_empty_or_schema_creation(tmp_path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    session(manager.store)
    path = tmp_path / ".doppel-agent" / "tasks.sqlite3"
    path.write_bytes(b"PRIVATE_CORRUPT_DATABASE")
    before = path.read_bytes()
    handler, replies = handler_for(manager, f"/api/runs/{IDENTITY}/tasks")
    handler.do_GET()
    assert replies == [(503, {"error": "legacy_task_persistence_unavailable"})]
    assert path.read_bytes() == before and fault.broken and manager._pending_requests == 0
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()
