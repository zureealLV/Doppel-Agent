"""Bound direct DAG dependencies to actual successful drained run results.

Read under reservation's transaction. Text is data, not a completion oracle.
Full result hashes are streamed with explicit raw/included/aggregate bounds.
"""

from __future__ import annotations

import hashlib

from .input import input_snapshot
from .manifest import ContextManifestError


MAX_RAW_RESULT = 1024 * 1024
MAX_RAW_TOTAL = 4 * 1024 * 1024
MAX_RESULT_TEXT = 8 * 1024
MAX_INCLUDED_TOTAL = 32 * 1024
READ_CHUNK = 64 * 1024


def bind_predecessors(db, identifier: str, plan_revision: int, task: dict, base: dict | None) -> dict | None:
    edges = db.execute(
        """SELECT depends_on FROM work_order_dependencies
        WHERE work_order_id=? AND revision=? AND task_id=? ORDER BY depends_on""",
        (identifier, task["execution_revision"], task["task_id"]),
    ).fetchall()
    if not edges:
        return base
    entries, prefixes, raw_total = [], [], 0
    for edge in edges:
        row = db.execute(
            """
            SELECT a.attempt_id,a.revision,a.run_id,a.status,r.status AS runtime_status,r.lease_active,r.finished_at,
                   length(CAST(r.answer AS BLOB)) AS result_bytes
            FROM work_order_task_bindings b JOIN work_order_attempts a
            ON a.work_order_id=b.work_order_id AND a.revision=b.execution_revision AND a.task_id=b.task_id
            JOIN runtime_runs r USING(run_id)
            WHERE b.work_order_id=? AND b.plan_revision=? AND b.task_id=?
            ORDER BY a.attempt_number DESC LIMIT 1
        """,
            (identifier, plan_revision, edge["depends_on"]),
        ).fetchone()
        if (
            row is None
            or row["status"] != "succeeded"
            or row["runtime_status"] != "completed"
            or row["lease_active"]
        ):
            raise ContextManifestError("predecessor_result_not_drained_success")
        size = row["result_bytes"]
        raw_total += size
        if size > MAX_RAW_RESULT or raw_total > MAX_RAW_TOTAL:
            raise ContextManifestError("predecessor_result_too_large")
        digest, prefix = hashlib.sha256(), b""
        for offset in range(0, size, READ_CHUNK):
            chunk = db.execute(
                "SELECT substr(CAST(answer AS BLOB),?,?) FROM runtime_runs WHERE run_id=?",
                (offset + 1, min(READ_CHUNK, size - offset), row["run_id"]),
            ).fetchone()[0]
            if len(chunk) != min(READ_CHUNK, size - offset):
                raise ContextManifestError("predecessor_result_changed")
            digest.update(chunk)
            if len(prefix) < MAX_RESULT_TEXT:
                prefix += chunk[: MAX_RESULT_TEXT - len(prefix)]
        prefixes.append(prefix.decode("utf-8", errors="ignore").encode("utf-8"))
        entries.append(
            {
                "work_order_id": identifier,
                "task_id": edge["depends_on"],
                "execution_revision": row["revision"],
                "attempt_id": row["attempt_id"],
                "run_id": row["run_id"],
                "status": "completed",
                "lease_drained": True,
                "result_finished_at": row["finished_at"],
                "result_bytes": size,
                "result_sha256": digest.hexdigest(),
                "text": "",
                "included_bytes": 0,
                "included_sha256": hashlib.sha256(b"").hexdigest(),
                # Reserve the largest possible metadata framing for EVERY pending
                # entry before allocating text. A previous entry must not consume
                # bytes needed to label a later entry's zero-byte truncation.
                "truncation_reasons": [
                    "per_predecessor_limit",
                    "aggregate_predecessor_limit",
                    "input_budget",
                ],
            }
        )

    def snapshot():
        return input_snapshot(
            (base or {}).get("manifest"), (base or {}).get("notes", []), identifier, predecessors=entries
        )

    snapshot()  # Identities alone must fit. Never silently omit a dependency.
    used = 0
    for entry, prefix in zip(entries, prefixes, strict=True):
        desired = min(len(prefix), MAX_INCLUDED_TOTAL - used)

        def include(count: int, *, prefix=prefix, entry=entry, desired=desired):
            raw = prefix[:count].decode("utf-8", errors="ignore").encode("utf-8")
            entry["text"] = raw.decode("utf-8")
            entry["included_bytes"] = len(raw)
            entry["included_sha256"] = hashlib.sha256(raw).hexdigest()
            reasons = []
            if entry["result_bytes"] > len(prefix):
                reasons.append("per_predecessor_limit")
            if desired < len(prefix):
                reasons.append("aggregate_predecessor_limit")
            if len(raw) < desired:
                reasons.append("input_budget")
            entry["truncation_reasons"] = reasons
            return snapshot()

        try:
            include(desired)
        except ContextManifestError as error:
            if str(error) != "context_input_budget_exceeded":
                raise
            low, high = 0, desired - 1
            while low < high:
                middle = (low + high + 1) // 2
                try:
                    include(middle)
                    low = middle
                except ContextManifestError as error:
                    if str(error) != "context_input_budget_exceeded":
                        raise
                    high = middle - 1
            include(low)
        used += entry["included_bytes"]
    return snapshot()
