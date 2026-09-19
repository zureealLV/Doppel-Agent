"""Persistent conversation threads and messages for the local workspace."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_id(value: str) -> bool:
    return len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


class ConversationStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversation_groups (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    group_id TEXT REFERENCES conversation_groups(id) ON DELETE SET NULL,
                    profile_id TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    model TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, id);
            """)
            conversation_columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
            for name, definition in (
                ("archived", "INTEGER NOT NULL DEFAULT 0"),
                ("group_id", "TEXT REFERENCES conversation_groups(id) ON DELETE SET NULL"),
                ("profile_id", "TEXT"),
            ):
                if name not in conversation_columns:
                    db.execute(f"ALTER TABLE conversations ADD COLUMN {name} {definition}")
            message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
            if "model" not in message_columns:
                db.execute("ALTER TABLE messages ADD COLUMN model TEXT")

    def create(self, title: str = "新对话") -> dict:
        title = title.strip()[:80] or "新对话"
        conversation_id = uuid4().hex
        now = _now()
        with self._database() as db:
            db.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title, now, now),
            )
        return self.get(conversation_id)

    def get(self, conversation_id: str) -> dict | None:
        if not _valid_id(conversation_id):
            return None
        with self._database() as db:
            row = db.execute(
                """SELECT c.id, c.title, c.created_at, c.updated_at, c.archived,
                          c.group_id, c.profile_id, g.name AS group_name
                   FROM conversations c LEFT JOIN conversation_groups g ON g.id = c.group_id
                   WHERE c.id = ?""",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            messages = db.execute(
                "SELECT role, content, run_id, created_at, model FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        result = dict(row)
        result["messages"] = [dict(message) for message in messages]
        return result

    def list(self, limit: int = 100, *, archived: bool = False) -> list[dict]:
        with self._database() as db:
            rows = db.execute("""
                SELECT c.id, c.title, c.created_at, c.updated_at, c.archived,
                       c.group_id, c.profile_id, g.name AS group_name,
                       COALESCE((SELECT content FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1), '') AS preview,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
                FROM conversations c LEFT JOIN conversation_groups g ON g.id = c.group_id
                WHERE c.archived = ? ORDER BY c.updated_at DESC LIMIT ?
            """, (int(archived), limit)).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self, conversation_id: str, role: str, content: str,
        run_id: str | None = None, model: str | None = None,
    ) -> None:
        if role not in ("user", "assistant") or not isinstance(content, str) or not content:
            raise ValueError("invalid conversation message")
        if self.get(conversation_id) is None:
            raise ValueError("conversation not found")
        now = _now()
        with self._database() as db:
            db.execute(
                "INSERT INTO messages(conversation_id, role, content, run_id, created_at, model) VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, role, content, run_id, now, model),
            )
            db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    def history(self, conversation_id: str, limit: int = 30) -> list[dict]:
        if self.get(conversation_id) is None:
            raise ValueError("conversation not found")
        with self._database() as db:
            rows = db.execute("""
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id
            """, (conversation_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def rename(self, conversation_id: str, title: str) -> dict:
        title = title.strip()[:80]
        if not title or not _valid_id(conversation_id):
            raise ValueError("invalid conversation title")
        with self._database() as db:
            changed = db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), conversation_id),
            ).rowcount
        if not changed:
            raise ValueError("conversation not found")
        return self.get(conversation_id)

    def set_title_from_prompt(self, conversation_id: str, prompt: str) -> None:
        conversation = self.get(conversation_id)
        if conversation and conversation["title"] == "新对话" and not conversation["messages"]:
            self.rename(conversation_id, " ".join(prompt.split())[:42])

    def delete(self, conversation_id: str) -> bool:
        if not _valid_id(conversation_id):
            return False
        with self._database() as db:
            return bool(db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount)

    def archive(self, conversation_id: str, archived: bool = True) -> dict:
        if not _valid_id(conversation_id):
            raise ValueError("invalid conversation id")
        with self._database() as db:
            changed = db.execute(
                "UPDATE conversations SET archived = ?, updated_at = ? WHERE id = ?",
                (int(archived), _now(), conversation_id),
            ).rowcount
        if not changed:
            raise ValueError("conversation not found")
        return self.get(conversation_id)

    def set_group(self, conversation_id: str, group_id: str | None) -> dict:
        if not _valid_id(conversation_id) or (group_id is not None and not _valid_id(group_id)):
            raise ValueError("invalid conversation or group id")
        with self._database() as db:
            if group_id and db.execute("SELECT 1 FROM conversation_groups WHERE id = ?", (group_id,)).fetchone() is None:
                raise ValueError("group not found")
            changed = db.execute(
                "UPDATE conversations SET group_id = ?, updated_at = ? WHERE id = ?",
                (group_id, _now(), conversation_id),
            ).rowcount
        if not changed:
            raise ValueError("conversation not found")
        return self.get(conversation_id)

    def set_profile(self, conversation_id: str, profile_id: str | None) -> dict:
        if not _valid_id(conversation_id) or (profile_id is not None and not isinstance(profile_id, str)):
            raise ValueError("invalid conversation or profile id")
        with self._database() as db:
            changed = db.execute(
                "UPDATE conversations SET profile_id = ?, updated_at = ? WHERE id = ?",
                (profile_id, _now(), conversation_id),
            ).rowcount
        if not changed:
            raise ValueError("conversation not found")
        return self.get(conversation_id)

    def list_groups(self) -> list[dict]:
        with self._database() as db:
            rows = db.execute("""
                SELECT g.id, g.name, g.created_at,
                       (SELECT COUNT(*) FROM conversations c WHERE c.group_id = g.id AND c.archived = 0) AS conversation_count
                FROM conversation_groups g ORDER BY lower(g.name), g.created_at
            """).fetchall()
        return [dict(row) for row in rows]

    def create_group(self, name: str) -> dict:
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError("group name is required")
        group_id = uuid4().hex
        created_at = _now()
        with self._database() as db:
            db.execute(
                "INSERT INTO conversation_groups(id, name, created_at) VALUES (?, ?, ?)",
                (group_id, name, created_at),
            )
        return {"id": group_id, "name": name, "created_at": created_at, "conversation_count": 0}

    def rename_group(self, group_id: str, name: str) -> dict:
        name = " ".join(name.split())[:60]
        if not _valid_id(group_id) or not name:
            raise ValueError("invalid group")
        with self._database() as db:
            changed = db.execute("UPDATE conversation_groups SET name = ? WHERE id = ?", (name, group_id)).rowcount
        if not changed:
            raise ValueError("group not found")
        return next(group for group in self.list_groups() if group["id"] == group_id)

    def delete_group(self, group_id: str) -> bool:
        if not _valid_id(group_id):
            return False
        with self._database() as db:
            return bool(db.execute("DELETE FROM conversation_groups WHERE id = ?", (group_id,)).rowcount)
