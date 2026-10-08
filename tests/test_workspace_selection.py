"""Workspace navigation preferences, using disposable stores only.

Written for the deferred whole-version test phase; no desktop/provider dependency.
"""

import json

import pytest

from doppel_agent.persistence.conversations import NativeConversationStore
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.migrations import MIGRATIONS
from doppel_agent.persistence.runs import RuntimeRunStore


def seed(root):
    path = root / "runtime.sqlite3"
    store, runs = NativeConversationStore(path), RuntimeRunStore(path)
    a, b = store.create(), store.create(mode="deep")
    for index, conversation in enumerate((a, a, b), start=1):
        rid = f"{index:032x}"
        body = {"conversation_id": conversation["id"], "mode": conversation["mode"],
                "prompt": f"independent fixture {index}", "permissions": {}}
        runs.create(rid, "unused", conversation["mode"], body, None, native=True)
        runs.update(rid, "completed", answer=f"receipt {index}")
        runs.release_conversation_turn(rid)
    return path, store, runs, a["id"], b["id"]


def test_workspace_selection_survives_reopen_without_reordering_or_runtime_effects(tmp_path):
    path, store, runs, a, b = seed(tmp_path)
    store.update(a, archived=True)
    before = {cid: store.get(cid)["updated_at"] for cid in (a, b)}
    audit = [runs.get(f"{index:032x}") for index in range(1, 4)]
    store.set_selection(a, f"{1:032x}")  # older archived conversation and older run
    reopened = NativeConversationStore(path)
    assert reopened.selection() == {"saved": True, "conversation_id": a, "run_id": f"{1:032x}"}
    assert reopened.get(a)["selected_run_id"] == f"{1:032x}"
    assert {cid: reopened.get(cid)["updated_at"] for cid in (a, b)} == before
    assert [runs.get(f"{index:032x}") for index in range(1, 4)] == audit
    store.set_selection(b, f"{3:032x}")
    assert reopened.get(a)["selected_run_id"] == f"{1:032x}"


def test_invalid_or_cross_conversation_selection_is_atomic(tmp_path):
    _path, store, _runs, a, b = seed(tmp_path)
    original = store.set_selection(a, f"{1:032x}")
    for cid, rid, error in ((b, f"{1:032x}", KeyError), (a, "f" * 32, KeyError),
                            ("f" * 32, None, KeyError), ("invalid", None, ValueError),
                            (a, "invalid", ValueError), (None, f"{1:032x}", ValueError)):
        with pytest.raises(error):
            store.set_selection(cid, rid)
        assert store.selection() == original


def test_clear_and_tombstone_selection_retain_adjacent_preferences_and_audit(tmp_path):
    path, store, runs, a, b = seed(tmp_path)
    store.set_selection(b, f"{3:032x}")
    store.set_selection(a, f"{1:032x}")
    audit = [runs.get(f"{index:032x}") for index in range(1, 4)]
    assert store.set_selection(None) == {"saved": True, "conversation_id": None, "run_id": None}
    assert store.get(a)["selected_run_id"] == f"{1:032x}"
    store.set_selection(a, None)
    assert store.get(a)["selected_run_id"] is None
    store.delete(a)
    reopened = NativeConversationStore(path)
    assert reopened.selection() == {"saved": True, "conversation_id": None, "run_id": None}
    assert reopened.get(b)["selected_run_id"] == f"{3:032x}"
    assert [runs.get(f"{index:032x}") for index in range(1, 4)] == audit
    with pytest.raises(KeyError):
        reopened.set_selection(a)
    with sqlite_connection(path) as db:
        assert not db.execute("SELECT 1 FROM native_conversation_selection WHERE conversation_id=?", (a,)).fetchone()
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_navigation_does_not_require_idle_or_release_an_active_turn(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store, runs = NativeConversationStore(path), RuntimeRunStore(path)
    cid, rid = store.create()["id"], "a" * 32
    runs.create(rid, "unused", "graph", {"conversation_id": cid, "mode": "graph",
                "prompt": "queued fixture", "permissions": {}}, None, native=True)
    original = runs.get(rid)
    store.set_selection(cid, rid)
    assert runs.get(rid) == original
    assert store.get(cid)["active_run_id"] == rid
    with pytest.raises(ValueError, match="active"):
        store.delete(cid)
    assert store.selection()["conversation_id"] == cid


def test_v3_upgrade_adds_empty_navigation_without_modifying_old_run_or_legacy_file(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    legacy = tmp_path / "conversations.sqlite3"
    legacy.write_bytes(b"untouched independent legacy sentinel")
    with sqlite_connection(path) as db:
        for version in (1, 2, 3):
            db.executescript(MIGRATIONS[version])
        db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT)")
        db.executemany("INSERT INTO schema_migrations VALUES(?,'old')", [(v,) for v in (1, 2, 3)])
        db.execute("INSERT INTO runtime_runs(run_id,thread_id,status,mode,request_json,created_at,updated_at) "
                   "VALUES('old','old-thread','completed','legacy',?,'old','old')", (json.dumps({"prompt": "old"}),))
        original = dict(db.execute("SELECT * FROM runtime_runs").fetchone())
    store = NativeConversationStore(path)
    assert store.selection() == {"saved": False, "conversation_id": None, "run_id": None}
    assert legacy.read_bytes() == b"untouched independent legacy sentinel"
    with sqlite_connection(path) as db:
        upgraded = dict(db.execute("SELECT * FROM runtime_runs").fetchone())
        assert {key: upgraded[key] for key in original} == original
        assert {key: value for key, value in upgraded.items() if key not in original} == {
            "write_scope": 0, "input_snapshot_json": None,
        }
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == max(MIGRATIONS)
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
