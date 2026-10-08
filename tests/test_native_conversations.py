"""Production repositories on disposable SQLite stores, no provider/network."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from doppel_agent.conversations import ConversationStore
from doppel_agent.persistence.conversations import NativeConversationStore
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.migrations import MIGRATIONS
from doppel_agent.persistence.migrations import apply_migrations
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.settings import SettingsStore


def request(cid, prompt="needle % _", key="native-key"):
    return {"conversation_id": cid, "mode": "graph", "prompt": prompt, "permissions": {}, "idempotency_key": key}


def test_v1_runtime_upgrade_preserves_unbound_runs_and_legacy_files(tmp_path):
    db = tmp_path / "runtime.sqlite3"
    with sqlite_connection(db) as c:
        c.executescript(MIGRATIONS[1])
        c.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT)")
        c.execute("INSERT INTO schema_migrations VALUES(1,'prior')")
        c.execute("INSERT INTO runtime_runs(run_id,thread_id,status,mode,request_json,created_at,updated_at) "
                  "VALUES('old-run','old-thread','completed','graph',?,'old','old')", (json.dumps({"prompt": "old"}),))
    legacy = ConversationStore(tmp_path / "conversations.sqlite3")
    group = legacy.create_group("old group")
    conv = legacy.create("old title")
    legacy.set_group(conv["id"], group["id"])
    legacy.add_message(conv["id"], "user", "old message")
    settings = SettingsStore(tmp_path / "provider-settings.json")
    settings.save_profile({"provider": "mock", "model": "mock", "name": "offline"})
    old_settings = settings.path.read_bytes()
    old_db = legacy.path.read_bytes()
    native = NativeConversationStore(db)
    runs = RuntimeRunStore(db)
    assert runs.get("old-run")["thread_id"] == "old-thread"
    assert runs.get("old-run")["conversation_id"] is None
    native.create()
    assert legacy.path.read_bytes() == old_db
    assert settings.path.read_bytes() == old_settings
    assert ConversationStore(legacy.path).get(conv["id"])["messages"][0]["content"] == "old message"
    with sqlite_connection(db) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_native_crud_search_drafts_groups_and_tombstone(tmp_path):
    db = tmp_path / "runtime.sqlite3"
    store = NativeConversationStore(db)
    conv = store.create(draft=True)
    assert store.create(draft=True)["id"] == conv["id"]
    assert store.create(mode="deep", draft=True)["id"] != conv["id"]
    runs = RuntimeRunStore(db)
    body = request(conv["id"])
    record, _ = runs.create("a" * 32, "ignored", "graph", body, body["idempotency_key"], native=True)
    assert record["thread_id"] != conv["id"]
    for action in (lambda: store.update(conv["id"], archived=True), lambda: store.delete(conv["id"]),
                   lambda: store.update(conv["id"], profile_id="other")):
        with pytest.raises(ValueError, match="active"):
            action()
    runs.update(record["run_id"], "completed", answer="finished")
    runs.update(record["run_id"], "completed", answer="finished")
    runs.release_conversation_turn(record["run_id"])
    group = store.save_group("group")
    store.update(conv["id"], title="renamed", group_id=group["id"], profile_id="mock", archived=True)
    assert store.list(query="needle % _")[0]["archived"] == 1
    assert store.list(query="needle % impossible") == []
    assert len(store.get(conv["id"])["messages"]) == 2
    store.delete_group(group["id"])
    assert NativeConversationStore(db).get(conv["id"])["group_id"] is None
    store.delete(conv["id"])
    store.reconcile()
    with pytest.raises(KeyError):
        store.get(conv["id"])
    assert runs.get(record["run_id"])["answer"] == "finished"  # retained audit, explicitly not secure erasure
    with sqlite_connection(db) as c:
        assert c.execute("SELECT COUNT(*) FROM native_messages WHERE conversation_id=?", (conv["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("profile_id", ["other", None])
@pytest.mark.parametrize("extra", [{}, {"title": "must not apply"}, {"archived": False}])
def test_archived_profile_update_is_rejected_atomically(tmp_path, profile_id, extra):
    store = NativeConversationStore(tmp_path / "runtime.sqlite3")
    cid = store.create(profile_id="original")["id"]
    store.update(cid, archived=True)
    before = store.get(cid)
    with pytest.raises(ValueError, match="archived"):
        store.update(cid, profile_id=profile_id, **extra)
    assert store.get(cid) == before
    assert NativeConversationStore(store.database).get(cid) == before


def test_archived_management_still_allows_explicit_unarchive_before_profile_edit(tmp_path):
    store = NativeConversationStore(tmp_path / "runtime.sqlite3")
    cid = store.create(profile_id="original")["id"]
    gid = store.save_group("archive group")["id"]
    store.update(cid, archived=True)
    managed = store.update(cid, title="managed archive", group_id=gid)
    assert managed["archived"] == 1 and managed["profile_id"] == "original"
    store.update(cid, archived=False)
    changed = store.update(cid, profile_id="other")
    assert changed["archived"] == 0 and changed["profile_id"] == "other"
    assert changed["title"] == "managed archive" and changed["group_id"] == gid
    assert changed["messages"] == changed["runs"] == []


def test_concurrent_turn_reservation_and_replay_are_atomic(tmp_path):
    db = tmp_path / "runtime.sqlite3"
    store = NativeConversationStore(db)
    cid = store.create()["id"]
    runs = RuntimeRunStore(db)
    body = request(cid)

    def submit(index):
        return runs.create(f"{index:032x}", "unused", "graph", body, body["idempotency_key"], native=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(submit, range(8)))
    assert sum(created for _, created in records) == 1
    assert len({r["run_id"] for r, _ in records}) == 1
    with pytest.raises(ValueError, match="active"):
        runs.create("b" * 32, "unused", "graph", request(cid, key="other-key"), "other-key", native=True)
    runs.update(records[0][0]["run_id"], "completed", answer="done")
    runs.release_conversation_turn(records[0][0]["run_id"])
    with pytest.raises(ValueError, match="immutable"):
        runs.create("c" * 32, "unused", "deep", request(cid, key="mode-change"), "mode-change", native=True)
    assert len(store.get(cid)["messages"]) == 2


def test_missing_projection_reconciliation_and_restart_recovery(tmp_path):
    db = tmp_path / "runtime.sqlite3"
    store = NativeConversationStore(db)
    runs = RuntimeRunStore(db)
    cid = store.create()["id"]
    body = request(cid)
    r, _ = runs.create("a" * 32, "unused", "graph", body, "native-key", native=True)
    runs.update(r["run_id"], "completed", answer="verified")
    runs.release_conversation_turn(r["run_id"])
    with sqlite_connection(db) as c:
        c.execute("DELETE FROM native_messages")  # simulate older/crash-window missing projection
    store.reconcile()
    store.reconcile()
    assert [m["role"] for m in store.get(cid)["messages"]] == ["user", "assistant"]
    second, _ = runs.create("b" * 32, "unused", "graph", request(cid, key="second-key"), "second-key", native=True)
    runs.recover_incomplete()
    store.reconcile()
    reopened = NativeConversationStore(db).get(cid)
    assert reopened["runs"][-1]["status"] == "failed"
    assert reopened["active_run_id"] is None
    assert len(reopened["messages"]) == 3
    third, _ = runs.create("c" * 32, "unused", "graph", request(cid, key="third-key"), "third-key", native=True)
    assert third["thread_id"] != second["thread_id"]
    assert len(store.history(cid)) == 2


def test_projection_failure_rolls_back_run_acceptance_and_completion(tmp_path, monkeypatch):
    from doppel_agent.persistence import runs as module

    db = tmp_path / "runtime.sqlite3"
    store, runs = NativeConversationStore(db), RuntimeRunStore(db)
    cid = store.create()["id"]
    original = module.project_run

    def fail(*args):
        original(*args)
        raise sqlite3.OperationalError("scripted projection crash")

    with monkeypatch.context() as m:
        m.setattr(module, "project_run", fail)
        with pytest.raises(sqlite3.OperationalError):
            runs.create("a" * 32, "unused", "graph", request(cid), "native-key", native=True)
    assert runs.get("a" * 32) is None
    assert store.get(cid)["messages"] == []
    r, _ = runs.create("a" * 32, "unused", "graph", request(cid), "native-key", native=True)
    with monkeypatch.context() as m:
        m.setattr(module, "project_run", fail)
        with pytest.raises(sqlite3.OperationalError):
            runs.update(r["run_id"], "completed", answer="not committed")
    assert runs.get(r["run_id"])["status"] == "queued"
    assert len(store.get(cid)["messages"]) == 1


def test_migration_failure_is_atomic_and_retryable(tmp_path, monkeypatch):
    db = tmp_path / "runtime.sqlite3"
    NativeConversationStore(db)
    next_version = max(MIGRATIONS) + 1
    with monkeypatch.context() as m:
        m.setitem(MIGRATIONS, next_version, "ALTER TABLE native_conversations ADD COLUMN bad_column TEXT; INVALID SQL;")
        with pytest.raises(sqlite3.OperationalError):
            apply_migrations(db)
    with sqlite_connection(db) as c:
        assert "bad_column" not in {row[1] for row in c.execute("PRAGMA table_info(native_conversations)")}
        assert not c.execute("SELECT 1 FROM schema_migrations WHERE version=?", (next_version,)).fetchone()
    assert apply_migrations(db) == max(MIGRATIONS)


def test_default_profile_race_rejects_before_acceptance(tmp_path):
    db = tmp_path / "runtime.sqlite3"
    store, runs = NativeConversationStore(db), RuntimeRunStore(db)
    cid = store.create(profile_id="before")["id"]
    store.update(cid, profile_id="after")
    with pytest.raises(ValueError, match="profile changed"):
        runs.create("a" * 32, "unused", "graph", request(cid), "native-key", native=True,
                    expected_profile_id="before", profile_snapshot={"id": "before", "provider": "mock", "model": "offline"})
    assert store.get(cid)["messages"] == []
