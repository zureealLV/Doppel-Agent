"""Work-order plans, serialized attempts and durable dispatch intents.

This store is not the per-run TaskManager and does not dispatch or grant access.
Runtime admission remains the responsibility of RunService, never this store.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..tasks.work_orders import (
    MAX_ATTEMPTS_PER_TASK, WorkOrderPlan, dispatch_request, execution_settings, plan_from_payload,
)
from .database import sqlite_connection
from .migrations import apply_migrations


class WorkOrderStore:
    def __init__(self, database: Path, *, input_resolver: Callable | None = None):
        self.database = database
        self.input_resolver = input_resolver
        apply_migrations(database)

    @staticmethod
    def _context_binding(db, identifier: str, revision: int) -> dict:
        # Historical-prefix migration fixtures may project before migration 14.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_order_context_bindings'").fetchone():
            return {"saved": False, "descriptor": None, "snapshot": None, "bound_revision": None}
        row = db.execute("SELECT * FROM work_order_context_bindings WHERE work_order_id=? AND revision=?", (identifier, revision)).fetchone()
        # Carried snapshots retain byte-identical JSON, including capture time.
        # Report their original resolution revision, not a new confirmation at
        # every plan edit. This is provenance, not an operation idempotency key.
        origin = db.execute("""SELECT MIN(revision) FROM work_order_context_bindings
            WHERE work_order_id=? AND input_snapshot_json=? AND descriptor_json IS ?""",
            (identifier, row["input_snapshot_json"], row["descriptor_json"])).fetchone()[0] if row and row["input_snapshot_json"] else None
        return {"saved": row is not None, "descriptor": json.loads(row["descriptor_json"]) if row and row["descriptor_json"] else None,
            "snapshot": json.loads(row["input_snapshot_json"]) if row and row["input_snapshot_json"] else None, "bound_revision": origin}

    @staticmethod
    def _save_context_binding(db, identifier: str, revision: int, descriptor: dict | None, snapshot: dict | None):
        db.execute("""INSERT INTO work_order_context_bindings VALUES (?,?,?,?)
            ON CONFLICT(work_order_id,revision) DO UPDATE SET
            descriptor_json=excluded.descriptor_json,input_snapshot_json=excluded.input_snapshot_json""",
            (identifier, revision, json.dumps(descriptor, ensure_ascii=False, sort_keys=True) if descriptor is not None else None,
             json.dumps(snapshot, ensure_ascii=False, sort_keys=True) if snapshot is not None else None))

    def _resolve_context(self, db, identifier: str, descriptor: dict | None):
        if descriptor is None:
            return None
        if self.input_resolver is None:
            raise ValueError("context resolver required")
        return self.input_resolver(db, descriptor, scope_work_order_id=identifier)

    @staticmethod
    def _identifier(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("invalid work order id")
        return value

    def create(self, plan: WorkOrderPlan, idempotency_key: str | None = None) -> tuple[dict[str, Any], bool]:
        if not isinstance(plan, WorkOrderPlan):
            raise ValueError("invalid work order plan")
        # Callers cannot bypass limits/DAG validation with manually built records.
        plan = plan_from_payload(plan.payload())
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200
            or "\x00" in idempotency_key
        ):
            raise ValueError("invalid idempotency key")
        encoded = json.dumps(plan.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        now = datetime.now(UTC).isoformat()
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                existing = db.execute("SELECT * FROM work_orders WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if existing:
                    if existing["request_json"] != encoded:
                        raise ValueError("idempotency key was reused with a different request")
                    return self._project(db, existing), False
            identifier = uuid4().hex
            db.execute("""
                INSERT INTO work_orders(work_order_id,title,status,active_revision,idempotency_key,request_json,created_at,updated_at)
                VALUES (?,?,'draft',1,?,?,?,?)
            """, (identifier, plan.title, idempotency_key, encoded, now, now))
            self._insert_plan(db, identifier, 1, plan, now)
            row = db.execute("SELECT * FROM work_orders WHERE work_order_id=?", (identifier,)).fetchone()
            return self._project(db, row), True

    @staticmethod
    def _insert_plan(db: sqlite3.Connection, identifier: str, revision: int, plan: WorkOrderPlan, now: str) -> None:
        encoded = json.dumps(plan.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        db.execute("INSERT INTO work_order_plans VALUES (?,?,?,?)", (identifier, revision, encoded, now))
        for position, task in enumerate(plan.tasks):
            db.execute("""
                INSERT INTO work_order_tasks(work_order_id,revision,task_id,position,title,prompt,access,mode,profile_id)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (identifier, revision, task.id, position, task.title, task.prompt, task.access, task.mode, task.profile_id))
        # Edges follow all nodes, independent of presentation order.
        for task in plan.tasks:
            db.executemany("INSERT INTO work_order_dependencies VALUES (?,?,?,?)",
                           [(identifier, revision, task.id, dep) for dep in task.dependencies])
        db.executemany("INSERT INTO work_order_task_bindings VALUES (?,?,?,?)",
                       [(identifier, revision, task.id, revision) for task in plan.tasks])

    def replace_draft_plan(
        self, work_order_id: str, plan: WorkOrderPlan, *, expected_revision: int,
    ) -> dict[str, Any]:
        identifier = self._identifier(work_order_id)
        if type(expected_revision) is not int or expected_revision < 1 or not isinstance(plan, WorkOrderPlan):
            raise ValueError("invalid plan replacement")
        plan = plan_from_payload(plan.payload())
        now = datetime.now(UTC).isoformat()
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM work_orders WHERE work_order_id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError("work order not found")
            if row["active_revision"] != expected_revision:
                raise ValueError("plan revision changed; reload before editing")
            attempts = db.execute("SELECT 1 FROM work_order_attempts WHERE work_order_id=? LIMIT 1", (identifier,)).fetchone()
            if row["status"] != "draft" or attempts:
                raise ValueError("only an unstarted draft can replace its entire plan")
            revision = expected_revision + 1
            self._insert_plan(db, identifier, revision, plan, now)
            db.execute("UPDATE work_orders SET title=?,active_revision=?,updated_at=? WHERE work_order_id=?",
                       (plan.title, revision, now, identifier))
            row = db.execute("SELECT * FROM work_orders WHERE work_order_id=?", (identifier,)).fetchone()
            return self._project(db, row)

    def plan(self, work_order_id: str, revision: int) -> dict[str, Any] | None:
        identifier = self._identifier(work_order_id)
        if type(revision) is not int or revision < 1:
            raise ValueError("invalid plan revision")
        with sqlite_connection(self.database) as db:
            row = db.execute("SELECT plan_json FROM work_order_plans WHERE work_order_id=? AND revision=?",
                             (identifier, revision)).fetchone()
            return json.loads(row["plan_json"]) if row else None

    def revise_plan(
        self, work_order_id: str, plan: WorkOrderPlan, *, expected_revision: int, profiles: dict | None = None,
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Append a plan; only never-dispatched definitions may change.

        Started definitions keep their original execution revision and attempts.
        The edited execution is paused until explicit resume; live IO is neither
        cancelled nor replaced. Drafts remain drafts and acquire no grants.
        """
        identifier = self._identifier(work_order_id)
        if not isinstance(plan, WorkOrderPlan):
            raise ValueError("invalid work order plan")
        plan = plan_from_payload(plan.payload())
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._order(db, identifier, expected_revision)
            self._sync(db, identifier)
            row = self._order(db, identifier, expected_revision)
            if row["status"] in {"succeeded", "cancelled"}:
                raise ValueError("terminal work order cannot revise its plan")
            if context is not None and row["status"] == "draft":
                raise ValueError("draft context must be bound at explicit activation")
            execution = json.loads(row["execution_json"])
            binding = self._context_binding(db, identifier, expected_revision)
            if context is not None:
                execution = execution_settings({**execution, "context": context})
                binding = {"descriptor": execution["context"], "snapshot": self._resolve_context(db, identifier, execution["context"])}
            if profiles is not None:
                existing = execution.get("profiles", {})
                if any(profiles.get(identifier) != snapshot for identifier, snapshot in existing.items()):
                    raise ValueError("frozen model profiles cannot change")
                execution = execution_settings({**execution, "profiles": profiles})
            previous = db.execute("SELECT plan_json FROM work_order_plans WHERE work_order_id=? AND revision=?",
                                  (identifier, expected_revision)).fetchone()
            old_plan = plan_from_payload(json.loads(previous["plan_json"]))
            old_definitions = {task.id: task for task in old_plan.tasks}
            new_definitions = {task.id: task for task in plan.tasks}
            started = {r[0] for r in db.execute("SELECT DISTINCT task_id FROM work_order_attempts WHERE work_order_id=?",
                                              (identifier,))}
            for task_id in started:
                if task_id not in new_definitions or new_definitions[task_id] != old_definitions.get(task_id):
                    raise ValueError("started task definitions cannot change or disappear")
            old_tasks = {r["task_id"]: r for r in db.execute("""
                SELECT t.*,b.execution_revision FROM work_order_tasks t JOIN work_order_task_bindings b
                ON b.work_order_id=t.work_order_id AND b.plan_revision=t.revision AND b.task_id=t.task_id
                WHERE t.work_order_id=? AND t.revision=?
            """, (identifier, expected_revision))}
            revision = expected_revision + 1
            now = self._now()
            self._insert_plan(db, identifier, revision, plan, now)
            self._save_context_binding(db, identifier, revision, binding["descriptor"], binding["snapshot"])
            for task_id in started:
                old_task = old_tasks[task_id]
                db.execute("""
                    UPDATE work_order_task_bindings SET execution_revision=?
                    WHERE work_order_id=? AND plan_revision=? AND task_id=?
                """, (old_task["execution_revision"], identifier, revision, task_id))
                db.execute("""
                    UPDATE work_order_tasks SET status=?,retry_requested=?
                    WHERE work_order_id=? AND revision=? AND task_id=?
                """, (old_task["status"], old_task["retry_requested"], identifier, revision, task_id))
            status = "draft" if row["status"] == "draft" else "paused"
            db.execute("UPDATE work_orders SET title=?,active_revision=?,status=?,updated_at=?,execution_json=? WHERE work_order_id=?",
                       (plan.title, revision, status, now, json.dumps(execution, ensure_ascii=False, sort_keys=True, separators=(",", ":")), identifier))
            self._refresh_order(db, identifier)
            return self._project(db, self._order(db, identifier))

    def get(self, work_order_id: str) -> dict[str, Any] | None:
        identifier = self._identifier(work_order_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM work_orders WHERE work_order_id=?", (identifier,)).fetchone()
            return self._project(db, row) if row else None

    def list(self, limit: int = 50, *, before_id: str | None = None, search: str = "") -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid work order list limit")
        if before_id is not None:
            self._identifier(before_id)
        if not isinstance(search, str) or len(search) > 200 or "\x00" in search:
            raise ValueError("invalid work order search")
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            conditions, parameters = [], []
            if before_id is not None:
                cursor = db.execute("SELECT created_at,work_order_id FROM work_orders WHERE work_order_id=?",
                                    (before_id,)).fetchone()
                if cursor is None:
                    raise ValueError("work order list cursor not found")
                conditions.append("(created_at<? OR (created_at=? AND work_order_id<?))")
                parameters = [cursor["created_at"], cursor["created_at"], before_id]
            if search.strip():
                conditions.append("(instr(lower(title),lower(?))>0 OR instr(work_order_id,?)>0)")
                parameters.extend([search.strip(), search.strip()])
            clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            rows = db.execute("""
                SELECT work_order_id,title,status,active_revision,created_at,updated_at
                FROM work_orders """ + clause + " ORDER BY created_at DESC,work_order_id DESC LIMIT ?",
                (*parameters, limit)).fetchall()
            return [dict(row) for row in rows]

    def candidates(self, *, after_id: str = "", limit: int = 50) -> list[str]:
        if after_id:
            self._identifier(after_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid candidate page size")
        with sqlite_connection(self.database) as db:
            return [row[0] for row in db.execute("""
                SELECT work_order_id FROM work_orders o WHERE work_order_id>?
                AND (status IN ('queued','running') OR EXISTS (
                    SELECT 1 FROM work_order_attempts a WHERE a.work_order_id=o.work_order_id
                    AND a.status IN ('reserved','accepted','running','awaiting_approval','interrupted')
                )) AND (dispatch_error='' OR status='paused') ORDER BY work_order_id LIMIT ?
            """, (after_id, limit))]

    @staticmethod
    def _run_selection(db: sqlite3.Connection, identifier: str) -> dict[str, Any]:
        row = db.execute("""
            SELECT s.work_order_id,r.run_id FROM work_order_run_selection s
            LEFT JOIN work_order_attempts a ON a.run_id=s.run_id AND a.work_order_id=s.work_order_id
            LEFT JOIN runtime_runs r ON r.run_id=a.run_id WHERE s.work_order_id=?
        """, (identifier,)).fetchone()
        return {"saved": row is not None, "work_order_id": identifier, "run_id": row["run_id"] if row else None}

    @classmethod
    def _selection(cls, db: sqlite3.Connection) -> dict[str, Any]:
        row = db.execute("SELECT * FROM work_order_workspace_selection WHERE singleton=1").fetchone()
        identifier = row["work_order_id"] if row else None
        run = cls._run_selection(db, identifier)["run_id"] if identifier else None
        return {"saved": row is not None, "work_order_id": identifier, "run_id": run,
                "revision": row["revision"] if row else 0, "request_key": row["request_key"] if row else None}

    def selection(self) -> dict[str, Any]:
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            return self._selection(db)

    def run_selection(self, work_order_id: str) -> dict[str, Any]:
        identifier = self._identifier(work_order_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            self._order(db, identifier)
            return self._run_selection(db, identifier)

    def set_selection(
        self, work_order_id: str | None, run_id: str | None, *, expected_revision: int, idempotency_key: str,
    ) -> dict[str, Any]:
        if work_order_id is not None:
            self._identifier(work_order_id)
        if run_id is not None:
            self._identifier(run_id)
            if work_order_id is None:
                raise ValueError("run selection requires a work order")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid selection revision")
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128 or "\x00" in idempotency_key:
            raise ValueError("invalid selection request key")
        encoded = json.dumps({"work_order_id": work_order_id, "run_id": run_id, "expected_revision": expected_revision}, sort_keys=True)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM work_order_workspace_selection WHERE singleton=1").fetchone()
            if row and row["request_key"] == idempotency_key:
                if row["request_json"] != encoded:
                    raise ValueError("selection request key was reused with a different request")
                return self._selection(db)
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise ValueError("work-order selection revision changed")
            if work_order_id is not None:
                self._order(db, work_order_id)
                if run_id is not None and not db.execute("""
                    SELECT 1 FROM work_order_attempts a JOIN runtime_runs r USING(run_id)
                    WHERE a.work_order_id=? AND a.run_id=?
                """, (work_order_id, run_id)).fetchone():
                    raise ValueError("run not found in this work order")
                db.execute("""
                    INSERT INTO work_order_run_selection(work_order_id,run_id) VALUES (?,?)
                    ON CONFLICT(work_order_id) DO UPDATE SET run_id=excluded.run_id
                """, (work_order_id, run_id))
            db.execute("""
                INSERT INTO work_order_workspace_selection(singleton,work_order_id,revision,request_key,request_json)
                VALUES (1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET work_order_id=excluded.work_order_id,
                revision=excluded.revision,request_key=excluded.request_key,request_json=excluded.request_json
            """, (work_order_id, revision + 1, idempotency_key, encoded))
            return self._selection(db)

    def activate(self, work_order_id: str, *, expected_revision: int, settings: dict[str, Any]) -> dict[str, Any]:
        identifier = self._identifier(work_order_id)
        options = execution_settings(settings)
        encoded = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._order(db, identifier, expected_revision)
            if row["status"] != "draft" and row["execution_json"] == encoded:
                return self._project(db, row)
            if row["status"] != "draft":
                raise ValueError("only a draft can activate a new execution scope")
            frozen = self._resolve_context(db, identifier, options.get("context"))
            self._save_context_binding(db, identifier, expected_revision, options.get("context"), frozen)
            db.execute("UPDATE work_orders SET status='queued',execution_json=?,updated_at=? WHERE work_order_id=?",
                       (encoded, self._now(), identifier))
            return self._project(db, self._order(db, identifier, expected_revision))

    def control(self, work_order_id: str, action: str, *, expected_revision: int) -> dict[str, Any]:
        identifier = self._identifier(work_order_id)
        if action not in {"pause", "resume", "cancel"}:
            raise ValueError("invalid work order action")
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._order(db, identifier, expected_revision)
            self._sync(db, identifier)
            row = self._order(db, identifier, expected_revision)
            before = row["status"]
            if action == "pause" and before in {"queued", "running", "paused"}:
                after = "paused"
            elif action == "resume" and before == "paused":
                after = "queued"
            elif action == "cancel" and before != "succeeded":
                after = "cancelled"
                db.execute("""
                    UPDATE work_order_tasks SET status='cancelled'
                    WHERE work_order_id=? AND revision=? AND status IN ('pending','blocked')
                """, (identifier, expected_revision))
            else:
                raise ValueError("invalid work order status transition")
            db.execute("UPDATE work_orders SET status=?,updated_at=? WHERE work_order_id=?",
                       (after, self._now(), identifier))
            if action in {"resume", "cancel"}:
                db.execute("UPDATE work_orders SET dispatch_error='' WHERE work_order_id=?", (identifier,))
            # Reserved/admitted work remains visible until S3 cancels and drains
            # it. A control operation must not assert termination of runtime IO.
            return self._project(db, self._order(db, identifier, expected_revision))

    def reserve_next(self, work_order_id: str, *, expected_revision: int) -> dict[str, Any] | None:
        identifier = self._identifier(work_order_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._order(db, identifier, expected_revision)
            self._sync(db, identifier)
            row = self._order(db, identifier, expected_revision)
            ready = self._ready(db, row)
            if not ready:
                return None
            task = ready[0]
            execution_revision = task["execution_revision"]
            number = db.execute("""
                SELECT COALESCE(MAX(attempt_number),0)+1 FROM work_order_attempts
                WHERE work_order_id=? AND revision=? AND task_id=?
            """, (identifier, execution_revision, task["task_id"])).fetchone()[0]
            if number > MAX_ATTEMPTS_PER_TASK:
                raise ValueError("task attempt limit reached")
            attempt_id = uuid4().hex
            admission_key = f"work-order:{attempt_id}"
            scope = json.loads(row["execution_json"])
            binding = WorkOrderStore._context_binding(db, identifier, execution_revision)
            if not binding["saved"] or binding["descriptor"] is not None and binding["snapshot"] is None:
                raise ValueError("work-order context binding missing")
            scope.pop("context", None)
            if binding["descriptor"] is not None:
                scope["context"] = binding["descriptor"]
            request = dispatch_request(dict(task), scope, admission_key)
            if number > 1:
                previous = db.execute("""SELECT i.input_snapshot_json FROM work_order_attempts a
                    JOIN work_order_dispatch_intents i USING(attempt_id)
                    WHERE a.work_order_id=? AND a.revision=? AND a.task_id=?
                    ORDER BY a.attempt_number DESC LIMIT 1""", (identifier, execution_revision, task["task_id"])).fetchone()
                frozen_input = json.loads(previous[0]) if previous[0] else None
            else:
                from ..context.predecessors import bind_predecessors

                frozen_input = bind_predecessors(db, identifier, expected_revision, dict(task), binding["snapshot"])
            snapshot = scope.get("profiles", {}).get(request["profile_id"], {})
            now = self._now()
            db.execute("""
                INSERT INTO work_order_attempts(attempt_id,work_order_id,revision,task_id,attempt_number,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'reserved',?,?)
            """, (attempt_id, identifier, execution_revision, task["task_id"], number, now, now))
            db.execute("""
                INSERT INTO work_order_dispatch_intents(attempt_id,admission_key,request_json,state,created_at,updated_at,profile_json,input_snapshot_json)
                VALUES (?,?,?,'pending',?,?,?,?)
            """, (attempt_id, admission_key, json.dumps(request, ensure_ascii=False, sort_keys=True), now, now,
                    json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    json.dumps(frozen_input, ensure_ascii=False, sort_keys=True) if frozen_input is not None else None))
            self._set_task_state(db, identifier, execution_revision, task["task_id"], "dispatching", retry_requested=0)
            db.execute("UPDATE work_orders SET status='running',updated_at=? WHERE work_order_id=?", (now, identifier))
            return self._intent(db, attempt_id)

    def ready_tasks(self, work_order_id: str, *, expected_revision: int) -> list[dict[str, Any]]:
        """Observes reconciled readiness, without creating an attempt or run."""
        identifier = self._identifier(work_order_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._order(db, identifier, expected_revision)
            self._sync(db, identifier)
            return [dict(task) for task in self._ready(db, self._order(db, identifier, expected_revision))]

    @staticmethod
    def _ready(db: sqlite3.Connection, row: sqlite3.Row) -> list[sqlite3.Row]:
        identifier, revision = row["work_order_id"], row["active_revision"]
        if row["status"] not in {"queued", "running"}:
            return []
        if db.execute("""
            SELECT 1 FROM work_order_attempts WHERE work_order_id=?
            AND status IN ('reserved','accepted','running','awaiting_approval','interrupted') LIMIT 1
        """, (identifier,)).fetchone():
            return []
        return db.execute("""
            SELECT t.*,b.execution_revision FROM work_order_tasks t JOIN work_order_task_bindings b
            ON b.work_order_id=t.work_order_id AND b.plan_revision=t.revision AND b.task_id=t.task_id
            WHERE t.work_order_id=? AND t.revision=? AND t.status='pending'
            AND NOT EXISTS (
                SELECT 1 FROM work_order_dependencies d JOIN work_order_tasks parent
                ON parent.work_order_id=d.work_order_id AND parent.revision=d.revision AND parent.task_id=d.depends_on
                WHERE d.work_order_id=t.work_order_id AND d.revision=t.revision AND d.task_id=t.task_id
                AND parent.status!='succeeded'
            ) ORDER BY position LIMIT 64
        """, (identifier, revision)).fetchall()

    def pending_intents(self, work_order_id: str) -> list[dict[str, Any]]:
        identifier = self._identifier(work_order_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN")
            ids = db.execute("""
                SELECT i.attempt_id FROM work_order_dispatch_intents i JOIN work_order_attempts a USING(attempt_id)
                WHERE a.work_order_id=? AND i.state='pending' ORDER BY i.created_at,i.attempt_id
            """, (identifier,)).fetchall()
            return [self._intent(db, item[0]) for item in ids]

    def intent_by_key(self, admission_key: str) -> dict[str, Any] | None:
        if not isinstance(admission_key, str) or not re.fullmatch(r"work-order:[0-9a-f]{32}", admission_key):
            raise ValueError("invalid dispatch admission key")
        with sqlite_connection(self.database) as db:
            row = db.execute("SELECT attempt_id FROM work_order_dispatch_intents WHERE admission_key=?", (admission_key,)).fetchone()
            return self._intent(db, row[0]) if row else None

    def reconcile(self, work_order_id: str) -> dict[str, Any]:
        identifier = self._identifier(work_order_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._order(db, identifier)
            self._sync(db, identifier)
            return self._project(db, self._order(db, identifier))

    def record_dispatch_error(self, work_order_id: str, code: str) -> dict[str, Any]:
        """Persist only a stable class; never raw exception/provider/prompt data."""
        identifier = self._identifier(work_order_id)
        if code not in {"admission_failed", "admission_rejected", "admission_not_persisted",
                        "reconciliation_failed", "runtime_cancel_failed"}:
            raise ValueError("invalid dispatch error code")
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._order(db, identifier)
            state = "paused" if row["status"] in {"queued", "running", "paused"} else row["status"]
            db.execute("UPDATE work_orders SET status=?,dispatch_error=?,updated_at=? WHERE work_order_id=?",
                       (state, code, self._now(), identifier))
            return self._project(db, self._order(db, identifier))

    def abandon_unaccepted(self, attempt_id: str, *, admission_settled: bool) -> dict[str, Any]:
        """Only S3's serialized admission owner may prove no acceptance in flight.

        Missing a run row alone is NOT that proof. Do not call this from a
        concurrent HTTP cancel path or after an ambiguous acceptance timeout.
        """
        if admission_settled is not True:
            raise ValueError("admission must be settled under its serialized owner")
        attempt_id = self._identifier(attempt_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            intent = self._intent(db, attempt_id)
            if intent is None:
                raise ValueError("dispatch intent not found")
            identifier = intent["work_order_id"]
            self._sync(db, identifier)
            intent = self._intent(db, attempt_id)
            if intent["state"] != "pending":
                raise ValueError("dispatch already accepted or abandoned")
            order = self._order(db, identifier)
            outcome = "cancelled" if order["status"] == "cancelled" else "failed"
            now = self._now()
            db.execute("UPDATE work_order_dispatch_intents SET state='abandoned',updated_at=? WHERE attempt_id=?", (now, attempt_id))
            db.execute("UPDATE work_order_attempts SET status=?,updated_at=? WHERE attempt_id=?", (outcome, now, attempt_id))
            self._set_task_state(db, identifier, intent["revision"], intent["task_id"], outcome)
            self._refresh_order(db, identifier)
            return self._project(db, self._order(db, identifier))

    def retry_task(
        self, work_order_id: str, task_id: str, *, expected_revision: int, expected_attempt_id: str,
    ) -> dict[str, Any]:
        identifier = self._identifier(work_order_id)
        expected_attempt_id = self._identifier(expected_attempt_id)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            self._order(db, identifier, expected_revision)
            self._sync(db, identifier)
            order = self._order(db, identifier, expected_revision)
            if order["status"] not in {"failed", "paused"}:
                raise ValueError("only stopped failed work can be retried")
            if db.execute("""
                SELECT 1 FROM work_order_attempts WHERE work_order_id=?
                AND status IN ('reserved','accepted','running','awaiting_approval','interrupted') LIMIT 1
            """, (identifier,)).fetchone():
                raise ValueError("previous attempt is still draining")
            binding = db.execute("""
                SELECT execution_revision FROM work_order_task_bindings
                WHERE work_order_id=? AND plan_revision=? AND task_id=?
            """, (identifier, expected_revision, task_id)).fetchone()
            if binding is None:
                raise ValueError("task not found in current plan")
            execution_revision = binding[0]
            attempt = db.execute("""
                SELECT * FROM work_order_attempts WHERE work_order_id=? AND revision=? AND task_id=?
                ORDER BY attempt_number DESC LIMIT 1
            """, (identifier, execution_revision, task_id)).fetchone()
            if attempt is None or attempt["attempt_id"] != expected_attempt_id or attempt["status"] != "failed":
                raise ValueError("retry requires the latest failed attempt")
            if attempt["attempt_number"] >= MAX_ATTEMPTS_PER_TASK:
                raise ValueError("task attempt limit reached")
            self._set_task_state(db, identifier, execution_revision, task_id, "pending", retry_requested=1)
            # Recompute blocked descendants, never reset a succeeded node or
            # erase its attempt/run audit. Retry itself does not dispatch.
            db.execute("UPDATE work_order_tasks SET status='pending' WHERE work_order_id=? AND revision=? AND status='blocked'",
                       (identifier, expected_revision))
            db.execute("UPDATE work_orders SET status='paused',updated_at=? WHERE work_order_id=?", (self._now(), identifier))
            self._refresh_order(db, identifier)
            return self._project(db, self._order(db, identifier))

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _set_task_state(
        db: sqlite3.Connection, identifier: str, execution_revision: int, task_id: str, status: str,
        *, retry_requested: int | None = None,
    ) -> None:
        # A carried node has one execution definition but projections in several
        # immutable authored plans. Never attach the same run to a new attempt.
        where = """
            WHERE work_order_id=? AND task_id=? AND revision IN (
                SELECT plan_revision FROM work_order_task_bindings
                WHERE work_order_id=? AND execution_revision=? AND task_id=?
            )
        """
        parameters = (identifier, task_id, identifier, execution_revision, task_id)
        if retry_requested is None:
            db.execute("UPDATE work_order_tasks SET status=? " + where + " AND retry_requested=0 AND status!=?",
                       (status, *parameters, status))
        else:
            db.execute("UPDATE work_order_tasks SET status=?,retry_requested=? " + where,
                       (status, retry_requested, *parameters))

    @staticmethod
    def _order(db: sqlite3.Connection, identifier: str, revision: int | None = None) -> sqlite3.Row:
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError("invalid plan revision")
        row = db.execute("SELECT * FROM work_orders WHERE work_order_id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("work order not found")
        if revision is not None and row["active_revision"] != revision:
            raise ValueError("plan revision changed; reload before acting")
        return row

    @staticmethod
    def _intent(db: sqlite3.Connection, attempt_id: str) -> dict[str, Any] | None:
        row = db.execute("""
            SELECT i.*,a.work_order_id,a.revision,a.task_id,a.attempt_number,a.status AS attempt_status,t.access
            FROM work_order_dispatch_intents i JOIN work_order_attempts a USING(attempt_id)
            JOIN work_order_tasks t ON t.work_order_id=a.work_order_id AND t.revision=a.revision AND t.task_id=a.task_id
            WHERE i.attempt_id=?
        """, (attempt_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["profile_snapshot"] = json.loads(result.pop("profile_json"))
        encoded_input = result.pop("input_snapshot_json")
        result["input_snapshot"] = json.loads(encoded_input) if encoded_input else None
        return result

    def _sync(self, db: sqlite3.Connection, identifier: str) -> None:
        rows = db.execute("""
            SELECT a.attempt_id,a.revision,a.task_id,a.status AS attempt_status,i.state,i.request_json,i.profile_json,i.input_snapshot_json AS intent_input,
                   r.run_id,r.request_json AS runtime_request,r.status AS runtime_status,r.lease_active,
                   r.profile_json AS runtime_profile,r.write_scope,t.access,r.input_snapshot_json AS runtime_input
            FROM work_order_attempts a JOIN work_order_dispatch_intents i USING(attempt_id)
            JOIN work_order_tasks t ON t.work_order_id=a.work_order_id AND t.revision=a.revision AND t.task_id=a.task_id
            LEFT JOIN runtime_runs r ON r.idempotency_key=i.admission_key WHERE a.work_order_id=?
        """, (identifier,)).fetchall()
        now = self._now()
        for row in rows:
            if row["run_id"] is None:
                continue  # Uncertain admission stays reserved, not replayed.
            if json.loads(row["runtime_request"]) != json.loads(row["request_json"]):
                raise ValueError("dispatch admission key matched a different runtime request")
            frozen = json.loads(row["profile_json"])
            if frozen and (json.loads(row["runtime_profile"]) != frozen or bool(row["write_scope"]) != (row["access"] == "write")):
                raise ValueError("dispatch runtime profile or write scope changed")
            if (json.loads(row["intent_input"]) if row["intent_input"] else None) != (json.loads(row["runtime_input"]) if row["runtime_input"] else None):
                raise ValueError("dispatch runtime input snapshot changed")
            if row["state"] == "abandoned":
                raise ValueError("runtime appeared after abandoned dispatch; reconciliation required")
            db.execute("""
                UPDATE work_order_dispatch_intents SET state='admitted',run_id=?,updated_at=?
                WHERE attempt_id=? AND (state!='admitted' OR run_id IS NOT ?)
            """, (row["run_id"], now, row["attempt_id"], row["run_id"]))
            status = row["runtime_status"]
            if status in {"completed", "failed", "cancelled", "interrupted_expired"}:
                if row["lease_active"]:
                    # Terminal outcome is visible before durable IO is drained.
                    attempt_status, task_status = "running", "running"
                else:
                    attempt_status = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled",
                                      "interrupted_expired": "failed"}[status]
                    task_status = attempt_status
            elif status == "interrupted":
                attempt_status, task_status = "awaiting_approval", "awaiting_approval"
            elif status == "queued":
                attempt_status, task_status = "accepted", "dispatching"
            elif status == "running":
                attempt_status, task_status = "running", "running"
            else:
                raise ValueError("unknown runtime status during reconciliation")
            db.execute("""
                UPDATE work_order_attempts SET run_id=?,status=?,updated_at=?
                WHERE attempt_id=? AND (run_id IS NOT ? OR status!=?)
            """, (row["run_id"], attempt_status, now, row["attempt_id"], row["run_id"], attempt_status))
            # A terminal old attempt must not overwrite a later explicit retry.
            latest = db.execute("""
                SELECT attempt_id FROM work_order_attempts WHERE work_order_id=? AND revision=? AND task_id=?
                ORDER BY attempt_number DESC LIMIT 1
            """, (identifier, row["revision"], row["task_id"])).fetchone()[0]
            if latest == row["attempt_id"]:
                self._set_task_state(db, identifier, row["revision"], row["task_id"], task_status)
        self._refresh_order(db, identifier)

    def _refresh_order(self, db: sqlite3.Connection, identifier: str) -> None:
        row = self._order(db, identifier)
        revision = row["active_revision"]
        # Propagate failed ancestry even when descendants precede parents in UI.
        while True:
            changed = db.execute("""
                UPDATE work_order_tasks AS t SET status='blocked' WHERE t.work_order_id=? AND t.revision=? AND t.status='pending'
                AND EXISTS (
                    SELECT 1 FROM work_order_dependencies d JOIN work_order_tasks p
                    ON p.work_order_id=d.work_order_id AND p.revision=d.revision AND p.task_id=d.depends_on
                    WHERE d.work_order_id=t.work_order_id AND d.revision=t.revision AND d.task_id=t.task_id
                    AND p.status IN ('failed','cancelled','blocked')
                )
            """, (identifier, revision)).rowcount
            if not changed:
                break
        states = [r[0] for r in db.execute("SELECT status FROM work_order_tasks WHERE work_order_id=? AND revision=?",
                                         (identifier, revision))]
        status = row["status"]
        if status not in {"draft", "cancelled"}:
            if all(state == "succeeded" for state in states):
                status = "succeeded"
            elif any(state in {"failed", "cancelled"} for state in states):
                status = "failed" if status != "paused" else "paused"
        if status != row["status"]:
            db.execute("UPDATE work_orders SET status=?,updated_at=? WHERE work_order_id=?", (status, self._now(), identifier))

    @staticmethod
    def _project(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = {key: row[key] for key in (
            "work_order_id", "title", "status", "active_revision", "created_at", "updated_at",
        )}
        identifier, revision = row["work_order_id"], row["active_revision"]
        plan = db.execute("SELECT plan_json FROM work_order_plans WHERE work_order_id=? AND revision=?",
                          (identifier, revision)).fetchone()
        result["plan"] = json.loads(plan["plan_json"])
        tasks = db.execute("""
            SELECT t.*,b.execution_revision FROM work_order_tasks t JOIN work_order_task_bindings b
            ON b.work_order_id=t.work_order_id AND b.plan_revision=t.revision AND b.task_id=t.task_id
            WHERE t.work_order_id=? AND t.revision=? ORDER BY position
        """,
                           (identifier, revision)).fetchall()
        edges = db.execute("""
            SELECT task_id,depends_on FROM work_order_dependencies WHERE work_order_id=? AND revision=? ORDER BY depends_on
        """, (identifier, revision)).fetchall()
        dependencies: dict[str, list[str]] = {}
        for edge in edges:
            dependencies.setdefault(edge["task_id"], []).append(edge["depends_on"])
        result["tasks"] = [{**dict(task), "dependencies": dependencies.get(task["task_id"], [])} for task in tasks]
        result["execution"] = json.loads(row["execution_json"])
        context_bindings = []
        for execution_revision in sorted({revision, *(task["execution_revision"] for task in tasks)}):
            binding = WorkOrderStore._context_binding(db, identifier, execution_revision)
            saved = binding["snapshot"]
            if saved is not None:
                context_bindings.append({"revision": execution_revision, "bound_revision": binding["bound_revision"], "descriptor": binding["descriptor"],
                    **{key: saved[key] for key in ("captured_at", "context_sha256", "context_bytes", "estimated_tokens", "estimate_method", "actual_tokens")}})
        result["context_bindings"] = context_bindings
        result["dispatch_error"] = row["dispatch_error"]
        attempts = db.execute("""
            SELECT a.*,r.status AS runtime_status,r.lease_active AS runtime_lease_active
            FROM work_order_attempts a LEFT JOIN runtime_runs r USING(run_id)
            WHERE a.work_order_id=? ORDER BY a.created_at,a.attempt_id
        """, (identifier,)).fetchall()
        result["attempts"] = [dict(attempt) for attempt in attempts]
        return result
