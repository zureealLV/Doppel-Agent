"""Append-only local records. A future milestone will add crash-safe indexing."""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

LEGACY_OPERATION_START = 'legacy.operation_started'
LEGACY_OPERATION_FINISH = 'legacy.operation_finished'
MAX_READ_BYTES = 32 * 1024 * 1024
MAX_EVENT_ROWS = 100_000
MAX_HISTORY_ENTRIES = 10_000


class LegacyStorageReadError(RuntimeError):
    def __init__(self, source: RunStore | None = None):
        super().__init__('legacy_storage_evidence_unavailable')
        self.source = source  # Private original source; never HTTP/report/provider payload.


@dataclass
class _StorageSourceLifetime:
    kind: str
    resource: Any = None
    owner_thread: int = field(default_factory=threading.get_ident)
    open_attempted: bool = False
    enter_returned: bool = False
    close_attempted: bool = False
    close_returned: bool = False


class RunStore:
    def __init__(self, root: Path, *, failure: Callable[[], None] | None = None,
                 cleanup_failure: Callable[[RunStore], None] | None = None):
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._failed = threading.Event()
        self._cleanup_uncertain = threading.Event()
        self._resource_cleanup_uncertain = threading.Event()
        self._lifetime_lock = threading.Lock()
        self._unresolved_sources: dict[int, _StorageSourceLifetime] = {}
        try:
            self.root = root.resolve()
        except OSError:
            self._unavailable()  # Path metadata, no invented allocated file/iterator.

    @property
    def cleanup_uncertain(self) -> bool:
        # Inherited conservative IO gate is NOT physical resource proof.
        return self._cleanup_uncertain.is_set()

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    @property
    def resource_cleanup_uncertain(self) -> bool:
        return self._resource_cleanup_uncertain.is_set()

    def _mark_failed(self, *, cleanup: bool = False) -> None:
        if cleanup:
            self._cleanup_uncertain.set()
        if self.failed:
            return
        self._failed.set()
        if self._failure is not None:
            try:
                self._failure()  # SAME original owner fault BEFORE original resource close.
            except BaseException:
                self._cleanup_uncertain.set()

    def _unavailable(self, *, cleanup: bool = False) -> NoReturn:
        self._mark_failed(cleanup=cleanup)
        raise LegacyStorageReadError(self) from None

    def _check_admission(self) -> None:
        if self.failed:
            raise LegacyStorageReadError(self)  # No failed-source read/write/retry/reset.

    def _retain_uncertain(self, frame: _StorageSourceLifetime) -> None:
        self._resource_cleanup_uncertain.set()
        with self._lifetime_lock:
            self._unresolved_sources[id(frame)] = frame
        self._mark_failed(cleanup=True)
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # Exact original source/handle/attempt, no IO.
            except BaseException:
                pass

    def check_resource_cleanup(self) -> None:
        if self.resource_cleanup_uncertain:
            raise LegacyStorageReadError(self)

    def _close_original(self, frame: _StorageSourceLifetime, details=(None, None, None)) -> None:
        if frame.close_attempted:
            if frame.close_returned:
                return
            raise LegacyStorageReadError(self)  # Never retry SAME original unknown exit.
        if frame.resource is None:
            self._retain_uncertain(frame)
            raise LegacyStorageReadError(self)  # No known original handle; no invented close attempt.
        frame.close_attempted = True
        try:
            if frame.enter_returned:
                frame.resource.__exit__(*details)
            else:
                frame.resource.close()  # SAME allocated resource whose enter failed.
            frame.close_returned = True  # Known API return, NOT physical/native proof.
        except BaseException:
            self._retain_uncertain(frame)
            raise LegacyStorageReadError(self) from None

    @contextmanager
    def _original_resource(self, factory: Callable[[], Any], *, kind: str,
                           missing_ok: bool = False, exclusive: bool = False):
        self._check_admission()
        frame = _StorageSourceLifetime(kind=kind)
        frame.open_attempted = True  # BEFORE SAME original Path.open/os.scandir.
        try:
            resource = factory()
        except FileNotFoundError:
            if missing_ok:
                yield None  # Only original OPEN absence, never entered read/iteration/exit.
                return
            self._unavailable()  # Required absent evidence, no allocated handle implied.
        except FileExistsError:
            if exclusive:
                self._unavailable(cleanup=True)  # Known original temp refusal, never truncate it.
            self._retain_uncertain(frame)
            raise LegacyStorageReadError(self) from None
        except BaseException:
            self._retain_uncertain(frame)  # Opaque allocation may not have returned its resource.
            raise LegacyStorageReadError(self) from None
        frame.resource = resource
        try:
            original = resource.__enter__()
            frame.enter_returned = True
            if original is None:
                raise ValueError('original_enter_not_acknowledged')  # Never manufacture missing defaults.
        except BaseException:
            self._mark_failed(cleanup=True)
            self._close_original(frame)
            self._unavailable(cleanup=True)
        try:
            yield original
        except BaseException as exc:
            # Known metadata validation inside iterator keeps inherited metadata
            # gate; actual IO/body failure sets conservative cleanup quarantine.
            cleanup = not isinstance(exc, LegacyStorageReadError)
            self._mark_failed(cleanup=cleanup)
            self._close_original(frame, (type(exc), exc, exc.__traceback__))
            self._unavailable(cleanup=cleanup)  # Suppressing exit cannot fake publication.
        else:
            self._close_original(frame)

    @staticmethod
    def _unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate original field')
            result[key] = value
        return result

    @staticmethod
    def _reject_constant(_constant) -> NoReturn:
        raise ValueError('nonfinite original value')

    def _decode(self, raw: str):
        try:
            return json.loads(raw, object_pairs_hook=self._unique_object, parse_constant=self._reject_constant)
        except (ValueError, RecursionError):
            self._unavailable()

    def _read_text(self, path: Path, *, required: bool = False) -> str | None:
        with self._original_resource(lambda: path.open('rb'), kind='file', missing_ok=not required) as stream:
            if stream is None:
                return None
            raw = stream.read(MAX_READ_BYTES + 1)
            if type(raw) is not bytes:
                raise ValueError('original_read_not_acknowledged')
        if len(raw) > MAX_READ_BYTES:
            self._unavailable()
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            self._unavailable()

    @staticmethod
    def _valid_id(value) -> bool:
        return type(value) is str and len(value) == 32 and all(ch in '0123456789abcdef' for ch in value)

    def _directory(self, run_id: str) -> Path:
        self._check_admission()
        if not run_id or any(ch not in "0123456789abcdef" for ch in run_id):
            raise ValueError("run_id must be lowercase hexadecimal")
        directory = self.root / "runs" / run_id
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except BaseException:
            self._unavailable(cleanup=True)  # Path preparation, not an invented file handle.
        return directory

    def _append_original(self, path: Path, event: dict[str, Any], *, durable: bool = False) -> None:
        with self._original_resource(lambda: path.open('a', encoding='utf-8'), kind='file') as stream:
            payload = json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n'
            written = stream.write(payload)
            if type(written) is not int or written != len(payload):
                raise ValueError('original_write_not_acknowledged')
            if durable:
                stream.flush()
                os.fsync(stream.fileno())

    def append_event(self, run_id: str, event: dict[str, Any], *, durable: bool = False) -> None:
        directory = self._directory(run_id)
        path = directory / "events.jsonl"
        self._append_original(path, event, durable=durable)
        try:
            trace = event["kind"] in {"run_started", "tool_requested", "tool_completed", "tool_failed", "run_completed", "run_failed"}
        except BaseException:
            self._unavailable(cleanup=True)  # Original event may already be committed, never append again.
        if trace:
            self._append_original(directory / 'trace.jsonl', event)

    def append_operation(self, operation_id: str, operation: str, *, outcome: str | None = None) -> None:
        """Original journal operation boundary, not canonical provider receipts.

        Probe ID identifies an explicit original operation, NOT a Core/runtime
        run/thread, bill or parent/child. Sequence zero reserves metadata outside
        original Core EventBus counters; source order is original file order.
        File flush/fsync/close required before return, not a promise of directory
        fsync/power-loss survival/atomic hostile-path exclusion or SDK drain.
        """
        self._check_admission()
        if (type(operation_id) is not str or len(operation_id) != 32
                or any(ch not in '0123456789abcdef' for ch in operation_id)
                or operation not in {'run', 'probe'} or outcome not in {None, 'returned', 'failed'}):
            raise ValueError('legacy_operation_frame_invalid')
        payload = {'version': 1, 'operation_id': operation_id, 'operation': operation}
        if outcome is not None:
            payload['outcome'] = outcome
        self.append_event(operation_id, {
            'run_id': operation_id, 'sequence': 0, 'timestamp': datetime.now(timezone.utc).isoformat(),
            'kind': LEGACY_OPERATION_START if outcome is None else LEGACY_OPERATION_FINISH,
            'payload': payload,
        }, durable=True)

    def write_session(self, run_id: str, session: dict[str, Any]) -> None:
        directory = self._directory(run_id)
        temporary = directory / "session.json.tmp"
        with self._original_resource(lambda: temporary.open('x', encoding='utf-8'), kind='file', exclusive=True) as stream:
            payload = json.dumps(session, ensure_ascii=False, indent=2, allow_nan=False)
            written = stream.write(payload)
            if type(written) is not int or written != len(payload):
                raise ValueError('original_session_write_not_acknowledged')
        try:
            temporary.replace(directory / "session.json")  # Only AFTER SAME original exit returned.
        except BaseException:
            self._unavailable(cleanup=True)  # Keep original temp; no retry/repair/new source.

    def read_session(self, run_id: str) -> dict[str, Any] | None:
        self._check_admission()
        if not self._valid_id(run_id):
            return None
        path = self.root / "runs" / run_id / "session.json"
        raw = self._read_text(path)
        if raw is None:
            return None
        value = self._decode(raw)
        if (not isinstance(value, dict) or value.get('run_id') != run_id
                or type(value.get('status')) is not str
                or value['status'] not in {'completed', 'failed', 'interrupted', 'cancelled', 'running', 'queued', 'paused'}):
            self._unavailable()
        return value  # Original private Legacy metadata, NOT a redacted report.

    def read_events(self, run_id: str) -> list[dict]:
        self._check_admission()
        if not self._valid_id(run_id):
            self._unavailable()
        raw = self._read_text(self.root / 'runs' / run_id / 'events.jsonl', required=True)
        if not raw or not raw.endswith('\n'):
            self._unavailable()
        lines = raw.splitlines()
        if len(lines) > MAX_EVENT_ROWS:
            self._unavailable()
        result = []
        for line in lines:
            value = self._decode(line)
            if (not isinstance(value, dict) or set(value) != {'run_id', 'sequence', 'timestamp', 'kind', 'payload'}
                    or value['run_id'] != run_id or type(value['sequence']) is not int or value['sequence'] < 0
                    or type(value['timestamp']) is not str or type(value['kind']) is not str or not value['kind']
                    or not isinstance(value['payload'], dict)
                    or value['sequence'] == 0 and value['kind'] not in {LEGACY_OPERATION_START, LEGACY_OPERATION_FINISH}):
                self._unavailable()
            result.append(value)
        # Entire bounded source decoded BEFORE original 300-row display tail;
        # neither clipping nor terminal status proves full operation closure.
        return result[-300:]

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        self._check_admission()
        runs = self.root / "runs"
        candidates = []
        with self._original_resource(lambda: os.scandir(runs), kind='directory', missing_ok=True) as entries:
            if entries is None:
                return []
            for count, entry in enumerate(entries, 1):
                if count > MAX_HISTORY_ENTRIES:
                    self._unavailable()
                if not self._valid_id(entry.name) or not entry.is_dir(follow_symlinks=False):
                    continue
                path = Path(entry.path) / 'session.json'
                try:
                    info = path.stat()
                except FileNotFoundError:
                    continue  # Original unfinished/probe directory, not a session.
                if not stat.S_ISREG(info.st_mode):
                    self._unavailable()
                candidates.append((info.st_mtime, path))
        paths = [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)]
        result = []
        for path in paths:
            value = self.read_session(path.parent.name)
            if value is None:
                self._unavailable()  # Observed source vanished; not filtered success.
            result.append(value)
            if len(result) >= limit:
                break
        return result
