"""Small app-owned recent-project catalog; never copies workspace credentials."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, get_ident
from typing import Any


def local_project_path(value: str | Path) -> Path:
    raw = str(value)
    if not raw or len(raw) > 32768 or "\0" in raw:
        raise ValueError("invalid project path")
    path = Path(raw)
    if not path.is_absolute() or raw.startswith(("\\\\", "//")):
        raise ValueError("project must be a local absolute directory")
    if os.name == "nt":
        import ctypes

        # Refuse UNC/device and mapped network drives before probing them.
        if not re.fullmatch(r"[A-Za-z]:", path.drive):
            raise ValueError("project must be on a local drive")
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) not in {2, 3}:
            raise ValueError("project must be on a local drive")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("project must be a directory")
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(str(resolved.anchor)) not in {2, 3}:
        raise ValueError("project target must be on a local drive")
    return resolved


def project_key(path: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:32]


def default_catalog_path() -> Path:
    # This is a new app-owned catalog, not the per-project provider settings.
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return root / "DoppelAgent" / "projects.sqlite3"


class ProjectCatalogPersistenceError(RuntimeError):
    def __init__(self, source):
        super().__init__('project_catalog_persistence_unavailable')
        self.source = source  # Exact private original constructor/SQL source, never native bridge payload.


@dataclass
class _CatalogCursorLifetime:
    cursor: Any = None
    execute_attempted: bool = False
    execute_returned: bool = False
    usable: bool = False
    close_attempted: bool = False
    close_returned: bool = False


@dataclass
class _CatalogConnectionLifetime:
    connection: Any = None
    owner_thread: int = field(default_factory=get_ident)
    connect_attempted: bool = False
    connect_returned: bool = False
    usable: bool = False
    transaction_entered: bool = False
    transaction_exit_returned: bool = False
    close_attempted: bool = False
    close_returned: bool = False
    cursors: list = field(default_factory=list)


class ProjectCatalog:
    def __init__(self, database: Path, *, failure: Callable[[], None] | None = None,
                 cleanup_failure: Callable[[ProjectCatalog], None] | None = None):
        self.database = database
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._failed = Event()
        self._resource_cleanup_uncertain = Event()
        self._lifetime_lock = Lock()
        self._active_connections: dict[int, _CatalogConnectionLifetime] = {}
        self._unresolved_connections: dict[int, _CatalogConnectionLifetime] = {}
        try:
            database.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._unavailable()  # BEFORE any SQL allocation; not unknown resource cleanup.
        with self._connect() as connection:
            self._execute_original(connection, """CREATE TABLE IF NOT EXISTS recent_projects (
                project_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, selected_at_ns INTEGER NOT NULL,
                opened_at_ns INTEGER NOT NULL DEFAULT 0
            )""")

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    @property
    def resource_cleanup_uncertain(self) -> bool:
        return self._resource_cleanup_uncertain.is_set()

    def _mark_failed(self) -> None:
        if self.failed:
            return
        self._failed.set()
        if self._failure is not None:
            try:
                self._failure()  # SAME original kernel admission latch, no IO/retry/dispatch.
            except BaseException:
                pass

    def _unavailable(self):
        self._mark_failed()
        raise ProjectCatalogPersistenceError(self) from None

    def _retain_uncertain(self, frame):
        self._resource_cleanup_uncertain.set()
        with self._lifetime_lock:
            self._unresolved_connections[id(frame)] = frame
        self._mark_failed()
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # Includes constructor which never publishes to WindowApi.
            except BaseException:
                pass

    def check_resource_cleanup(self) -> None:
        if self.resource_cleanup_uncertain:
            raise ProjectCatalogPersistenceError(self)

    def _close_connection_original(self, frame) -> None:
        if frame.close_attempted:
            if frame.close_returned:
                return
            raise ProjectCatalogPersistenceError(self)
        if not frame.usable:
            self._retain_uncertain(frame)
            raise ProjectCatalogPersistenceError(self)
        frame.close_attempted = True
        try:
            frame.connection.close()
            frame.close_returned = True
        except BaseException:
            self._retain_uncertain(frame)
            raise ProjectCatalogPersistenceError(self) from None

    def _close_cursor_original(self, frame, original) -> None:
        if original.close_attempted:
            if original.close_returned:
                return
            raise ProjectCatalogPersistenceError(self)
        if not original.usable:
            self._retain_uncertain(frame)
            raise ProjectCatalogPersistenceError(self)
        original.close_attempted = True
        try:
            original.cursor.close()
            original.close_returned = True
        except BaseException:
            self._retain_uncertain(frame)
            raise ProjectCatalogPersistenceError(self) from None

    def _execute_original(self, connection, sql, parameters=(), *, fetch=None):
        with self._lifetime_lock:
            frame = self._active_connections.get(id(connection))
        if frame is None:
            self._unavailable()  # Never manufacture another cursor or SQL connection.
        original = _CatalogCursorLifetime(execute_attempted=True)
        frame.cursors.append(original)  # BEFORE original execute/cursor allocation.
        try:
            original.cursor = connection.execute(sql, parameters)
            original.execute_returned = True
            if not callable(getattr(original.cursor, 'close', None)):
                raise ValueError('original cursor identity unavailable')
            original.usable = True
        except BaseException:
            self._retain_uncertain(frame)  # Unreturned cursor is NOT known no-allocation/closed.
            raise ProjectCatalogPersistenceError(self) from None
        try:
            if fetch == 'all':
                return [dict(row) for row in original.cursor.fetchall()]
            if fetch == 'one':
                row = original.cursor.fetchone()
                return None if row is None else dict(row)
            return None  # Same write statement, not a fabricated read/cursor receipt.
        except BaseException:
            self._unavailable()  # Original read/projection failed BEFORE independent cursor close.
        finally:
            self._close_cursor_original(frame, original)

    @contextmanager
    def _connect(self):
        if self.failed:
            raise ProjectCatalogPersistenceError(self)
        frame = _CatalogConnectionLifetime(connect_attempted=True)  # BEFORE original factory.
        try:
            frame.connection = connection = sqlite3.connect(self.database, timeout=5)
            frame.connect_returned = True
            if not all(callable(getattr(connection, name, None)) for name in ('execute', '__enter__', '__exit__', 'close')):
                raise ValueError('original connection identity unavailable')
            frame.usable = True
        except BaseException:
            self._retain_uncertain(frame)
            raise ProjectCatalogPersistenceError(self) from None
        with self._lifetime_lock:
            self._active_connections[id(connection)] = frame
        try:
            try:
                connection.row_factory = sqlite3.Row  # SAME original setup inside close ownership.
                if connection.__enter__() is not connection:
                    raise ValueError('original transaction identity unavailable')
                frame.transaction_entered = True
            except BaseException:
                self._unavailable()
            try:
                yield connection
            except BaseException as error:
                storage_failure = isinstance(error, (sqlite3.Error, OSError, ProjectCatalogPersistenceError))
                if storage_failure:
                    self._mark_failed()
                try:
                    suppressed = connection.__exit__(type(error), error, error.__traceback__)
                    frame.transaction_exit_returned = True
                except BaseException:
                    self._unavailable()
                if storage_failure:
                    self._unavailable()
                if not suppressed:
                    raise  # SAME original domain refusal/cancellation with known rollback/close.
            else:
                try:
                    connection.__exit__(None, None, None)
                    frame.transaction_exit_returned = True
                except BaseException:
                    self._unavailable()
        finally:
            self._close_connection_original(frame)
            with self._lifetime_lock:
                self._active_connections.pop(id(connection), None)  # Only after original close returned.

    def register(self, value: str | Path) -> dict:
        path = local_project_path(value)
        key = project_key(path)
        with self._connect() as connection:
            self._execute_original(connection, """INSERT INTO recent_projects(project_id,path,name,selected_at_ns)
                VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
                selected_at_ns=excluded.selected_at_ns, name=excluded.name""",
                (key, str(path), path.name or path.anchor, time.time_ns()))
            self._execute_original(connection, """DELETE FROM recent_projects WHERE project_id NOT IN (
                SELECT project_id FROM recent_projects
                ORDER BY MAX(selected_at_ns,opened_at_ns) DESC, project_id LIMIT 20
            )""")
        return self.get(key)

    def list(self) -> list[dict]:
        # Do not probe stale entries, scan repositories or visit remote paths.
        with self._connect() as connection:
            return self._execute_original(connection, """SELECT * FROM recent_projects
                ORDER BY MAX(selected_at_ns,opened_at_ns) DESC, project_id""", fetch='all')

    def get(self, key: str) -> dict:
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            raise ValueError("invalid project identity")
        with self._connect() as connection:
            row = self._execute_original(connection, "SELECT * FROM recent_projects WHERE project_id=?", (key,), fetch='one')
        if row is None:
            raise ValueError("unknown project identity")
        return row

    def resolve(self, key: str) -> Path:
        record = self.get(key)
        path = local_project_path(record["path"])
        if project_key(path) != key:
            raise ValueError("project path target has changed; select the directory again")
        return path

    def mark_opened(self, key: str) -> None:
        self.get(key)
        with self._connect() as connection:
            self._execute_original(connection, "UPDATE recent_projects SET opened_at_ns=? WHERE project_id=?", (time.time_ns(), key))
