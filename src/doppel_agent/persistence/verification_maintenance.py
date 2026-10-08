"""One owner-invoked keyset page of existing verification metadata.

No store construction/schema migration, command recovery or project file reads.
Startup may reconcile running intents only before manual admission exists.
Ordinary maintenance protects running/indeterminate rows; unknown never means absence.
Not global history/output retention, secure erase, or a hostile-actor sandbox.
"""

from __future__ import annotations

import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from .inverse_reviews import identifier
from .runs import TERMINAL_STATUSES
from .verification_reviews import VerificationReviewStore


@dataclass(frozen=True)
class VerificationMaintenance:
    max_rows: int = 64
    max_decode_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        if (type(self.max_rows) is not int or not 1 <= self.max_rows <= 64
                or type(self.max_decode_bytes) is not int or not 5 * 1024 * 1024 <= self.max_decode_bytes <= 8 * 1024 * 1024):
            raise ValueError("verification_maintenance_limits_invalid")

    def apply(self, database: Path, *, after_id: str = "", recover_running: bool = False,
              runtime_database: Path | None = None):
        if after_id != "":
            identifier(after_id, uuid=True)
        if type(recover_running) is not bool:
            raise ValueError("verification_maintenance_recovery_invalid")
        report = {"scanned": 0, "decode_bytes": 0, "expired": 0, "completed": 0,
                  "indeterminate": 0, "protected": 0, "deferred": False, "next_after_id": after_id}
        original = Path(database).absolute()
        if not original.exists() and not original.is_symlink():
            report["deferred"] = True
            return report
        root = original.parent.resolve(strict=True)
        if original.parent != root or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("verification_maintenance_database_path")
        databases = [original]
        if runtime_database is not None:
            runtime_database = Path(runtime_database).absolute()
            if runtime_database.parent != root or runtime_database == original:
                raise ValueError("verification_maintenance_database_path")
            if not runtime_database.exists() and not runtime_database.is_symlink():
                report["deferred"] = True
                return report
            databases.append(runtime_database)
        paths = [path for base in databases for path in
                 (base, *(base.with_name(base.name + suffix) for suffix in ("-wal", "-shm", "-journal")))]
        for path in paths:
            if path not in databases and not path.exists() and not path.is_symlink():
                continue
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink()
                    or getattr(info, "st_file_attributes", 0) & 0x400
                    or path.resolve(strict=True).parent != root):
                raise ValueError("verification_maintenance_database_path")
        db = sqlite3.connect(original.as_uri() + "?mode=rw", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if runtime_database is not None:
                db.execute("ATTACH DATABASE ? AS registered_runtime", (runtime_database.as_uri() + "?mode=ro",))
            with db:
                db.execute("BEGIN IMMEDIATE")
                deadline = monotonic() + 2
                db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"verification_reviews", "verification_command_steps"} <= tables:
                    report["deferred"] = True
                    return report
                candidates = db.execute("""SELECT source_run_id,review_id FROM verification_reviews
                    WHERE review_id>? AND status IN ('pending','running','indeterminate')
                    ORDER BY review_id LIMIT ?""", (after_id, self.max_rows + 1)).fetchall()
                for candidate in candidates[:self.max_rows]:
                    if monotonic() >= deadline:
                        report["deferred"] = True
                        break
                    run, review = candidate["source_run_id"], candidate["review_id"]
                    if runtime_database is not None:
                        source = db.execute("SELECT status,lease_active FROM registered_runtime.runtime_runs WHERE run_id=?", (run,)).fetchone()
                        if source is None or source["status"] not in TERMINAL_STATUSES or source["lease_active"] != 0:
                            report["protected"] += 1
                            report["scanned"] += 1
                            report["next_after_id"] = review
                            continue
                    size = db.execute("""SELECT length(CAST(plan_json AS BLOB))+length(CAST(selection_json AS BLOB))+
                        COALESCE((SELECT SUM(length(CAST(result_json AS BLOB))) FROM verification_command_steps
                                  WHERE review_id=?),0) FROM verification_reviews WHERE review_id=?""",
                                      (review, review)).fetchone()[0]
                    if type(size) is not int or size < 0 or size > self.max_decode_bytes:
                        raise ValueError("verification_evidence_budget")
                    if report["decode_bytes"] + size > self.max_decode_bytes:
                        report["deferred"] = True
                        break
                    row = VerificationReviewStore._row(db, run, review)
                    VerificationReviewStore._expire(db, row)
                    row = VerificationReviewStore._row(db, run, review)
                    if row["status"] in {"running", "indeterminate"} and not recover_running:
                        report["protected"] += 1
                    else:
                        view = VerificationReviewStore._reconcile_row(db, row)
                        if view["status"] in {"expired", "completed", "indeterminate"}:
                            report[view["status"]] += 1
                    report["scanned"] += 1
                    report["decode_bytes"] += size
                    report["next_after_id"] = review
                if not report["deferred"] and len(candidates) <= self.max_rows:
                    report["next_after_id"] = ""
                return report
        finally:
            db.close()
