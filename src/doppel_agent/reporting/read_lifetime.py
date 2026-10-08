"""Retain original readonly SQL identities; no SQL policy or second executor.

Close receipts are method returns, NOT native handle/lock closure proof. Opaque
allocation/individual close failure is sticky even if a later method returns.
"""

from dataclasses import dataclass
from threading import get_ident
from typing import Any


class ReportReadCleanupError(RuntimeError):
    def __init__(self, source):
        super().__init__('report_read_cleanup_unresolved')
        self.source = source  # Private original references; never report/HTTP fields.


@dataclass
class _ReportCursorLifetime:
    cursor: Any = None
    execute_attempted: bool = False
    execute_returned: bool = False
    usable: bool = False
    close_attempted: bool = False
    close_returned: bool = False


class ReportReadSource:
    def __init__(self, *, failure=None, cleanup_failure=None):
        self.connection = None
        self.owner_thread = get_ident()
        self.connect_attempted = self.connect_returned = self.usable = False
        self.close_attempted = self.close_returned = False
        self.cleanup_uncertain = False
        self.cursors = []
        self._cleanup_started = self._cleanup_finished = False
        self._failure, self._cleanup_failure = failure, cleanup_failure

    def _retain(self):
        first = not self.cleanup_uncertain
        self.cleanup_uncertain = True  # BEFORE callbacks, including reentry.
        if first:
            for callback, args in ((self._failure, ()), (self._cleanup_failure, (self,))):
                if callback is not None:
                    try:
                        callback(*args)  # Original admission latch/root references only.
                    except BaseException:
                        pass

    def _unavailable(self):
        self._retain()
        raise ReportReadCleanupError(self) from None

    def _check_thread(self):
        if get_ident() != self.owner_thread:
            self._unavailable()  # Never manufacture a foreign-thread SQL close.

    def open_original(self, factory, uri):
        self._check_thread()
        if self.connect_attempted or self.cleanup_uncertain or self._cleanup_started:
            self._unavailable()
        self.connect_attempted = True  # BEFORE exact original sqlite3.connect.
        try:
            self.connection = factory(uri, uri=True, timeout=1)
            self.connect_returned = True
            if not all(callable(getattr(self.connection, name, None))
                       for name in ('execute', 'set_progress_handler', 'close')):
                self._unavailable()
            self.usable = True
        except BaseException:
            self._unavailable()
        return self

    @property
    def row_factory(self):
        return self.connection.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self.connection.row_factory = value

    def set_progress_handler(self, *args):
        return self.connection.set_progress_handler(*args)

    def execute(self, *args, **kwargs):
        self._check_thread()
        if not self.usable or self.cleanup_uncertain or self._cleanup_started:
            self._unavailable()
        original = _ReportCursorLifetime(execute_attempted=True)
        self.cursors.append(original)  # BEFORE original implicit cursor allocation.
        try:
            original.cursor = self.connection.execute(*args, **kwargs)
            original.execute_returned = True
            if not callable(getattr(original.cursor, 'close', None)):
                self._unavailable()
            original.usable = True
        except BaseException:
            self._unavailable()
        return original.cursor  # SAME cursor, iteration/fetch/projection untouched.

    def close_original(self):
        self._check_thread()
        if self._cleanup_started:
            if not self._cleanup_finished or self.cleanup_uncertain:
                self._unavailable()
            return
        self._cleanup_started = True
        # An opaque execute still permits independent disposal of the known
        # connection and OTHER known cursors, exactly once. It cannot clear fault.
        seen = set()
        for original in self.cursors:
            if not original.usable:
                self._retain()
                continue
            identity = id(original.cursor)
            if identity in seen:
                continue  # Same returned cursor identity must not get two closes.
            seen.add(identity)
            original.close_attempted = True
            try:
                original.cursor.close()
                original.close_returned = True
            except BaseException:
                self._retain()
        if self.usable:
            self.close_attempted = True
            try:
                self.connection.close()
                self.close_returned = True
            except BaseException:
                self._retain()
        elif self.connect_attempted:
            self._retain()  # No usable identity: neither invented close nor retry.
        self._cleanup_finished = True
        if self.cleanup_uncertain:
            raise ReportReadCleanupError(self) from None
