"""OS-owned, nonblocking Local Mode startup/recovery exclusion.

The sentinel is never unlinked: deleting it can let two processes lock different
inodes. Kernel locks, not PID/mtime guesses, release ownership on process death.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Any


class WorkspaceOwnershipError(RuntimeError):
    def __init__(self, source):
        super().__init__("workspace owner cleanup is unresolved")
        self.source = source  # Private exact original owner/file/lock, not a native ownership proof.


@dataclass
class _OwnerFileLifetime:
    handle: Any = None
    open_attempted: bool = False
    open_returned: bool = False
    usable: bool = False
    lock_attempted: bool = False
    lock_returned: bool = False
    close_attempted: bool = False
    close_returned: bool = False


class WorkspaceOwner:
    def __init__(
        self,
        path: Path,
        *,
        failure: Callable[[], None] | None = None,
        cleanup_failure: Callable[[WorkspaceOwner], None] | None = None,
    ):
        self.path = path
        self._file = None
        self._source = None
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._cleanup_uncertain = Event()
        self._operation_lock = Lock()
        self._unresolved_sources: dict[int, _OwnerFileLifetime] = {}

    @property
    def held(self) -> bool:
        # Conservative retained lease after failed close, NOT an assertion that
        # OS lock survived effect-then-throw. Borrow/admission also check cleanup.
        return self._file is not None and self._source is not None and self._source.lock_returned

    @property
    def cleanup_uncertain(self) -> bool:
        return self._cleanup_uncertain.is_set()

    def check_cleanup(self) -> None:
        if self.cleanup_uncertain:
            raise WorkspaceOwnershipError(self)

    def _retain_uncertain(self, source) -> None:
        first = not self.cleanup_uncertain
        self._cleanup_uncertain.set()
        if source is not None:
            self._unresolved_sources[id(source)] = source
        if first and self._failure is not None:
            try:
                self._failure()
            except BaseException:
                pass
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # Same source even if factory never published its handle.
            except BaseException:
                pass

    @contextmanager
    def _original_operation(self):
        self.check_cleanup()
        if not self._operation_lock.acquire(blocking=False):
            self._retain_uncertain(self._source)
            raise WorkspaceOwnershipError(self)
        try:
            self.check_cleanup()
            yield
        finally:
            self._operation_lock.release()

    def _close_original(self, source) -> None:
        if source.close_attempted:
            if source.close_returned:
                return
            raise WorkspaceOwnershipError(self)
        if not source.usable:
            self._retain_uncertain(source)
            raise WorkspaceOwnershipError(self)
        source.close_attempted = True  # BEFORE same actual close, no retry after effect-then-throw.
        try:
            source.handle.close()
            self.check_cleanup()  # Late return cannot erase a concurrent/reentrant original fault.
            source.close_returned = True
            if self._file is source.handle:
                self._file = None
        except BaseException:
            self._retain_uncertain(source)
            raise WorkspaceOwnershipError(self) from None

    def borrow(self) -> BorrowedWorkspaceOwner:
        return BorrowedWorkspaceOwner(self)

    def acquire(self) -> None:
        with self._original_operation():
            if self._file is not None:
                if self.held and not self._source.close_attempted:
                    return
                self._retain_uncertain(self._source)
                raise WorkspaceOwnershipError(self)
            source = self._source = _OwnerFileLifetime(open_attempted=True)  # BEFORE original file factory.
            try:
                source.handle = handle = self.path.open("a+b")
                source.open_returned = True
                if not all(
                    callable(getattr(handle, name, None))
                    for name in ("seek", "tell", "write", "flush", "fileno", "close")
                ):
                    raise ValueError("original lock file identity unavailable")
                source.usable = True
            except BaseException:
                self._retain_uncertain(source)
                raise WorkspaceOwnershipError(self) from None
            self._file = handle  # Exact original BEFORE any setup/lock can fail.
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                source.lock_attempted = True
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.check_cleanup()
                source.lock_returned = True
            except BaseException as error:
                self._close_original(source)  # Independent known handle once, before refusal.
                if not isinstance(error, Exception):
                    raise  # Original cancellation with known original close, not another lock attempt.
                raise RuntimeError("Local Mode workspace is already owned or cannot be locked") from None

    def release(self) -> None:
        with self._original_operation():
            if self._file is None:
                return
            self._close_original(self._source)


class BorrowedWorkspaceOwner:
    """RunService lease under a desktop owner that also covers Legacy IO.

    The desktop must retain the actual lock through *both* servers' cleanup.
    Lifespan failure must not release ownership under still-running Legacy IO.
    """

    def __init__(self, owner: WorkspaceOwner):
        self.owner = owner
        self.path = owner.path

    def acquire(self) -> None:
        self.owner.check_cleanup()  # A retained reference isn't known original lease admission.
        if not self.owner.held:
            raise RuntimeError("desktop workspace owner is not held")
        if self.owner._source.close_attempted:
            raise WorkspaceOwnershipError(self.owner)

    def release(self) -> None:
        self.owner.check_cleanup()
        # Only the desktop resource owner may release the outer lease; no file/OS operation here.
