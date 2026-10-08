"""Bound quiescent service-owned checkpoints, never side-effect/audit ledgers.

Latest snapshots contain complete state; arbitrary historical time travel is not
a Local Mode promise. Pending threads and unregistered factory threads are left
untouched. Invoke only while holding the Local Mode workspace owner.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .database import sqlite_connection
from .runs import TERMINAL_STATUSES


@dataclass(frozen=True)
class CheckpointRetention:
    keep_per_namespace: int = 32
    obsolete_days: int = 30

    def __post_init__(self):
        for value in (self.keep_per_namespace, self.obsolete_days):
            if type(value) is not int or value < 1:
                raise ValueError("checkpoint retention limits must be positive integers")

    def apply(self, state_root: Path, *, dry_run: bool = False) -> dict[str, int]:
        root = state_root.resolve(strict=True)
        result = {"checkpoints_deleted": 0, "writes_deleted": 0, "threads_deleted": 0}
        cutoff = datetime.now(UTC) - timedelta(days=self.obsolete_days)
        with sqlite_connection(root / "runtime.sqlite3") as db:
            with ExitStack() as stack:
                checkpoints = []
                # Acquire checkpoint writers BEFORE the runtime admission lock.
                # A graph may emit durable events while its saver is busy; never
                # hold runtime.sqlite3 while waiting on that saver. Connections
                # commit inside this stack, before the outer runtime lock exits.
                for filename in ("checkpoints.sqlite3", "deep-checkpoints.sqlite3", "focused-fallback.sqlite3"):
                    path = root / filename
                    if not path.exists():
                        continue
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        raise ValueError("checkpoint database must remain within the state root")
                    cp = stack.enter_context(sqlite_connection(path))
                    tables = {r[0] for r in cp.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"checkpoints", "writes"} <= tables:
                        continue
                    cp.execute("BEGIN IMMEDIATE")
                    checkpoints.append(cp)
                # Selection/admission stays serialized until all checkpoint
                # commits above have drained. No stale post-selection deletion.
                db.execute("BEGIN IMMEDIATE")
                records: dict[str, list] = {}
                for row in db.execute("SELECT thread_id,status,lease_active,updated_at FROM runtime_runs"):
                    records.setdefault(row["thread_id"], []).append(row)
                current = {r[0] for r in db.execute("SELECT thread_id FROM native_conversations WHERE deleted=0")}
                children = {}
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='async_subagents'").fetchone():
                    children = {r["subagent_id"]: r for r in db.execute("SELECT * FROM async_subagents")}
                eligible: dict[str, bool] = {}
                for thread, rows in records.items():
                    if any(r["status"] not in TERMINAL_STATUSES or r["lease_active"] for r in rows):
                        continue
                    try:
                        last = max(datetime.fromisoformat(r["updated_at"]) for r in rows)
                        obsolete = last < cutoff and thread not in current
                    except (ValueError, TypeError):
                        continue  # Malformed provenance is never authority to delete.
                    eligible[thread] = obsolete
                for thread, child in children.items():
                    if child["status"] in {"completed", "failed", "cancelled"}:
                        eligible.setdefault(thread, False)
                for cp in checkpoints:
                    for thread, obsolete in eligible.items():
                        remove = list(cp.execute(
                            "SELECT checkpoint_ns,checkpoint_id FROM (SELECT checkpoint_ns,checkpoint_id,"
                            "ROW_NUMBER() OVER (PARTITION BY checkpoint_ns ORDER BY checkpoint_id DESC) AS rank "
                            "FROM checkpoints WHERE thread_id=?) WHERE rank>?",
                            (thread, 0 if obsolete else self.keep_per_namespace),
                        ))
                        if obsolete and remove:
                            result["threads_deleted"] += 1
                        result["checkpoints_deleted"] += len(remove)
                        retained = {(r[0], r[1]) for r in cp.execute(
                            "SELECT checkpoint_ns,checkpoint_id FROM checkpoints WHERE thread_id=?", (thread,)
                        )} - {(r[0], r[1]) for r in remove}
                        doomed_writes = [(r[0], r[1], r[2], r[3]) for r in cp.execute(
                            "SELECT checkpoint_ns,checkpoint_id,task_id,idx FROM writes WHERE thread_id=?", (thread,)
                        ) if (r[0], r[1]) not in retained]
                        result["writes_deleted"] += len(doomed_writes)
                        if not dry_run:
                            cp.executemany("DELETE FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? AND task_id=? AND idx=?",
                                           [(thread, *row) for row in doomed_writes])
                            cp.executemany("DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                                           [(thread, r[0], r[1]) for r in remove])
        # WAL/freelist pages are reused, not secure erasure or an automatic VACUUM.
        return result
