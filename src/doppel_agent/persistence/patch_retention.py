"""Bounded, owner-invoked private payload expiry; audit identities stay durable.

One keyset page of one phase per call, not a global history/storage cap. Never
instantiate a store, migrate a schema, recover a tool or touch workspace files.
Applying/unknown reviews, running effects and unregistered roots fail closed.
WAL/freelist pages and deliberately reviewed diffs are NOT securely erased.
"""

from __future__ import annotations

import json
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .inverse_reviews import InverseReviewStore, identifier
from .legacy_review import decode as decode_legacy
from .runs import TERMINAL_STATUSES
from .tool_ledger import ToolExecutionLedger


def _utc(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    if value.utcoffset() != timedelta(0) or value.isoformat() != raw:
        raise ValueError("private_retention_invalid_time")
    return value


def _regular(path: Path, root: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
            or path.resolve(strict=True).parent != root):
        raise ValueError("private_retention_database_path")


@dataclass(frozen=True)
class PrivatePatchRetention:
    private_days: int = 30
    max_rows: int = 100
    max_decode_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        for value, maximum in ((self.private_days, 36500), (self.max_rows, 100),
                               (self.max_decode_bytes, 4 * 1024 * 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid_private_retention_limit")

    def apply(self, state_root: Path, *, cursor: dict | None = None, dry_run: bool = False) -> dict:
        if type(dry_run) is not bool:
            raise ValueError("invalid_private_retention_dry_run")
        cursor = dict(cursor) if cursor is not None else {"phase": "reviews", "after": ""}
        if set(cursor) != {"phase", "after"} or cursor["phase"] not in {"reviews", "receipts", "legacy"}:
            raise ValueError("invalid_private_retention_cursor")
        phase, after = cursor["phase"], cursor["after"]
        if phase == "receipts":
            if after == "":
                after = ("", "")
            if not isinstance(after, (tuple, list)) or len(after) != 2:
                raise ValueError("invalid_private_retention_cursor")
            for item in after:
                if item != "":
                    identifier(item)
            after = tuple(after)
        elif after != "":
            identifier(after)
        report = {"phase": phase, "scanned": 0, "decode_bytes": 0,
                  "reviews_expired": 0, "preimages_expired": 0, "legacy_expired": 0,
                  "protected": 0, "deferred": False, "dry_run": dry_run,
                  "next_cursor": {"phase": phase, "after": after}}
        original_root = Path(state_root).absolute()
        root = original_root.resolve(strict=True)
        if (original_root != root or getattr(original_root.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("private_retention_database_path")
        ledger, runtime = root / "tool-executions.sqlite3", root / "runtime.sqlite3"
        if not ledger.exists() or not runtime.exists():
            report["deferred"] = True
            return report
        for path in (ledger, runtime):
            _regular(path, root)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    _regular(sidecar, root)
        mode = "ro" if dry_run else "rw"
        # No mkdir/PRAGMA journal-mode/schema writes, especially on dry run.
        db = sqlite3.connect(ledger.as_uri() + "?mode=" + mode, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("ATTACH DATABASE ? AS owned_runtime", (runtime.as_uri() + "?mode=" + mode,))
            # Acquire ledger before runtime admission, as effect/event writers do.
            # Only the ledger is changed; this is not cross-WAL atomicity proof.
            db.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
            deadline = time.monotonic() + 2
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"reviews": "inverse_patch_reviews", "receipts": "patch_effect_receipts",
                        "legacy": "legacy_review_continuations"}[phase]
            following = {"reviews": "receipts", "receipts": "legacy", "legacy": "reviews"}[phase]
            if required not in tables:
                report["next_cursor"] = {"phase": following, "after": ""}
                return report
            columns = {r[1] for r in db.execute(f"PRAGMA table_info({required})")}
            needed = {"reviews": set(), "receipts": {"summary_json", "preimage_expired_at"},
                      "legacy": {"snapshot_expired_at"}}[phase]
            if not needed <= columns:
                report["deferred"] = True
                report["next_cursor"] = {"phase": following, "after": ""}
                return report
            now = datetime.now(UTC)
            cutoff = now - timedelta(days=self.private_days)

            def root_record(run_id, *, old=False):
                identifier(run_id, uuid=True)
                row = db.execute("""SELECT CASE WHEN length(thread_id)<=128 THEN thread_id END AS thread_id,
                    status,lease_active,CASE WHEN length(updated_at)<=64 THEN updated_at END AS updated_at
                    FROM owned_runtime.runtime_runs WHERE run_id=?""", (run_id,)).fetchone()
                if (row is None or row["status"] not in TERMINAL_STATUSES
                        or type(row["lease_active"]) is not int or row["lease_active"] != 0
                        or not isinstance(row["thread_id"], str) or not 1 <= len(row["thread_id"]) <= 128):
                    return None
                updated = _utc(row["updated_at"])
                if old and updated >= cutoff:
                    return None
                if "tool_executions" in tables and db.execute(
                    "SELECT 1 FROM tool_executions WHERE run_id=? AND (status IS NULL OR status NOT IN ('completed','failed')) LIMIT 1",
                    (run_id,),
                ).fetchone():
                    return None
                return row

            def has_live_review(run_id):
                # Conservative whole-run protection includes BOTH source and
                # manual inverse receipts, even if a live review is malformed.
                return "inverse_patch_reviews" in tables and db.execute("""SELECT 1 FROM inverse_patch_reviews
                    WHERE source_run_id=? AND (status IS NULL OR status NOT IN ('applied','failed','rejected','expired')) LIMIT 1""",
                    (run_id,),
                ).fetchone() is not None

            if phase == "reviews":
                rows = db.execute("""SELECT review_id,source_run_id,
                    length(CAST(proposal_json AS BLOB))+length(CAST(review_json AS BLOB))+
                    length(CAST(result_json AS BLOB)) AS bytes FROM inverse_patch_reviews
                    WHERE review_id>? AND status='pending' AND length(review_id)<=256 AND length(source_run_id)<=256
                    ORDER BY review_id LIMIT ?""", (after, self.max_rows)).fetchall()
            elif phase == "receipts":
                rows = db.execute("""SELECT p.run_id,p.tool_call_id,p.patch_id,
                    CASE WHEN length(p.created_at)<=64 THEN p.created_at END AS created_at,t.status,t.tool_name,
                    length(CAST(p.receipt_json AS BLOB)) AS bytes FROM patch_effect_receipts p
                    JOIN tool_executions t USING(run_id,tool_call_id)
                    WHERE (p.run_id,p.tool_call_id)>(?,?) AND p.preimage_expired_at IS NULL
                    AND length(p.run_id)<=256 AND length(p.tool_call_id)<=256 AND length(p.patch_id)<=256
                    ORDER BY p.run_id,p.tool_call_id LIMIT ?""", (*after, self.max_rows)).fetchall()
            else:
                rows = db.execute("""SELECT run_id,thread_id,interrupt_id,status,
                    length(CAST(snapshot_json AS BLOB))+length(CAST(decision_json AS BLOB)) AS bytes
                    FROM legacy_review_continuations WHERE run_id>? AND snapshot_expired_at IS NULL
                    AND length(run_id)<=256 AND length(thread_id)<=256 AND length(interrupt_id)<=256
                    ORDER BY run_id LIMIT ?""", (after, self.max_rows)).fetchall()
            stopped = False
            for item in rows:
                key = ((item["run_id"], item["tool_call_id"]) if phase == "receipts" else
                       item["review_id"] if phase == "reviews" else item["run_id"])
                size = item["bytes"]
                if type(size) is int and 0 <= size <= self.max_decode_bytes:
                    if report["decode_bytes"] + size > self.max_decode_bytes:
                        stopped = True
                        break  # Retry this key on a fresh decode budget.
                report["scanned"] += 1
                report["next_cursor"] = {"phase": phase, "after": key}
                try:
                    if type(size) is not int or not 0 < size <= self.max_decode_bytes:
                        raise ValueError("private_retention_payload_budget")
                    run_id = item["source_run_id"] if phase == "reviews" else item["run_id"]
                    source = root_record(run_id, old=phase != "reviews")
                    if source is None or phase != "reviews" and has_live_review(run_id):
                        raise ValueError("private_retention_root_protected")
                    report["decode_bytes"] += size
                    if phase == "reviews":
                        row = InverseReviewStore._row(db, run_id, key)
                        InverseReviewStore._public(row)
                        if _utc(row["expires_at"]) > now:
                            raise ValueError("private_retention_review_live")
                        InverseReviewStore._pending_proposal(row)
                        if not dry_run:
                            db.execute("UPDATE inverse_patch_reviews SET status='expired',proposal_json='' WHERE review_id=? AND status='pending'", (key,))
                        report["reviews_expired"] += 1
                    elif phase == "receipts":
                        identifier(item["tool_call_id"])
                        identifier(item["patch_id"], uuid=True)
                        if (item["tool_name"] not in {"propose_patch", "inverse_patch"}
                                or item["status"] not in {"completed", "failed"} or _utc(item["created_at"]) >= cutoff):
                            raise ValueError("private_retention_effect_protected")
                        if item["tool_name"] == "inverse_patch":
                            # Unknown/manual orphan provenance never authorizes expiry.
                            if not item["tool_call_id"].startswith("manual-inverse:"):
                                raise ValueError("private_retention_effect_protected")
                            review_id = item["tool_call_id"].removeprefix("manual-inverse:")
                            identifier(review_id, uuid=True)
                            manual = db.execute("SELECT status,patch_id FROM inverse_patch_reviews WHERE review_id=? AND source_run_id=?",
                                                (review_id, run_id)).fetchone() if "inverse_patch_reviews" in tables else None
                            if manual is None or manual["status"] != "applied" or manual["patch_id"] != item["patch_id"]:
                                raise ValueError("private_retention_effect_protected")
                            extra = db.execute("""SELECT length(CAST(proposal_json AS BLOB))+
                                length(CAST(review_json AS BLOB))+length(CAST(result_json AS BLOB))
                                FROM inverse_patch_reviews WHERE review_id=?""", (review_id,)).fetchone()[0]
                            if type(extra) is not int or extra < 0 or report["decode_bytes"] + extra > self.max_decode_bytes:
                                raise ValueError("private_retention_payload_budget")
                            report["decode_bytes"] += extra
                            InverseReviewStore._public(InverseReviewStore._row(db, run_id, review_id))
                        raw = db.execute("SELECT receipt_json FROM patch_effect_receipts WHERE run_id=? AND tool_call_id=?", key).fetchone()[0]
                        receipt = ToolExecutionLedger._decode_patch_receipt(raw)
                        if receipt.patch_id != item["patch_id"] or receipt.status != ("applied" if item["status"] == "completed" else "failed"):
                            raise ValueError("private_retention_effect_protected")
                        summary = json.dumps(receipt.as_dict(include_preimage=False), ensure_ascii=False, separators=(",", ":"))
                        if not dry_run:
                            db.execute("""UPDATE patch_effect_receipts SET receipt_json='',summary_json=?,preimage_expired_at=?
                                WHERE run_id=? AND tool_call_id=? AND preimage_expired_at IS NULL""", (summary, now.isoformat(), *key))
                        report["preimages_expired"] += 1
                    else:
                        identifier(item["interrupt_id"], uuid=True)
                        if item["thread_id"] != source["thread_id"] or item["status"] not in {"pending", "consumed"}:
                            raise ValueError("private_retention_continuation_protected")
                        private = db.execute("SELECT snapshot_json,decision_json FROM legacy_review_continuations WHERE run_id=?", (key,)).fetchone()
                        payload, _ = decode_legacy(private["snapshot_json"])
                        if payload["run_id"] != run_id or payload["thread_id"] != source["thread_id"]:
                            raise ValueError("private_retention_continuation_protected")
                        if (item["status"] == "pending" and private["decision_json"] != ""
                                or item["status"] == "consumed" and not isinstance(json.loads(private["decision_json"]), dict)):
                            raise ValueError("private_retention_continuation_protected")
                        if not dry_run:
                            db.execute("""UPDATE legacy_review_continuations SET snapshot_json='',decision_json='',snapshot_expired_at=?
                                WHERE run_id=? AND snapshot_expired_at IS NULL""", (now.isoformat(), key))
                        report["legacy_expired"] += 1
                except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                    report["protected"] += 1
            if not stopped and len(rows) < self.max_rows:
                report["next_cursor"] = {"phase": following, "after": ""}
            if not dry_run:
                db.commit()
            return report
        finally:
            db.set_progress_handler(None, 0)
            db.close()  # Rollback dry runs and incomplete/failed batches.
