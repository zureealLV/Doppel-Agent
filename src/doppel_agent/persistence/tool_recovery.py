"""Existing-only original tool ledger closure check before startup pumps.

No ledger constructor/migration, result/preimage decode, effect repair, retry or
completion invention. A sealed known failure is not an unknown effect; sealed
rows still do not prove physical drain, provider billing or project acceptance.
Caller holds the same workspace owner and joins this reader before admission.
"""

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from time import monotonic

from .verification_queries import QUERY_SECONDS, _check, _safe_database
from ..reporting.read_lifetime import ReportReadCleanupError, ReportReadSource


@dataclass(frozen=True)
class ToolOperationRecovery:
    quarantined: bool
    reason: str


class ToolOperationRecoveryQueries:
    def __init__(self, database, runtime_database, *, failure=None, cleanup_failure=None):
        self.database, self.runtime_database = Path(database), Path(runtime_database)
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._unresolved_reads = {}

    def _retain(self, source):
        self._unresolved_reads[id(source)] = source
        if self._cleanup_failure is not None:
            self._cleanup_failure(source)

    def read(self) -> ToolOperationRecovery:
        try:
            if self._unresolved_reads:
                return ToolOperationRecovery(True, 'evidence_unavailable')
            # Legitimately absent before the first original tool admission.
            # lstat distinguishes absence from a broken symlink/reparse source;
            # existing sources pass the shared fixed-path/sidecar guards below.
            try:
                self.database.lstat()
            except FileNotFoundError:
                return ToolOperationRecovery(False, 'no_recorded_tool_source')
            deadline = monotonic() + QUERY_SECONDS
            database = _safe_database(self.database, deadline)
            runtime = _safe_database(self.runtime_database, deadline)
            if database.parent != runtime.parent or database == runtime:
                raise ValueError('tool_source_unavailable')
            # Reuse existing original SQL lifetime retention. A scalar read
            # result never replaces individual cursor/connection close receipts.
            source = ReportReadSource(failure=self._failure, cleanup_failure=self._retain)
            try:
                db = source.open_original(sqlite3.connect, database.as_uri() + '?mode=ro')
                db.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                db.execute('BEGIN')
                # Never load private result/error/arguments/patch preimages.
                # Progress-handler deadline bounds scans; output is one scalar,
                # not an arbitrarily selected report page treated as completeness.
                malformed = db.execute("""SELECT 1 FROM tool_executions
                    WHERE typeof(status)!='text' OR status NOT IN ('running','completed','failed')
                    LIMIT 1""").fetchone()
                _check(deadline)
                if malformed is not None:
                    return ToolOperationRecovery(True, 'evidence_unavailable')
                unresolved = db.execute("SELECT 1 FROM tool_executions WHERE status='running' LIMIT 1").fetchone()
                _check(deadline)
                if unresolved is not None:
                    return ToolOperationRecovery(True, 'unsealed_tool_operations')
            finally:
                source.close_original()  # Same worker, each original once; unknown retains owner.
            return ToolOperationRecovery(False, 'no_unsealed_tool_operations_found')
        except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError, OverflowError,
                ReportReadCleanupError):
            return ToolOperationRecovery(True, 'evidence_unavailable')
