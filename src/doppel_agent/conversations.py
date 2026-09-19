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
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, id);
            """)

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
                "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            messages = db.execute(
                "SELECT role, content, run_id, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        result = dict(row)
        result["messages"] = [dict(message) for message in messages]
        return result

    def list(self, limit: int = 100) -> list[dict]:
        with self._database() as db:
            rows = db.execute("""
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COALESCE((SELECT content FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1), '') AS preview,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
                FROM conversations c ORDER BY c.updated_at DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, conversation_id: str, role: str, content: str, run_id: str | None = None) -> None:
        if role not in ("user", "assistant") or not isinstance(content, str) or not content:
            raise ValueError("invalid conversation message")
        if self.get(conversation_id) is None:
            raise ValueError("conversation not found")
        now = _now()
        with self._database() as db:
            db.execute(
                "INSERT INTO messages(conversation_id, role, content, run_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, role, content, run_id, now),
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
