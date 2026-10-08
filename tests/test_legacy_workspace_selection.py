"""Legacy-only preferences; regression definitions written for unified acceptance."""

import sqlite3

import pytest

from doppel_agent.conversations import ConversationStore


def test_selection_persists_without_origin_or_native_history(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    store = ConversationStore(path)
    assert store.selection() == {"saved": False, "conversation_id": None}
    cid = store.create("Legacy")['id']
    store.save_selection(cid)
    assert ConversationStore(path).selection() == {"saved": True, "conversation_id": cid}
    assert not (tmp_path / "runtime.sqlite3").exists()


def test_explicit_null_and_delete_do_not_select_a_different_conversation(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    first = store.create()["id"]
    other = store.create()["id"]
    store.save_selection(first)
    store.delete(first)
    assert store.selection() == {"saved": True, "conversation_id": None}
    assert store.get(other) is not None
    store.save_selection(other)
    store.save_selection(None)
    assert store.selection() == {"saved": True, "conversation_id": None}


@pytest.mark.parametrize("invalid", ["", "unknown", "a" * 32, 12, [], {}])
def test_invalid_selection_preserves_previous_choice(tmp_path, invalid):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    cid = store.create()["id"]
    store.save_selection(cid)
    with pytest.raises(ValueError):
        store.save_selection(invalid)
    assert store.selection()["conversation_id"] == cid


def test_additive_selection_table_preserves_old_schema_messages(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    cid = "b" * 32
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE conversations(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE messages(id INTEGER PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT,
                                  run_id TEXT, created_at TEXT);
        """)
        db.execute("INSERT INTO conversations VALUES (?, 'old', 'date', 'date')", (cid,))
        db.execute("INSERT INTO messages VALUES (1, ?, 'user', 'preserved', 'audit', 'date')", (cid,))
    store = ConversationStore(path)
    store.save_selection(cid)
    assert store.get(cid)["messages"][0]["content"] == "preserved"
    assert store.get(cid)["messages"][0]["run_id"] == "audit"
