"""SQLite-backed task DAG with validated state transitions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskManager:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    result TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    depends_on TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    PRIMARY KEY (task_id, depends_on)
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, status);
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _get(self, connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown task: {task_id}")
        return row

    def _check_deps(self, connection: sqlite3.Connection, run_id: str, dependencies: list[str]) -> None:
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("duplicate dependencies")
        for dep_id in dependencies:
            if self._get(connection, dep_id)["run_id"] != run_id:
                raise ValueError("dependency belongs to a different run")

    def create(self, run_id: str, title: str, dependencies: list[str] | None = None) -> str:
        dependencies = dependencies or []
        if not run_id or not title.strip() or len(title) > 500:
            raise ValueError("run_id and a 1-500 character title are required")
        with self._connect() as connection:
            self._check_deps(connection, run_id, dependencies)
            task_id = uuid4().hex
            now = _now()
            connection.execute(
                "INSERT INTO tasks(id,run_id,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (task_id, run_id, title.strip(), "pending", now, now),
            )
            connection.executemany(
                "INSERT INTO dependencies(task_id,depends_on) VALUES(?,?)",
                [(task_id, dep_id) for dep_id in dependencies],
            )
        return task_id

    def set_dependencies(self, task_id: str, dependencies: list[str]) -> None:
        with self._connect() as connection:
            task = self._get(connection, task_id)
            if task["status"] != "pending":
                raise ValueError("only pending tasks can change dependencies")
            self._check_deps(connection, task["run_id"], dependencies)
            if task_id in dependencies:
                raise ValueError("task cannot depend on itself")
            graph = {
                row["id"]: set() for row in connection.execute("SELECT id FROM tasks WHERE run_id=?", (task["run_id"],))
            }
            for row in connection.execute(
                "SELECT d.task_id,d.depends_on FROM dependencies d JOIN tasks t ON t.id=d.task_id WHERE t.run_id=?",
                (task["run_id"],),
            ):
                graph[row["task_id"]].add(row["depends_on"])
            graph[task_id] = set(dependencies)
            visiting, visited = set(), set()

            def visit(node: str) -> None:
                if node in visiting:
                    raise ValueError("task dependency cycle")
                if node in visited:
                    return
                visiting.add(node)
                for dep in graph[node]:
                    visit(dep)
                visiting.remove(node)
                visited.add(node)

            for node in graph:
                visit(node)
            connection.execute("DELETE FROM dependencies WHERE task_id=?", (task_id,))
            connection.executemany(
                "INSERT INTO dependencies(task_id,depends_on) VALUES(?,?)",
                [(task_id, dep_id) for dep_id in dependencies],
            )
            connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", (_now(), task_id))

    def list(self, run_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()
            tasks = [dict(row) for row in rows]
            for task in tasks:
                task["dependencies"] = [
                    row[0] for row in connection.execute(
                        "SELECT depends_on FROM dependencies WHERE task_id=? ORDER BY depends_on", (task["id"],)
                    )
                ]
            return tasks

    def ready(self, run_id: str) -> list[dict]:
        return [
            task for task in self.list(run_id)
            if task["status"] == "pending" and all(
                self.get(dep)["status"] == "completed" for dep in task["dependencies"]
            )
        ]

    def get(self, task_id: str) -> dict:
        with self._connect() as connection:
            return dict(self._get(connection, task_id))

    def transition(self, run_id: str, task_id: str, action: str, result: str = "") -> dict:
        with self._connect() as connection:
            task = self._get(connection, task_id)
            if task["run_id"] != run_id:
                raise ValueError("task belongs to a different run")
            current = task["status"]
            allowed = {
                "start": ("pending", "running"),
                "complete": ("running", "completed"),
                "fail": ("running", "failed"),
                "retry": ("failed", "pending"),
            }
            if action not in allowed or current != allowed[action][0]:
                raise ValueError(f"invalid task transition: {current} -> {action}")
            if action == "start":
                blockers = connection.execute("""
                    SELECT 1 FROM dependencies d JOIN tasks dep ON dep.id=d.depends_on
                    WHERE d.task_id=? AND dep.status!='completed' LIMIT 1
                """, (task_id,)).fetchone()
                if blockers:
                    raise ValueError("task dependencies are not completed")
            status = allowed[action][1]
            connection.execute(
                "UPDATE tasks SET status=?, attempts=attempts+?, result=?, updated_at=? WHERE id=?",
                (status, 1 if action == "start" else 0, result if action in ("complete", "fail") else task["result"], _now(), task_id),
            )
        return self.get(task_id)
