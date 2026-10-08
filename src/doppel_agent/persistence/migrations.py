"""Small forward-only schema migrator for runtime API state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .database import sqlite_connection


MIGRATIONS = {
    1: """
        CREATE TABLE IF NOT EXISTS runtime_runs (
            run_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            idempotency_key TEXT UNIQUE,
            request_json TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS runtime_events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_runtime_events_run_seq
            ON runtime_events(run_id, seq);
    """,
    2: """
        CREATE TABLE native_groups (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE native_conversations (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL CHECK(mode IN ('legacy','graph','deep')),
            title TEXT NOT NULL, profile_id TEXT,
            group_id TEXT REFERENCES native_groups(id) ON DELETE SET NULL,
            archived INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        ALTER TABLE runtime_runs ADD COLUMN conversation_id TEXT
            REFERENCES native_conversations(id);
        ALTER TABLE runtime_runs ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}';
        CREATE INDEX ix_runtime_runs_conversation ON runtime_runs(conversation_id, created_at, run_id);
        CREATE UNIQUE INDEX ix_native_active_turn ON runtime_runs(conversation_id)
            WHERE conversation_id IS NOT NULL AND status IN ('queued','running','interrupted');
        CREATE TABLE native_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES native_conversations(id),
            run_id TEXT NOT NULL REFERENCES runtime_runs(run_id),
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL, created_at TEXT NOT NULL, model TEXT,
            UNIQUE(run_id,role)
        );
        CREATE INDEX ix_native_messages_conversation ON native_messages(conversation_id,id);
    """,
    3: """
        ALTER TABLE runtime_runs ADD COLUMN lease_active INTEGER NOT NULL DEFAULT 0;
        UPDATE runtime_runs SET lease_active=1 WHERE conversation_id IS NOT NULL
            AND status IN ('queued','running','interrupted');
        CREATE UNIQUE INDEX ix_native_turn_lease ON runtime_runs(conversation_id)
            WHERE conversation_id IS NOT NULL AND lease_active=1;
    """,
    4: """
        CREATE TABLE native_workspace_selection (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            conversation_id TEXT REFERENCES native_conversations(id)
        );
        CREATE TABLE native_conversation_selection (
            conversation_id TEXT PRIMARY KEY REFERENCES native_conversations(id),
            run_id TEXT REFERENCES runtime_runs(run_id)
        );
    """,
    5: """
        CREATE TABLE work_orders (
            work_order_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('draft','queued','running','paused','succeeded','failed','cancelled')),
            active_revision INTEGER NOT NULL CHECK(active_revision > 0),
            idempotency_key TEXT UNIQUE,
            request_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE work_order_plans (
            work_order_id TEXT NOT NULL REFERENCES work_orders(work_order_id),
            revision INTEGER NOT NULL CHECK(revision > 0),
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(work_order_id, revision)
        );
        CREATE TABLE work_order_tasks (
            work_order_id TEXT NOT NULL, revision INTEGER NOT NULL,
            task_id TEXT NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
            title TEXT NOT NULL, prompt TEXT NOT NULL,
            access TEXT NOT NULL CHECK(access IN ('read','write')),
            mode TEXT NOT NULL CHECK(mode IN ('legacy','graph','deep')),
            profile_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','dispatching','running','awaiting_approval','succeeded','failed','cancelled','blocked')),
            PRIMARY KEY(work_order_id, revision, task_id),
            UNIQUE(work_order_id, revision, position),
            FOREIGN KEY(work_order_id, revision) REFERENCES work_order_plans(work_order_id, revision)
        );
        CREATE TABLE work_order_dependencies (
            work_order_id TEXT NOT NULL, revision INTEGER NOT NULL,
            task_id TEXT NOT NULL, depends_on TEXT NOT NULL,
            PRIMARY KEY(work_order_id, revision, task_id, depends_on),
            CHECK(task_id != depends_on),
            FOREIGN KEY(work_order_id, revision, task_id) REFERENCES work_order_tasks(work_order_id, revision, task_id),
            FOREIGN KEY(work_order_id, revision, depends_on) REFERENCES work_order_tasks(work_order_id, revision, task_id)
        );
        CREATE TABLE work_order_attempts (
            attempt_id TEXT PRIMARY KEY,
            work_order_id TEXT NOT NULL, revision INTEGER NOT NULL, task_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
            run_id TEXT UNIQUE REFERENCES runtime_runs(run_id),
            status TEXT NOT NULL CHECK(status IN ('reserved','accepted','running','awaiting_approval','succeeded','failed','cancelled','interrupted')),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(work_order_id, revision, task_id, attempt_number),
            FOREIGN KEY(work_order_id, revision, task_id) REFERENCES work_order_tasks(work_order_id, revision, task_id)
        );
        CREATE TABLE work_order_dispatch_intents (
            attempt_id TEXT PRIMARY KEY REFERENCES work_order_attempts(attempt_id),
            admission_key TEXT NOT NULL UNIQUE,
            request_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending','admitted','abandoned')),
            run_id TEXT UNIQUE REFERENCES runtime_runs(run_id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            CHECK(state != 'admitted' OR run_id IS NOT NULL)
        );
        CREATE INDEX ix_work_orders_updated ON work_orders(updated_at, work_order_id);
        CREATE INDEX ix_work_order_intents_state ON work_order_dispatch_intents(state, created_at);
    """,
    6: """
        ALTER TABLE work_orders ADD COLUMN execution_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE work_order_tasks ADD COLUMN retry_requested INTEGER NOT NULL DEFAULT 0 CHECK(retry_requested IN (0,1));
        CREATE UNIQUE INDEX ix_work_order_active_attempt ON work_order_attempts(work_order_id)
            WHERE status IN ('reserved','accepted','running','awaiting_approval','interrupted');
    """,
    7: """
        CREATE TABLE work_order_task_bindings (
            work_order_id TEXT NOT NULL,
            plan_revision INTEGER NOT NULL,
            task_id TEXT NOT NULL,
            execution_revision INTEGER NOT NULL,
            PRIMARY KEY(work_order_id, plan_revision, task_id),
            FOREIGN KEY(work_order_id, plan_revision, task_id)
                REFERENCES work_order_tasks(work_order_id, revision, task_id),
            FOREIGN KEY(work_order_id, execution_revision, task_id)
                REFERENCES work_order_tasks(work_order_id, revision, task_id)
        );
        INSERT INTO work_order_task_bindings(work_order_id,plan_revision,task_id,execution_revision)
            SELECT work_order_id,revision,task_id,revision FROM work_order_tasks;
        CREATE INDEX ix_work_order_task_execution
            ON work_order_task_bindings(work_order_id,execution_revision,task_id);
    """,
    8: """
        ALTER TABLE work_orders ADD COLUMN dispatch_error TEXT NOT NULL DEFAULT '';
    """,
    9: """
        ALTER TABLE work_order_dispatch_intents ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE runtime_runs ADD COLUMN write_scope INTEGER NOT NULL DEFAULT 0 CHECK(write_scope IN (0,1));
    """,
    10: """
        CREATE TABLE work_order_run_selection (
            work_order_id TEXT PRIMARY KEY REFERENCES work_orders(work_order_id) ON DELETE CASCADE,
            run_id TEXT REFERENCES runtime_runs(run_id) ON DELETE SET NULL
        );
        CREATE TABLE work_order_workspace_selection (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            work_order_id TEXT REFERENCES work_orders(work_order_id) ON DELETE SET NULL,
            revision INTEGER NOT NULL CHECK(revision>=1),
            request_key TEXT NOT NULL,
            request_json TEXT NOT NULL
        );
    """,
    11: """
        CREATE TABLE context_manifests (
            manifest_id TEXT PRIMARY KEY,
            request_key TEXT NOT NULL UNIQUE,
            request_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE project_notes (
            note_id TEXT PRIMARY KEY,
            scope_work_order_id TEXT REFERENCES work_orders(work_order_id),
            active_revision INTEGER NOT NULL CHECK(active_revision>=1),
            deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE project_note_revisions (
            note_id TEXT NOT NULL REFERENCES project_notes(note_id),
            revision INTEGER NOT NULL CHECK(revision>=1),
            request_key TEXT NOT NULL UNIQUE,
            request_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
            created_at TEXT NOT NULL,
            PRIMARY KEY(note_id,revision)
        );
        CREATE INDEX ix_project_notes_created ON project_notes(created_at,note_id);
    """,
    12: """
        CREATE TABLE context_selections (
            scope_key TEXT PRIMARY KEY,
            scope_work_order_id TEXT UNIQUE REFERENCES work_orders(work_order_id),
            manifest_id TEXT REFERENCES context_manifests(manifest_id),
            note_refs_json TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision>=1),
            request_key TEXT NOT NULL,
            request_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK(scope_key=COALESCE(scope_work_order_id,'project'))
        );
    """,
    13: """
        ALTER TABLE runtime_runs ADD COLUMN input_snapshot_json TEXT;
    """,
    14: """
        CREATE TABLE work_order_context_bindings (
            work_order_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            descriptor_json TEXT,
            input_snapshot_json TEXT,
            PRIMARY KEY(work_order_id,revision),
            FOREIGN KEY(work_order_id,revision) REFERENCES work_order_plans(work_order_id,revision)
        );
        INSERT INTO work_order_context_bindings(work_order_id,revision)
            SELECT work_order_id,revision FROM work_order_plans;
        ALTER TABLE work_order_dispatch_intents ADD COLUMN input_snapshot_json TEXT;
    """,
}


def apply_migrations(database: Path) -> int:
    with sqlite_connection(database) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        connection.execute("BEGIN IMMEDIATE")
        applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
        for version, sql in sorted(MIGRATIONS.items()):
            if version in applied:
                continue
            # executescript implicitly commits before executing. Keep additive DDL
            # and its version marker under one transaction (also serializes two
            # process-equivalent store constructors). These migrations contain
            # plain statements, no triggers/quoted semicolons.
            for statement in sql.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (version, datetime.now(UTC).isoformat()),
            )
    return max(MIGRATIONS, default=0)
