"""Bounded read of ORIGINAL Legacy events, no session/status recovery or IO grant.

Operation closure isn't provider/transport/usage/billing or physical SDK drain.
Historical unmarked probe/provider coverage remains unknown, never claimed zero.
Caller must hold original workspace owner and join reader before execution pumps.
No directory/store creation, SQL registration, mutation, retry or reset.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from ..storage import LEGACY_OPERATION_START, LEGACY_OPERATION_FINISH
from .provider_recovery import ProviderReceiptRecoveryQueries, _object, _constant, _float
from .verification_queries import QUERY_SECONDS, _check, _safe_database


MAX_DIRECTORIES = 10000
MAX_LINES = 100000
MAX_LINE = 256 * 1024
MAX_DECODE = 32 * 1024 * 1024


@dataclass(frozen=True)
class LegacyOperationRecovery:
    quarantined: bool
    reason: str
    operation_rows: int = 0


class _EvidenceLimit(ValueError):
    pass


def _directory(path: Path, deadline: float) -> Path:
    original = path.absolute()
    _check(deadline)
    if len(original.parents) > 64 or original.resolve(strict=True) != original:
        raise ValueError("legacy_source_unavailable")
    for candidate in (original, *original.parents):
        _check(deadline)
        info = candidate.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or candidate.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("legacy_source_unavailable")
    return original


def _identity(value):
    return type(value) is str and len(value) == 32 and all(ch in "0123456789abcdef" for ch in value)


def _fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


class LegacyOperationRecoveryQueries:
    def __init__(self, state_root: Path, *, runtime_database: Path | None = None):
        self.state_root = Path(state_root)  # Path only, no constructor IO.
        self.runtime_database = runtime_database

    def _recorded_runtime_legacy(self, deadline):
        if self.runtime_database is None:
            return frozenset()
        # Reviewed Legacy Core can deliberately pause at an approval, with no
        # raw Core terminal event yet. Do not quarantine that exact registered
        # SQL source merely for being resumable. Require actual canonical
        # receipts and SAME full closed-source reader, never status alone.
        if ProviderReceiptRecoveryQueries(self.runtime_database).read().quarantined:
            raise ValueError("original_runtime_source_unknown")
        path = _safe_database(self.runtime_database, deadline)
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
            connection.execute("BEGIN")
            rows = connection.execute(
                """SELECT DISTINCT r.run_id FROM runtime_runs r
                JOIN runtime_events e ON e.run_id=r.run_id AND e.thread_id=r.thread_id
                WHERE r.mode='legacy' AND e.type='provider.call_started'
                ORDER BY r.run_id LIMIT ?""",
                (MAX_DIRECTORIES + 1,),
            ).fetchall()
            _check(deadline)
            if len(rows) > MAX_DIRECTORIES:
                raise _EvidenceLimit()
            if any(not _identity(row[0]) for row in rows):
                raise ValueError("original_runtime_scope_unknown")
            return frozenset(row[0] for row in rows)
        finally:
            connection.close()

    def read(self) -> LegacyOperationRecovery:
        deadline = monotonic() + QUERY_SECONDS
        rows = 0
        try:
            root = _directory(self.state_root, deadline)
            runs = root / "runs"
            try:
                runs.lstat()
            except FileNotFoundError:
                return LegacyOperationRecovery(False, "no_unresolved_recorded_operations_found")
            runs = _directory(runs, deadline)
            recorded_runtime_legacy = self._recorded_runtime_legacy(deadline)
            directories = lines = decoded = 0
            unresolved = False
            with os.scandir(runs) as entries:
                for entry in entries:
                    _check(deadline)
                    directories += 1
                    if directories > MAX_DIRECTORIES:
                        raise _EvidenceLimit()
                    if not _identity(entry.name):
                        raise ValueError("legacy_source_unavailable")
                    directory = _directory(Path(entry.path), deadline)
                    # Application-level existing-file/reparse/hardlink guard,
                    # not an atomic sandbox against a hostile path replacer.
                    path = _safe_database(directory / "events.jsonl", deadline)
                    before = path.lstat()
                    starts = finishes = 0
                    operation = None
                    core_open = False
                    file_lines = 0
                    with path.open("rb") as stream:
                        if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(before):
                            raise ValueError("legacy_source_changed")
                        while True:
                            _check(deadline)
                            raw = stream.readline(MAX_LINE + 1)
                            if not raw:
                                break
                            lines += 1
                            file_lines += 1
                            decoded += len(raw)
                            if lines > MAX_LINES or len(raw) > MAX_LINE or decoded > MAX_DECODE:
                                raise _EvidenceLimit()
                            if not raw.endswith(b"\n"):
                                raise ValueError("legacy_partial_source")
                            frame = json.loads(
                                raw, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float
                            )
                            if type(frame) is not dict or frame.get("run_id") != entry.name:
                                raise ValueError("legacy_source_unavailable")
                            kind = frame.get("kind")
                            if type(kind) is not str or type(frame.get("payload")) is not dict:
                                raise ValueError("legacy_source_unavailable")
                            if kind == "run_started":
                                if core_open:
                                    unresolved = True
                                core_open = True
                            elif kind in {"run_completed", "run_failed"}:
                                if not core_open:
                                    unresolved = True
                                core_open = False
                            if kind not in {LEGACY_OPERATION_START, LEGACY_OPERATION_FINISH}:
                                continue  # No private contents retained or returned.
                            rows += 1
                            payload = frame["payload"]
                            expected = {"version", "operation_id", "operation"}
                            if kind == LEGACY_OPERATION_FINISH:
                                expected.add("outcome")
                            if (
                                set(frame) != {"run_id", "sequence", "timestamp", "kind", "payload"}
                                or type(frame["sequence"]) is not int
                                or frame["sequence"] != 0
                                or type(frame["timestamp"]) is not str
                                or len(frame["timestamp"]) > 64
                                or set(payload) != expected
                                or type(payload.get("version")) is not int
                                or payload["version"] != 1
                                or payload.get("operation_id") != entry.name
                                or payload.get("operation") not in {"run", "probe"}
                            ):
                                raise ValueError("legacy_operation_invalid")
                            if kind == LEGACY_OPERATION_START:
                                starts += 1
                                if starts != 1 or finishes:
                                    unresolved = True
                                operation = payload["operation"]
                            else:
                                finishes += 1
                                if (
                                    starts != 1
                                    or finishes != 1
                                    or operation != payload["operation"]
                                    or payload.get("outcome") != "returned"
                                ):
                                    unresolved = True
                        if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(before):
                            raise ValueError("legacy_source_changed")
                    _safe_database(path, deadline)
                    if _fingerprint(path.lstat()) != _fingerprint(before):
                        raise ValueError("legacy_source_changed")
                    if (
                        file_lines == 0
                        or starts != finishes
                        or core_open
                        and entry.name not in recorded_runtime_legacy
                    ):
                        unresolved = True
            return LegacyOperationRecovery(
                unresolved,
                "unresolved_operations" if unresolved else "no_unresolved_recorded_operations_found",
                rows,
            )
        except _EvidenceLimit:
            return LegacyOperationRecovery(True, "evidence_limit", rows)
        except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError, OverflowError):
            return LegacyOperationRecovery(True, "evidence_unavailable", rows)
