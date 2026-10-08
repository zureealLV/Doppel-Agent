"""Persistent conversation threads and messages for the local workspace."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, get_ident
from typing import Any, NoReturn
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_id(value: str) -> bool:
    return len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


class ConversationPersistenceError(RuntimeError):
    """Original Legacy SQLite failure, never private SQL/path/error text."""

    def __init__(self, source: ConversationStore | None = None):
        super().__init__("legacy_metadata_persistence_unavailable")
        self.source = source  # Private original source, never an HTTP/report payload.


@dataclass
class _ConversationConnectionLifetime:
    connection: Any = None
    owner_thread: int = field(default_factory=get_ident)
    connect_attempted: bool = False
    transaction_entered: bool = False
    transaction_exit_returned: bool = False
    close_returned: bool = False


class ConversationStore:
    def __init__(self, path: Path, *, failure: Callable[[], None] | None = None,
                 cleanup_failure: Callable[[ConversationStore], None] | None = None):
        self._failure, self._cleanup_failure = failure, cleanup_failure
        # Preserve inherited broad metadata quarantine. It does NOT by itself
        # assert failed physical resource cleanup; that has a separate boundary.
        self._cleanup_uncertain = Event()
        self._resource_cleanup_uncertain = Event()
        self._lifetime_lock = Lock()
        self._unresolved_connections: dict[int, _ConversationConnectionLifetime] = {}
        self._draft_lock = Lock()
        try:
            self.path = path.resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._unavailable()
        self._initialize()

    @property
    def cleanup_uncertain(self) -> bool:
        return self._cleanup_uncertain.is_set()

    @property
    def failed(self) -> bool:
        return self.cleanup_uncertain

    @property
    def resource_cleanup_uncertain(self) -> bool:
        return self._resource_cleanup_uncertain.is_set()

    def _mark_failed(self) -> None:
        if self.failed:
            return
        self._cleanup_uncertain.set()
        if self._failure is not None:
            try:
                self._failure()  # Original manager/native latch; no DB/loop/IO.
            except BaseException:
                pass  # Preserve fixed storage error; never raw callback failure.

    def _unavailable(self) -> NoReturn:
        self._mark_failed()
        raise ConversationPersistenceError(self) from None

    def _retain_uncertain(self, frame: _ConversationConnectionLifetime) -> None:
        self._resource_cleanup_uncertain.set()
        with self._lifetime_lock:
            self._unresolved_connections[id(frame)] = frame
        self._mark_failed()
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # SAME original source, even unreturned constructor.
            except BaseException:
                pass

    def check_resource_cleanup(self) -> None:
        if self.resource_cleanup_uncertain:
            raise ConversationPersistenceError(self)

    def _close_original(self, frame: _ConversationConnectionLifetime) -> None:
        try:
            frame.connection.close()
            frame.close_returned = True
        except BaseException:
            self._retain_uncertain(frame)
            raise ConversationPersistenceError(self) from None

    def _connect(self) -> sqlite3.Connection:
        if self.failed:
            raise ConversationPersistenceError(self)  # No direct healthy-read reset/reopen.
        frame = _ConversationConnectionLifetime()
        frame.connect_attempted = True  # BEFORE original SQLite factory.
        try:
            connection = sqlite3.connect(self.path, timeout=10)
        except BaseException:
            # Opaque factory allocation may not return an original handle.
            self._retain_uncertain(frame)
            raise ConversationPersistenceError(self) from None
        frame.connection = connection
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except BaseException:
            self._mark_failed()  # BEFORE same original setup close.
            self._close_original(frame)
            self._unavailable()

    @contextmanager
    def _database(self):
        if self.failed:
            raise ConversationPersistenceError(self)
        # Original method boundary is observed BEFORE entering it; _connect's
        # own frame covers a handle that fails setup before returning here.
        frame = _ConversationConnectionLifetime()
        frame.connect_attempted = True
        try:
            connection = self._connect()
        except ConversationPersistenceError:
            raise  # SAME _connect already recorded exact failure/known close.
        except BaseException:
            self._retain_uncertain(frame)  # Opaque original method did not return.
            raise ConversationPersistenceError(self) from None
        frame.connection = connection
        try:
            try:
                connection.__enter__()
                frame.transaction_entered = True
            except BaseException:
                self._unavailable()
            try:
                yield connection
            except BaseException as exc:
                storage_failure = isinstance(exc, (sqlite3.Error, OSError, ConversationPersistenceError))
                if storage_failure:
                    self._mark_failed()  # Receipt fault BEFORE same rollback/close.
                try:
                    suppressed = connection.__exit__(type(exc), exc, exc.__traceback__)
                    frame.transaction_exit_returned = True
                except BaseException:
                    self._unavailable()
                if storage_failure:
                    self._unavailable()  # A suppressing exit cannot invent a known receipt.
                if not suppressed:
                    raise  # SAME healthy rollback retains original validation/cancellation.
            else:
                try:
                    connection.__exit__(None, None, None)
                    frame.transaction_exit_returned = True
                except BaseException:
                    self._unavailable()
        finally:
            self._close_original(frame)  # SAME original handle, no retry/second close.

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
                CREATE TABLE IF NOT EXISTS legacy_workspace_selection (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL
                );
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

    def selection(self) -> dict:
        """Workspace-owned UI selection, independent of native runtime history."""
        with self._database() as db:
            row = db.execute("SELECT conversation_id FROM legacy_workspace_selection WHERE singleton = 1").fetchone()
        return {"saved": row is not None, "conversation_id": row[0] if row else None}

    def save_selection(self, conversation_id: str | None) -> dict:
        if conversation_id is not None and (not isinstance(conversation_id, str) or not _valid_id(conversation_id)):
            raise ValueError("invalid conversation id")
        with self._database() as db:
            # Check existence in the same write transaction as the upsert. A
            # concurrent delete cannot leave an invalid persisted reference.
            db.execute("BEGIN IMMEDIATE")
            if conversation_id is not None and db.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,),
            ).fetchone() is None:
                raise ValueError("conversation not found")
            db.execute("""
                INSERT INTO legacy_workspace_selection(singleton, conversation_id) VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET conversation_id = excluded.conversation_id
            """, (conversation_id,))
        return {"saved": True, "conversation_id": conversation_id}

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

    def get_or_create_empty(self, title: str) -> dict:
        """Return one reusable empty draft, creating it atomically when absent."""
        title = title.strip()[:80]
        if not title:
            raise ValueError("conversation title is required")
        with self._draft_lock:
            with self._database() as db:
                row = db.execute(
                    """SELECT c.id FROM conversations c
                       WHERE c.title = ? AND c.archived = 0
                         AND NOT EXISTS (
                             SELECT 1 FROM messages m WHERE m.conversation_id = c.id
                         )
                       ORDER BY c.updated_at DESC LIMIT 1""",
                    (title,),
                ).fetchone()
                if row is None:
                    conversation_id = uuid4().hex
                    now = _now()
                    db.execute(
                        "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (conversation_id, title, now, now),
                    )
                else:
                    conversation_id = row["id"]
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

    def search(self, query: str, limit: int = 50) -> list[dict]:
        query = " ".join(query.split())[:200]
        if not query:
            return []
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self._database() as db:
            rows = db.execute("""
                SELECT c.id, c.title, c.created_at, c.updated_at, c.archived,
                       c.group_id, c.profile_id, g.name AS group_name,
                       COALESCE((SELECT content FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1), '') AS preview,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
                FROM conversations c LEFT JOIN conversation_groups g ON g.id = c.group_id
                WHERE c.title LIKE ? ESCAPE '\\'
                   OR EXISTS (
                       SELECT 1 FROM messages m
                       WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'
                   )
                ORDER BY c.updated_at DESC LIMIT ?
            """, (pattern, pattern, limit)).fetchall()
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
