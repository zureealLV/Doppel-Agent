"""Cancellable subprocess trees with Windows Job Object supervision."""

from __future__ import annotations

import asyncio
import inspect
import math
import os
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from time import monotonic
from time import sleep
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..owned_async import await_durable


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    supervision: str


@dataclass(frozen=True)
class BinaryProcessResult:
    """Lossless bounded output, for plumbing protocols rather than UI text."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: bytes
    stderr: bytes
    supervision: str


class ProcessOutputLimitError(RuntimeError):
    """Output overflow is not a successfully truncated protocol response."""


class ProcessReadError(RuntimeError):
    """Invalid original binary read, distinct from unproved resource cleanup."""

    def __init__(self, message: str, *, source=None):
        super().__init__(message)
        self.source = source  # Private original pipe, never provider/report payload.


class ProcessCleanupError(RuntimeError):
    """Owned tree cleanup is unproved; retain registration and reject admission."""

    def __init__(self, message: str, *, managed=None, source=None):
        super().__init__(message)
        self.managed = managed
        self.source = source  # Private exact original source, never report/API/provider payload.


class ProcessSupervisionError(RuntimeError):
    """Strict ownership could not be established before child execution."""

    def __init__(self, message: str, *, source=None):
        super().__init__(message)
        self.source = source  # Known original admission refusal, not cleanup uncertainty.


@dataclass
class _ProcessStartLifetime:
    owner_thread: int = field(default_factory=threading.get_ident)
    process: Any = None
    job: Any = None
    managed: Any = None
    spawn_attempted: bool = False
    spawn_returned: bool = False
    job_attempted: bool = False
    job_returned: bool = False
    resume_attempted: bool = False
    resume_returned: bool = False
    cleanup_uncertain: bool = False
    original_sources: dict[int, Any] = field(default_factory=dict)


@dataclass
class _ProcessRegistrationLifetime:
    managed: Any
    attempted: bool = False
    returned: bool = False


@dataclass
class _ProcessFileLifetime:
    kind: str
    resource: Any = None
    owner_thread: int = field(default_factory=threading.get_ident)
    factory_attempted: bool = False
    enter_returned: bool = False
    close_attempted: bool = False
    close_returned: bool = False
    cleanup_uncertain: bool = False
    managed: Any = None  # SAME original text operation, no stream-attached synthetic authority.
    operation: Any = None  # SAME original call's lifetime, not stream-attached authority.
    job_close_returned: bool = False
    producer_settled: bool = True  # False BEFORE start, known text parent wait + managed close afterwards.
    read_attempts: int = 0
    read_returns: int = 0
    read_bytes: int = 0
    read_failed: bool = False
    eof_seen: bool = False


@dataclass
class _ProcessWorkerLifetime:
    kind: str
    managed: Any
    function: Any
    args: tuple = ()
    source: Any = None
    coroutine: Any = None
    source_coroutine: Any = None
    coroutine_attempted: bool = False
    coroutine_returned: bool = False
    task: Any = None
    task_attempted: bool = False
    task_returned: bool = False
    started: bool = False
    finished: bool = False  # Actual original callback returned/raised, NOT Future.done().
    returned: bool = False
    result: Any = None
    cleanup_known: bool = False


@dataclass
class _ProcessGatherLifetime:
    sources: tuple
    attempted: bool = False
    returned: bool = False
    future: Any = None


@dataclass
class _ProcessOperationLifetime:
    run_id: str
    managed: Any
    kind: str
    strict: bool = False
    files: list = field(default_factory=list)
    workers: list = field(default_factory=list)
    gathers: list = field(default_factory=list)
    drainer: Any = None
    cleanup_uncertain: bool = False
    terminate_attempted: bool = False
    terminate_returned: bool = False
    terminate_thread: int | None = None
    terminate_done: threading.Event = field(default_factory=threading.Event)
    drained: bool = False
    read_failure: Any = None
    drain_worker: Any = None
    job_close_attempted: bool = False
    job_close_returned: bool = False
    unregister_attempted: bool = False
    unregister_returned: bool = False
    close_resources: tuple = ()  # Original close snapshot retained before worker/gather allocation.


def _close_original_process_file(frame: _ProcessFileLifetime, details=(None, None, None)) -> None:
    if not frame.producer_settled:
        frame.cleanup_uncertain = True
        raise ProcessCleanupError(
            "process file producer cleanup unproved", managed=frame.managed, source=frame
        )
    if frame.close_attempted:
        if frame.close_returned:
            return
        raise ProcessCleanupError("process file cleanup unproved", source=frame)
    if frame.resource is None:
        frame.cleanup_uncertain = True
        raise ProcessCleanupError("process file allocation unproved", source=frame)  # No invented close.
    frame.close_attempted = True  # BEFORE SAME actual original exit/close.
    try:
        if frame.enter_returned:
            frame.resource.__exit__(*details)
        else:
            frame.resource.close()
        frame.close_returned = True  # Original API return, not OS/tree/producer drain proof.
    except BaseException:
        frame.cleanup_uncertain = True
        raise ProcessCleanupError("process file cleanup unproved", source=frame) from None


@dataclass
class _OriginalResumeHandleLifetime:
    kind: str
    handle: Any = None
    factory_attempted: bool = False
    factory_returned: bool = False
    usable: bool = False
    close_attempted: bool = False
    close_returned: bool = False
    close_response: Any = None
    cleanup_uncertain: bool = False


@dataclass
class _OriginalResumeLifetime:
    process_id: int
    kernel32: Any = None
    snapshot: Any = field(default_factory=lambda: _OriginalResumeHandleLifetime("snapshot"))
    thread: Any = field(default_factory=lambda: _OriginalResumeHandleLifetime("thread"))
    entry: Any = None
    enumeration_attempts: int = 0
    enumeration_returns: int = 0
    resume_attempted: bool = False
    resume_returned: bool = False
    resume_response: Any = None
    cleanup_uncertain: bool = False


def _open_original_resume_handle(source, original, factory):
    import ctypes

    original.factory_attempted = True  # BEFORE original snapshot/OpenThread factory.
    try:
        original.handle = factory()
        original.factory_returned = True
    except BaseException:
        original.cleanup_uncertain = source.cleanup_uncertain = True
        raise ProcessCleanupError("initial thread handle allocation unproved", source=source) from None
    if (
        original.handle is None
        or type(original.handle) is int
        and original.handle in (0, -1, ctypes.c_void_p(-1).value)
    ):
        raise ProcessSupervisionError("initial thread handle unavailable", source=source)
    if type(original.handle) is not int or original.handle < 0:
        original.cleanup_uncertain = source.cleanup_uncertain = True
        raise ProcessCleanupError("initial thread handle allocation unproved", source=source) from None
    original.usable = True  # Known returned original handle ONLY, no resume/tree authority.
    return original.handle


def _close_original_resume_handle(source, original):
    if original.close_attempted:
        if original.close_returned:
            return
        raise ProcessCleanupError("initial thread handle close unproved", source=source)
    if not original.usable:
        original.cleanup_uncertain = source.cleanup_uncertain = True
        raise ProcessCleanupError("initial thread handle allocation unproved", source=source)
    original.close_attempted = True  # Exact original handle once; keep it even if close throws after effect.
    try:
        original.close_response = source.kernel32.CloseHandle(original.handle)
        if type(original.close_response) not in (int, bool) or not original.close_response:
            raise ValueError("original handle close not acknowledged")
        original.close_returned = True
    except BaseException:
        original.cleanup_uncertain = source.cleanup_uncertain = True
        raise ProcessCleanupError("initial thread handle close unproved", source=source) from None


def _resume_suspended_primary_thread(process_id: int) -> None:
    """Resume the sole initial thread after Job attachment, using Win32 APIs.

    Popen closes the CreateProcess thread handle. A bounded Toolhelp snapshot
    recovers that suspended thread's identity; do not use undocumented NT APIs.
    No caller may use this helper for an already-running arbitrary process.
    """
    import ctypes
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    source = _OriginalResumeLifetime(process_id)  # BOTH original handle frames before either factory.
    try:
        kernel32 = source.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ("Thread32First", "Thread32Next"):
            function = getattr(kernel32, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            function.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = _open_original_resume_handle(
            source, source.snapshot, lambda: kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        )
        thread_ids: list[int] = []
        try:
            deadline = monotonic() + 2
            entry = source.entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            source.enumeration_attempts += 1
            exists = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            source.enumeration_returns += 1
            count = 0
            while True:
                if type(exists) not in (int, bool):
                    raise ValueError("original thread enumeration acknowledgement unavailable")
                if not exists:
                    break
                count += 1
                if count > 65_536 or monotonic() > deadline:
                    raise ValueError("original thread snapshot limit exceeded")
                if entry.dwSize < ThreadEntry.th32OwnerProcessID.offset + ctypes.sizeof(wintypes.DWORD):
                    raise ValueError("original thread snapshot incomplete")
                if entry.th32OwnerProcessID == process_id:
                    thread_ids.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                source.enumeration_attempts += 1
                exists = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
                source.enumeration_returns += 1
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES, not a partial snapshot
                raise ValueError("original thread enumeration failed")
        finally:
            _close_original_resume_handle(source, source.snapshot)
        if len(thread_ids) != 1:
            raise ValueError("original suspended thread identity ambiguous")
        thread = _open_original_resume_handle(
            source, source.thread, lambda: kernel32.OpenThread(0x0002, False, thread_ids[0])
        )
        try:
            source.resume_attempted = True  # Original effect attempt retained BEFORE ResumeThread.
            try:
                source.resume_response = kernel32.ResumeThread(thread)
                source.resume_returned = True
            except BaseException:
                source.cleanup_uncertain = True
                raise ProcessCleanupError("initial thread resume unproved", source=source) from None
            if type(source.resume_response) is not int or not 0 <= source.resume_response <= 0xFFFFFFFF:
                source.cleanup_uncertain = True
                raise ProcessCleanupError("initial thread resume unproved", source=source)
            if source.resume_response != 1:
                raise ValueError("original suspended thread did not resume exactly once")
        finally:
            # Known original handle can close once even if resume effect is unknown;
            # known close DOES NOT reset that independent operation uncertainty.
            _close_original_resume_handle(source, source.thread)
    except BaseException:
        if source.cleanup_uncertain:
            raise ProcessCleanupError("initial thread cleanup unproved", source=source) from None
        raise ProcessSupervisionError("initial thread resume unavailable", source=source) from None


@dataclass
class _WindowsJobQueryLifetime:
    handle: Any
    value: Any
    written: Any
    attempted: bool = False
    returned: bool = False
    response: Any = None


@dataclass
class _WindowsJobOperationLifetime:
    kind: str
    handle: Any
    owner_thread: int = field(default_factory=threading.get_ident)
    attempted: bool = False
    returned: bool = False
    acknowledged: bool = False
    response: Any = None
    finished: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    queries: list = field(default_factory=list)


class _WindowsJob:
    """Small ctypes wrapper kept private so non-Windows imports stay harmless."""

    def __init__(self, process_handle: int):
        self._handle = None
        self._kernel32 = None
        self._create_attempted = False
        self._create_returned = False
        self._close_attempted = False
        self._close_returned = False
        self._close_response = None
        self._cleanup_uncertain = threading.Event()
        self._operation_lock = threading.Lock()
        self._termination_source = None
        self._accounting_source = None
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        self._kernel32 = kernel32  # BEFORE SAME original allocation/setup API.
        self._create_attempted = True
        try:
            handle = kernel32.CreateJobObjectW(None, None)
            self._create_returned = True
        except BaseException:
            self._cleanup_uncertain.set()  # Opaque original allocation, no known handle to close.
            raise ProcessCleanupError("job allocation unproved", source=self) from None
        if handle is None or type(handle) is int and handle == 0:
            raise ProcessSupervisionError("job allocation unavailable", source=self) from None
        self._handle = handle  # Exact original handle retained BEFORE any setup can throw.
        if type(handle) is not int or handle < 0:
            self._cleanup_uncertain.set()  # Unusable original return isn't known close authority.
            raise ProcessCleanupError("job allocation unproved", source=self) from None
        try:
            limits = ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
            configured = kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            if type(configured) not in (int, bool) or not configured:
                raise ProcessSupervisionError("job setup unavailable", source=self)
            assigned = kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process_handle))
            if type(assigned) not in (int, bool) or not assigned:
                raise ProcessSupervisionError("job setup unavailable", source=self)
        except BaseException:
            # Setup failed; SAME original handle closes once. A false/throwing
            # close retains this unpublished source, never clears/retries it.
            self.close_checked()
            raise ProcessSupervisionError("job setup unavailable", source=self) from None

    @property
    def cleanup_uncertain(self) -> bool:
        return self._cleanup_uncertain.is_set()

    def _check_original_cleanup(self) -> None:
        if self.cleanup_uncertain:
            raise ProcessCleanupError("job cleanup identity unavailable", source=self)

    def terminate(self) -> None:
        # Compatibility still lacks strict tree-zero proof, but false/unusable
        # TerminateJobObject ACK must never mean known original termination.
        self.terminate_checked()

    def _original_job_operation(self, attribute: str, kind: str):
        with self._operation_lock:
            # Atomic original admission relative to close; native API itself
            # runs outside the lock, retaining its frame before entry.
            self._check_original_cleanup()
            if not self._handle:
                raise ProcessCleanupError("job cleanup identity unavailable", source=self)
            if self._close_attempted:
                self._cleanup_uncertain.set()
                raise ProcessCleanupError("job operation cleanup unproved", source=self)
            frame = getattr(self, attribute)
            first = frame is None
            if first:
                frame = _WindowsJobOperationLifetime(kind, self._handle)
                setattr(self, attribute, frame)  # BEFORE original API/query sequence, no lost-return retry.
        if not first:
            if not frame.done.is_set():
                if frame.owner_thread == threading.get_ident() or not frame.done.wait(10):
                    self._cleanup_uncertain.set()
                    raise ProcessCleanupError("job operation cleanup unproved", source=self)
            self._check_original_cleanup()
            if not frame.finished or not frame.acknowledged:
                self._cleanup_uncertain.set()
                raise ProcessCleanupError("job operation cleanup unproved", source=self)
        return frame, first

    def terminate_checked(self) -> None:
        frame, first = self._original_job_operation("_termination_source", "terminate")
        if not first:
            return
        frame.attempted = True
        try:
            frame.response = self._kernel32.TerminateJobObject(frame.handle, 1)
            frame.returned = True
            if type(frame.response) not in (int, bool) or not frame.response:
                raise ValueError("original termination not acknowledged")
            self._check_original_cleanup()  # Concurrent original fault cannot be overwritten by late ACK.
            frame.acknowledged = True  # Native API ACK only, NOT accounting/tree-zero proof.
        except BaseException:
            self._cleanup_uncertain.set()
            raise ProcessCleanupError("job termination unproved", source=self) from None
        finally:
            frame.finished = True
            frame.done.set()

    def wait_empty(self, timeout_seconds: float = 10) -> None:
        """Wait for actual Job accounting, not just the original parent's wait."""
        frame, first = self._original_job_operation("_accounting_source", "accounting")
        if not first:
            return
        frame.attempted = True
        try:
            import ctypes
            from ctypes import wintypes

            class Accounting(ctypes.Structure):
                _fields_ = [
                    (name, ctypes.c_longlong)
                    for name in (
                        "TotalUserTime",
                        "TotalKernelTime",
                        "ThisPeriodTotalUserTime",
                        "ThisPeriodTotalKernelTime",
                    )
                ]
                _fields_ += [
                    (name, wintypes.DWORD)
                    for name in (
                        "TotalPageFaultCount",
                        "TotalProcesses",
                        "ActiveProcesses",
                        "TotalTerminatedProcesses",
                    )
                ]

            deadline = monotonic() + timeout_seconds
            while True:
                self._check_original_cleanup()
                if self._handle != frame.handle or self._close_attempted:
                    raise ValueError("original accounting handle unavailable")
                original = _WindowsJobQueryLifetime(frame.handle, Accounting(), wintypes.DWORD())
                frame.queries.append(original)  # Exact buffers/attempt retained BEFORE original Query.
                original.attempted = True
                original.response = self._kernel32.QueryInformationJobObject(
                    original.handle,
                    1,
                    ctypes.byref(original.value),
                    ctypes.sizeof(original.value),
                    ctypes.byref(original.written),
                )
                original.returned = True
                if (
                    type(original.response) not in (int, bool)
                    or not original.response
                    or original.written.value != ctypes.sizeof(original.value)
                ):
                    raise ValueError("original accounting not acknowledged")
                self._check_original_cleanup()
                if original.value.ActiveProcesses == 0:
                    frame.returned = frame.acknowledged = True  # Known ORIGINAL accounting observation.
                    return
                if monotonic() >= deadline:
                    raise ValueError("original accounting deadline exceeded")
                sleep(0.01)
        except BaseException:
            self._cleanup_uncertain.set()
            raise ProcessCleanupError("job accounting unproved", source=self) from None
        finally:
            frame.finished = True
            frame.done.set()

    def close(self) -> None:
        # Compatibility tree semantics stay weaker; false CloseHandle is never
        # known handle closure in either path.
        self.close_checked()

    def close_checked(self) -> None:
        with self._operation_lock:
            self._check_original_cleanup()
            if self._close_attempted:
                if self._close_returned:
                    return
                self._cleanup_uncertain.set()
                raise ProcessCleanupError("job handle cleanup unproved", source=self)
            if not self._handle:
                return
            if any(
                frame is not None and not frame.finished
                for frame in (self._termination_source, self._accounting_source)
            ):
                self._cleanup_uncertain.set()
                raise ProcessCleanupError("job handle cleanup unproved", source=self)
            handle = self._handle
            self._close_attempted = True  # BEFORE SAME original CloseHandle.
        try:
            self._close_response = self._kernel32.CloseHandle(handle)
            if type(self._close_response) not in (int, bool) or not self._close_response:
                raise ValueError("original close not acknowledged")
            with self._operation_lock:
                self._check_original_cleanup()  # Late close ACK cannot erase another original fault.
                self._close_returned = True  # API acknowledgement, NOT physical/native proof here.
                self._handle = None
        except BaseException:
            self._cleanup_uncertain.set()
            raise ProcessCleanupError("job handle cleanup unproved", source=self) from None


@dataclass
class _ManagedProcess:
    process: subprocess.Popen[Any]
    supervision: str
    job: _WindowsJob | None
    startup: _ProcessStartLifetime | None = None

    def terminate_tree(self) -> None:
        # Job/group ownership can outlive the parent. In particular, a child
        # can keep binary output pipes open after the parent has exited.
        if self.job is not None:
            self.job.terminate()
            return
        if os.name == "nt":
            # taskkill cannot recover an exited parent's descendant identity.
            # This pre-existing fallback is weaker than Job Object ownership.
            if self.process.poll() is not None:
                return
            subprocess.run(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            return
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def terminate_strict_tree(self) -> None:
        if self.job is not None:
            self.job.terminate_checked()
        else:
            self.terminate_tree()  # Non-Windows process group, explicitly weaker.

    def close(self, *, strict: bool = False) -> None:
        if self.job is not None:
            if strict:
                self.job.close_checked()
            else:
                self.job.close()


class ProcessSupervisor:
    """Start shell-free commands and guarantee cancellation reaches descendants."""

    def __init__(
        self,
        *,
        failure: Callable[[], None] | None = None,
        cleanup_failure: Callable[[ProcessSupervisor], None] | None = None,
    ) -> None:
        self._active: dict[str, list[_ManagedProcess]] = {}
        self._lock = threading.Lock()
        self._cleanup_failed = False
        self._closing = False
        self._drainers: dict[int, threading.Event] = {}
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._unresolved_sources: dict[int, Any] = {}
        self._registrations: dict[int, _ProcessRegistrationLifetime] = {}
        self._operations: dict[int, _ProcessOperationLifetime] = {}
        self._close_source = None
        self._close_task = None

    def _fail_original(self, owned, source, message):
        owned.cleanup_uncertain = True
        self._mark_cleanup_failed(owned)
        self._mark_cleanup_failed(source)
        raise ProcessCleanupError(message, managed=owned.managed, source=source) from None

    def _retain_original_close_failure(self, owned, error):
        # Memory-only owner fence. Keep EVERY already-created original source,
        # including siblings which gather may not have settled when it failed.
        # No new gather/worker, cancellation, close, inspection or cleanup retry.
        owned.cleanup_uncertain = True
        self._mark_cleanup_failed(owned)
        source = getattr(error, "source", None)
        if source is not None:
            self._mark_cleanup_failed(source)
        for original in owned.close_resources:
            self._mark_cleanup_failed(original)
        for frame in owned.workers:
            self._mark_cleanup_failed(frame)
            if frame.source is not None:
                self._mark_cleanup_failed(frame.source)
        for frame in owned.gathers:
            self._mark_cleanup_failed(frame)

    def _begin_original(self, run_id, managed, kind, *, files=(), strict=False):
        owned = _ProcessOperationLifetime(run_id, managed, kind, strict=strict, files=list(files))
        with self._lock:
            previous = self._operations.get(id(managed))
            if previous is None:
                self._operations[id(managed)] = owned  # BEFORE original registration/worker factories.
        if previous is not None:
            self._fail_original(previous, previous, "process operation identity unproved")
        for original in owned.files:
            original.operation = owned
        self._admit_original(run_id, managed)
        with self._lock:
            owned.drainer = self._drainers.get(id(managed))
        if owned.drainer is None:
            self._fail_original(owned, owned, "process drainer identity unproved")
        return owned

    def _make_original_task(self, owned, frame, factory):
        frame.coroutine_attempted = True
        try:
            frame.coroutine = factory()
            frame.coroutine_returned = True
            if not inspect.iscoroutine(frame.coroutine):
                raise ValueError("original coroutine unavailable")
            frame.task_attempted = True  # BEFORE SAME create_task, including lost-return factories.
            frame.task = asyncio.create_task(frame.coroutine)
            frame.task_returned = True
            if (
                not isinstance(frame.task, asyncio.Task)
                or frame.task.get_coro() is not frame.coroutine
                or frame.task.get_loop() is not asyncio.get_running_loop()
            ):
                raise ValueError("original task identity unavailable")
            return frame
        except BaseException:
            # Do NOT close/retry the coroutine: an unreturned Task may own it.
            self._fail_original(owned, frame, "process worker allocation unproved")

    def _spawn_original_thread(self, owned, kind, function, *args, source=None):
        frame = _ProcessWorkerLifetime(kind, owned.managed, function, args, source=source)
        owned.workers.append(frame)  # BEFORE to_thread or task factory.

        def invoke():
            frame.started = True
            try:
                value = function(*args)  # SAME original function/stream, no substitute worker.
                frame.result, frame.returned = value, True
                return value
            except (ProcessCleanupError, ProcessReadError):
                raise
            except BaseException:
                # Includes StopIteration, which cannot be forwarded into asyncio
                # Future as an ordinary exception. No private raw error payload.
                raise ProcessCleanupError("process worker unavailable", source=frame) from None
            finally:
                frame.finished = True  # Actual callback lifetime, not Future cancellation.

        return self._make_original_task(owned, frame, lambda: asyncio.to_thread(invoke))

    def _spawn_original_async(self, owned, kind, factory):
        frame = _ProcessWorkerLifetime(kind, owned.managed, factory)
        owned.workers.append(frame)

        async def invoke():
            frame.started = True
            try:
                value = await frame.source_coroutine
                frame.result, frame.returned = value, True
                return value
            finally:
                frame.finished = True

        def prepare():
            frame.source_coroutine = factory()  # Original drain/cancel/close coroutine.
            if not inspect.iscoroutine(frame.source_coroutine):
                raise ValueError("original async source unavailable")
            return invoke()

        return self._make_original_task(owned, frame, prepare)

    def _gather_original(self, owned, sources, *, return_exceptions=False):
        frame = _ProcessGatherLifetime(tuple(sources))
        owned.gathers.append(frame)  # BEFORE SAME gather allocation, not after return.
        frame.attempted = True
        try:
            frame.future = asyncio.gather(*frame.sources, return_exceptions=return_exceptions)
            frame.returned = True
            if (
                not isinstance(frame.future, asyncio.Future)
                or frame.future.get_loop() is not asyncio.get_running_loop()
            ):
                raise ValueError("original gather unavailable")
            return frame.future
        except BaseException:
            self._fail_original(owned, frame, "process gather allocation unproved")

    def _inspect_original_worker(self, owned, frame):
        task = frame.task
        if (
            not frame.task_returned
            or not isinstance(task, asyncio.Task)
            or not frame.finished
            or not task.done()
            or task.cancelled()
        ):
            self._fail_original(owned, frame, "process worker cleanup unproved")
        error = task.exception()
        if error is None and (not frame.returned or task.result() is not frame.result):
            self._fail_original(owned, frame, "process worker receipt unproved")
        frame.cleanup_known = True  # Worker callback finished ONLY, no process/tree receipt.
        return error

    def _parent_wait_original(self, owned, frame):
        error = self._inspect_original_worker(owned, frame)
        if (
            error is not None
            or type(frame.result) is not int
            or type(getattr(owned.managed.process, "returncode", None)) is not int
            or frame.result != owned.managed.process.returncode
        ):
            self._fail_original(
                owned, getattr(error, "source", None) or frame, "process wait cleanup unproved"
            )
        return frame.result

    def _terminate_original(self, owned):
        with self._lock:
            repeated = owned.terminate_attempted
            if not repeated:
                owned.terminate_attempted = True
                owned.terminate_thread = threading.get_ident()
        if repeated:
            # Both read workers/cancel/drain join the SAME attempt. No second
            # native termination. This wait creates no new executor/worker.
            if not owned.terminate_done.is_set() and (
                owned.terminate_thread == threading.get_ident() or not owned.terminate_done.wait(10)
            ):
                self._fail_original(owned, owned, "process termination cleanup unproved")
            if owned.cleanup_uncertain or self.cleanup_failed or not owned.terminate_returned:
                self._fail_original(owned, owned, "process termination cleanup unproved")
            return
        try:
            if owned.strict and isinstance(owned.managed, _ManagedProcess):
                owned.managed.terminate_strict_tree()
            else:
                owned.managed.terminate_tree()
            if owned.cleanup_uncertain or self.cleanup_failed:
                # SAME original effect may return after a concurrent/reentrant
                # join failed. A late ACK never restores cleanup authority.
                self._fail_original(owned, owned, "process termination cleanup unproved")
            owned.terminate_returned = True
        except BaseException as exc:
            self._fail_original(
                owned, getattr(exc, "source", None) or owned, "process termination cleanup unproved"
            )
        finally:
            owned.terminate_done.set()

    def _retire_original(self, owned):
        if owned.unregister_attempted:
            if owned.unregister_returned:
                return
            self._fail_original(owned, owned, "process unregister unproved")
        if owned.cleanup_uncertain:
            self._fail_original(owned, owned, "process cleanup quarantine")
        if (
            not owned.drained
            or not owned.job_close_returned
            or any(not original.close_returned for original in owned.files)
        ):
            self._fail_original(owned, owned, "process resource cleanup unproved")
        owned.unregister_attempted = True  # BEFORE original removal + drainer.set.
        try:
            self._unregister(owned.run_id, owned.managed)
            owned.unregister_returned = True
        except BaseException:
            self._fail_original(owned, owned, "process unregister unproved")
        with self._lock:
            self._operations.pop(id(owned.managed), None)  # Only actual unregister return.

    @contextmanager
    def _original_spool(self, frame: _ProcessFileLifetime):
        frame.factory_attempted = True  # BEFORE SAME original tempfile.TemporaryFile.
        try:
            frame.resource = tempfile.TemporaryFile()
        except BaseException:
            frame.cleanup_uncertain = True
            self._mark_cleanup_failed(frame)
            raise ProcessCleanupError("process file allocation unproved", source=frame) from None
        try:
            stream = frame.resource.__enter__()
            frame.enter_returned = True
            if stream is None:
                raise ValueError("original spool enter not acknowledged")
        except BaseException:
            try:
                _close_original_process_file(frame)
            except ProcessCleanupError:
                self._mark_cleanup_failed(frame)
                raise
            raise ProcessSupervisionError("process spool unavailable", source=frame) from None
        try:
            yield stream
        except BaseException as exc:
            if not frame.producer_settled:
                # SAME original process/wait/registration is unproved. Preserve
                # BOTH original spools without inventing an exit/close attempt.
                frame.cleanup_uncertain = True
                self._mark_cleanup_failed(frame)
                raise  # Keep original fixed startup/register/wait failure source.
            try:
                _close_original_process_file(frame, (type(exc), exc, exc.__traceback__))
            except ProcessCleanupError:
                self._mark_cleanup_failed(frame)
                raise
            raise  # A suppressing exit must not fabricate a process receipt.
        else:
            if not frame.producer_settled:
                frame.cleanup_uncertain = True
                self._mark_cleanup_failed(frame)
                raise ProcessCleanupError(
                    "process file producer cleanup unproved", managed=frame.managed, source=frame
                )
            try:
                _close_original_process_file(frame)
            except ProcessCleanupError:
                self._mark_cleanup_failed(frame)
                raise

    def _mark_cleanup_failed(self, source=None) -> None:
        with self._lock:
            self._cleanup_failed = True
            if source is not None:
                self._unresolved_sources[id(source)] = source
        if self._failure is not None:
            try:
                self._failure()  # SAME original owner latch, no IO or cleanup retry.
            except BaseException:
                pass
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # SAME supervisor owns SAME managed/source/drainer refs.
            except BaseException:
                pass

    def _retain_startup_failure(self, run_id: str, exc: ProcessCleanupError) -> None:
        # Retain source BEFORE attempted registration can fail/mask this error.
        self._mark_cleanup_failed(exc.source if exc.source is not None else exc.managed)
        if exc.managed is not None:
            self._admit_original(run_id, exc.managed)

    def _admit_original(self, run_id: str, managed: Any) -> None:
        with self._lock:
            frame = self._registrations.get(id(managed))
            repeated = frame is not None
            if frame is None:
                frame = _ProcessRegistrationLifetime(managed)
                self._registrations[id(managed)] = frame
        if repeated:
            self._mark_cleanup_failed(frame)
            raise ProcessCleanupError("process registration unproved", managed=managed, source=frame)
        frame.attempted = True  # BEFORE SAME original registration/drainer mutation.
        try:
            self._register(run_id, managed)
            frame.returned = True
        except BaseException:
            self._mark_cleanup_failed(frame)
            raise ProcessCleanupError(
                "process registration unproved", managed=managed, source=frame
            ) from None

    @property
    def cleanup_failed(self) -> bool:
        with self._lock:
            return self._cleanup_failed

    def _assert_admission(self) -> None:
        if self.cleanup_failed:
            raise ProcessCleanupError("process cleanup quarantine")
        if self._closing:
            raise RuntimeError("process supervisor is closed")

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._active.values())

    def _register(self, run_id: str, managed: _ManagedProcess) -> None:
        with self._lock:
            self._active.setdefault(run_id, []).append(managed)
            self._drainers[id(managed)] = threading.Event()

    def _unregister(self, run_id: str, managed: _ManagedProcess) -> None:
        with self._lock:
            items = self._active.get(run_id, [])
            if managed in items:
                items.remove(managed)
            if not items:
                self._active.pop(run_id, None)
            event = self._drainers.pop(id(managed), None)
            if event is not None:
                event.set()
            self._registrations.pop(id(managed), None)  # Only original known unregister return path.

    @staticmethod
    def _start(
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str] | None,
        stdout: Any,
        stderr: Any,
        *,
        require_tree_ownership: bool = False,
    ) -> _ManagedProcess:
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": dict(env) if env is not None else None,
            "shell": False,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout,
            "stderr": stderr,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            if require_tree_ownership:
                kwargs["creationflags"] |= 0x00000004  # CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = True
        frame = _ProcessStartLifetime()
        frame.spawn_attempted = True  # BEFORE SAME original Popen, not after constructor return.
        try:
            process = subprocess.Popen(list(argv), **kwargs)
            frame.spawn_returned = True
            frame.process = process
            if process is None:
                raise ValueError("original process factory returned unusable receipt")
        except BaseException:
            frame.cleanup_uncertain = True
            raise ProcessCleanupError("process allocation unproved", source=frame) from None
        if os.name != "nt":
            frame.managed = _ManagedProcess(process, "process_group", None, startup=frame)
            return frame.managed
        job = None
        try:
            process_handle = int(process._handle)
            frame.job_attempted = True  # BEFORE SAME original Job factory.
            job = _WindowsJob(process_handle)
            frame.job, frame.job_returned = job, True
            if job is None:
                raise ValueError("original Job factory returned unusable receipt")
            if require_tree_ownership:
                frame.resume_attempted = True
                _resume_suspended_primary_thread(process.pid)
                frame.resume_returned = True
            frame.managed = _ManagedProcess(process, "job_object", job, startup=frame)
            return frame.managed
        except ProcessCleanupError as exc:
            # Actual _WindowsJob can fail before its constructor publishes job.
            # Preserve exact source/handle and SAME original process/returned
            # pipes. No resume/fallback/new Job/second close. Process cleanup
            # remains unresolved; this is NOT a killed/drained process receipt.
            source = exc.source if exc.source is not None else job
            frame.cleanup_uncertain = True
            if source is not None:
                frame.original_sources[id(source)] = source
            if frame.job_returned:
                frame.original_sources[id(job)] = job
            # An ALREADY returned Job is independent of resume/snapshot/thread
            # error source. Never replace it with that source or lose either.
            managed = _ManagedProcess(
                process, "unavailable", job if frame.job_returned else source, startup=frame
            )
            frame.managed = managed
            raise ProcessCleanupError(
                "process startup cleanup unproved", managed=managed, source=source
            ) from None
        except BaseException as exc:
            if frame.resume_attempted and getattr(exc, "source", None) is not None:
                frame.original_sources[id(exc.source)] = exc.source
            known_job_refusal = (
                isinstance(exc, ProcessSupervisionError)
                and exc.source is not None
                and not getattr(exc.source, "cleanup_uncertain", True)
                and getattr(exc.source, "_create_returned", False)
                and getattr(exc.source, "_handle", None) is None
            )
            if known_job_refusal:
                frame.original_sources[id(exc.source)] = exc.source
            elif frame.job_attempted and (not frame.job_returned or job is None):
                # External Job factory error/unusable return is opaque allocation,
                # NOT known no-Job/refusal/fallback/strict startup drain authority.
                frame.cleanup_uncertain = True
                managed = _ManagedProcess(process, "unavailable", None, startup=frame)
                frame.managed = managed
                raise ProcessCleanupError(
                    "process startup cleanup unproved", managed=managed, source=frame
                ) from None
            if require_tree_ownership:
                # The initial thread is suspended until Job attachment succeeds.
                # Resume failure also retains the Job; never fall back or leave
                # a suspended/unowned process or pipe handle behind.
                try:
                    if job is not None:
                        job.terminate_checked()
                        job.wait_empty()
                        job.close_checked()
                    try:
                        process.kill()
                    except OSError:
                        pass  # Checked Job termination may have stopped parent.
                    process.wait()
                    files = [
                        _ProcessFileLifetime(kind=name, resource=stream)
                        for name, stream in (
                            ("startup_stdout", process.stdout),
                            ("startup_stderr", process.stderr),
                        )
                        if stream is not None
                    ]
                    for original in files:
                        frame.original_sources[id(original)] = original  # Both BEFORE either original close.
                    for original in files:
                        _close_original_process_file(original)
                except BaseException as cleanup:
                    frame.cleanup_uncertain = True
                    source = getattr(cleanup, "source", None) or job or frame
                    frame.original_sources[id(source)] = source
                    managed = _ManagedProcess(
                        process, "job_object" if job is not None else "unavailable", job, startup=frame
                    )
                    frame.managed = managed
                    raise ProcessCleanupError(
                        "strict startup cleanup unproved", managed=managed, source=source
                    ) from None
                raise ProcessSupervisionError("strict process ownership unavailable", source=frame) from None
            frame.managed = _ManagedProcess(process, "taskkill_fallback", None, startup=frame)
            return frame.managed

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        run_id: str,
        timeout_seconds: float = 30,
        env: Mapping[str, str] | None = None,
        output_limit: int = 64 * 1024,
    ) -> ProcessResult:
        self._assert_admission()
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("argv must contain non-empty NUL-free strings")
        if timeout_seconds <= 0 or output_limit < 1:
            raise ValueError("process limits must be positive")
        root = cwd.resolve(strict=True)
        stdout_frame, stderr_frame = _ProcessFileLifetime("text_stdout"), _ProcessFileLifetime("text_stderr")
        try:
            with self._original_spool(stdout_frame) as stdout, self._original_spool(stderr_frame) as stderr:
                return await self._run_text_original(
                    argv,
                    root,
                    env,
                    run_id,
                    timeout_seconds,
                    output_limit,
                    stdout,
                    stderr,
                    stdout_frame,
                    stderr_frame,
                )
        finally:
            # _run_text_original records original managed/Job close return in
            # this call's same frames. Unregister/drainer ONLY after both spools.
            if (
                stdout_frame.managed is not None
                and stdout_frame.job_close_returned
                and stdout_frame.close_returned
                and stderr_frame.close_returned
            ):
                self._retire_original(stdout_frame.operation)

    async def _run_text_original(
        self,
        argv,
        root,
        env,
        run_id,
        timeout_seconds,
        output_limit,
        stdout,
        stderr,
        original_frame: _ProcessFileLifetime,
        stderr_frame: _ProcessFileLifetime,
    ) -> ProcessResult:
        # SAME original wait worker/text-spool contract, not a second executor.
        # Caller stores these returned lifetime facts even on timeout/cancel.
        original_frame.producer_settled = stderr_frame.producer_settled = False
        try:
            managed = self._start(argv, root, env, stdout, stderr)
        except ProcessCleanupError as exc:
            original_frame.managed = stderr_frame.managed = exc.managed
            self._retain_startup_failure(run_id, exc)
            raise
        original_frame.managed = stderr_frame.managed = managed
        owned = self._begin_original(run_id, managed, "text", files=(original_frame, stderr_frame))
        wait_frame = self._spawn_original_thread(owned, "text_wait", managed.process.wait)
        waiter = wait_frame.task
        parent_wait_known = parent_wait_failed = False

        def settle_original_wait() -> None:
            nonlocal parent_wait_known, parent_wait_failed
            if parent_wait_known:
                return  # Same already-observed Task receipt, no repeated wait/API.
            if not parent_wait_failed:
                # Completion/failed wait/unusable reply isn't a stopped producer.
                # Inspect BEFORE spool seek/read as well as cleanup on unwind.
                try:
                    self._inspect_original_worker(owned, wait_frame)
                    if not waiter.done() or waiter.cancelled() or waiter.exception() is not None:
                        raise ValueError("original wait not acknowledged")
                    code = waiter.result()
                    if (
                        type(code) is not int
                        or type(managed.process.returncode) is not int
                        or code != managed.process.returncode
                    ):
                        raise ValueError("original wait receipt unusable")
                except BaseException:
                    parent_wait_failed = True  # Sticky SAME observation, no post-fault reinspection/retry.
                    self._mark_cleanup_failed(waiter)
                    self._mark_cleanup_failed(managed)
                else:
                    parent_wait_known = True
            if parent_wait_failed:
                raise ProcessCleanupError(
                    "process wait cleanup unproved", managed=managed, source=waiter
                ) from None

        try:
            try:
                await asyncio.wait_for(asyncio.shield(waiter), timeout=timeout_seconds)
            except TimeoutError as exc:
                self._terminate_original(owned)
                await await_durable(waiter)
                raise TimeoutError(f"command timed out after {timeout_seconds:g}s") from exc
            except asyncio.CancelledError:
                self._terminate_original(owned)
                await await_durable(waiter)
                raise
            settle_original_wait()
            stdout.seek(0)
            stderr.seek(0)
            return ProcessResult(
                tuple(argv),
                managed.process.returncode,
                stdout.read(output_limit).decode("utf-8", errors="replace"),
                stderr.read(output_limit).decode("utf-8", errors="replace"),
                managed.supervision,
            )
        finally:
            settle_original_wait()
            try:
                owned.job_close_attempted = True
                managed.close()
            except BaseException as exc:
                self._mark_cleanup_failed(getattr(exc, "source", None) or managed)
                raise
            owned.job_close_returned = owned.drained = True
            # Inherited TEXT parent-wait/managed-close contract ONLY, NOT tree/
            # descendant accounting proof. Failed Job close keeps BOTH spools.
            original_frame.producer_settled = stderr_frame.producer_settled = True
            original_frame.job_close_returned = True

    async def run_binary(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        run_id: str,
        timeout_seconds: float = 30,
        env: Mapping[str, str] | None = None,
        output_limit: int = 1024 * 1024,
        require_tree_ownership: bool = False,
    ) -> BinaryProcessResult:
        """Run the same owned process tree with strict per-stream byte caps.

        Never decode/replace bytes or silently truncate a machine protocol.
        Readers stop at limit+1 and terminate the tree on overflow. The deadline
        includes pipe drain (a descendant can outlive its parent's exit), and
        worker threads are joined before unregister, even on repeated cancel.
        The existing text run/spool contract is intentionally unchanged.
        Windows taskkill fallback is still best effort if the parent has exited;
        callers must not claim Job Object-equivalent descendant guarantees.
        Strict callers start Windows children suspended and reject Job/resume
        failure before execution rather than silently using that fallback.
        Strict Windows release additionally terminates remaining Job members and
        observes zero ActiveProcesses before close/unregister, even after EOF.
        Cleanup is drained after the command deadline and may exceed it; failed
        cleanup keeps registration/Job and quarantines future admission.
        """
        self._assert_admission()
        if (
            isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
        ):
            raise ValueError("argv must contain non-empty NUL-free strings")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or type(output_limit) is not int
            or not 1 <= output_limit <= 16 * 1024 * 1024
            or type(require_tree_ownership) is not bool
        ):
            raise ValueError("invalid binary process limits")
        deadline = monotonic() + timeout_seconds
        root = cwd.resolve(strict=True)
        if monotonic() >= deadline:
            raise TimeoutError("binary command deadline exceeded")
        files = [
            _ProcessFileLifetime(kind, producer_settled=False) for kind in ("binary_stdout", "binary_stderr")
        ]
        try:
            if require_tree_ownership:
                managed = self._start(
                    argv,
                    root,
                    env,
                    subprocess.PIPE,
                    subprocess.PIPE,
                    require_tree_ownership=True,
                )
            else:
                managed = self._start(argv, root, env, subprocess.PIPE, subprocess.PIPE)
        except ProcessCleanupError as exc:
            self._retain_startup_failure(run_id, exc)
            raise
        for original in files:
            original.managed = managed
        owned = self._begin_original(run_id, managed, "binary", files=files, strict=require_tree_ownership)
        try:
            files[0].resource, files[1].resource = managed.process.stdout, managed.process.stderr
            for original in files:
                if original.resource is None or not callable(getattr(original.resource, "read", None)):
                    self._fail_original(owned, original, "process pipe identity unproved")
        except ProcessCleanupError:
            raise
        except BaseException:
            self._fail_original(owned, owned, "process pipe identity unproved")

        def read_bounded(original: _ProcessFileLifetime) -> tuple[bytes, bool]:
            result = bytearray()
            try:
                while len(result) <= output_limit:
                    requested = min(64 * 1024, output_limit + 1 - len(result))
                    original.read_attempts += 1  # BEFORE SAME original bounded read.
                    chunk = original.resource.read(requested)
                    original.read_returns += 1
                    if type(chunk) is not bytes or len(chunk) > requested:
                        raise ValueError("original binary read receipt unusable")
                    if chunk == b"":
                        original.eof_seen = True  # ONLY typed empty bytes, never None/falsy metadata.
                        return bytes(result), False
                    original.read_bytes += len(chunk)
                    result.extend(chunk)
                    if len(result) > output_limit:
                        self._terminate_original(owned)
                        return bytes(result), True
                raise ValueError("original binary read state unavailable")
            except ProcessCleanupError:
                original.read_failed = True
                raise
            except BaseException:
                original.read_failed = True
                raise ProcessReadError("binary process read unavailable", source=original) from None

        workers = [self._spawn_original_thread(owned, "binary_wait", managed.process.wait)]
        for original in files:
            workers.append(
                self._spawn_original_thread(owned, original.kind, read_bounded, original, source=original)
            )
        combined = self._gather_original(owned, (frame.task for frame in workers))

        def validate_original_workers():
            code = self._parent_wait_original(owned, workers[0])
            values = []
            for original, frame in zip(files, workers[1:], strict=True):
                error = self._inspect_original_worker(owned, frame)
                if error is not None:
                    if not isinstance(error, ProcessReadError) or error.source is not original:
                        self._fail_original(
                            owned, getattr(error, "source", None) or frame, "process reader cleanup unproved"
                        )
                    owned.read_failure = owned.read_failure or error
                    values.append((b"", False))  # NOT a response; failure must propagate after cleanup.
                    continue
                value = frame.result
                if (
                    type(value) is not tuple
                    or len(value) != 2
                    or type(value[0]) is not bytes
                    or type(value[1]) is not bool
                    or len(value[0]) > output_limit + 1
                    or value[1] != (len(value[0]) > output_limit)
                    or not value[1]
                    and not original.eof_seen
                ):
                    self._fail_original(owned, frame, "process reader receipt unproved")
                values.append(value)
            return code, values[0], values[1]

        async def drain():
            # Remaining descendants can close pipes and outlive parent wait.
            # Strict Job membership, not pipe EOF, is the Windows release gate.
            try:
                self._terminate_original(owned)
                joined = self._gather_original(
                    owned, (combined, *(frame.task for frame in workers)), return_exceptions=True
                )
                await await_durable(joined)
                validate_original_workers()  # Gather(return_exceptions=True) alone proves NOTHING.
                job = getattr(managed, "job", None)
                if require_tree_ownership and job is not None:
                    accounting = self._spawn_original_thread(owned, "job_empty", job.wait_empty, source=job)
                    try:
                        await await_durable(accounting.task)
                    finally:
                        error = self._inspect_original_worker(owned, accounting)
                        if error is not None or accounting.result is not None:
                            self._fail_original(
                                owned,
                                getattr(error, "source", None) or accounting,
                                "process job accounting unproved",
                            )
                elif require_tree_ownership and os.name == "nt":
                    self._fail_original(owned, owned, "process strict job identity unproved")
                owned.drained = True  # Strict accounting or explicitly weaker compatibility contract.
                for original in files:
                    original.producer_settled = True
            except BaseException as exc:
                owned.cleanup_uncertain = True
                self._mark_cleanup_failed(owned)
                self._mark_cleanup_failed(getattr(exc, "source", None) or managed)
                raise

        async def join_original_drain():
            if owned.drain_worker is None:
                owned.drain_worker = self._spawn_original_async(owned, "binary_drain", drain)
            await await_durable(owned.drain_worker.task)  # Original Task, no opaque ensure_future allocation.

        try:
            try:
                await asyncio.wait_for(asyncio.shield(combined), timeout=max(0, deadline - monotonic()))
            except TimeoutError as exc:
                await join_original_drain()
                raise TimeoutError("binary command deadline exceeded") from exc
            except BaseException:
                await join_original_drain()
                raise
            if require_tree_ownership:
                await join_original_drain()
            else:
                validate_original_workers()
                owned.drained = True  # Compatibility typed EOF/parent wait ONLY.
                for original in files:
                    original.producer_settled = True
            exit_code, out, err = validate_original_workers()
            if owned.read_failure is not None:
                raise owned.read_failure
            if out[1] or err[1]:
                raise ProcessOutputLimitError("binary command output limit exceeded")
            return BinaryProcessResult(tuple(argv), exit_code, out[0], err[0], managed.supervision)
        finally:
            if owned.drained:
                failures = []
                # Independently close each SAME original pipe once even when its
                # sibling close fails. Retain all failures; never signal drainer.
                for original in files:
                    try:
                        _close_original_process_file(original)
                    except BaseException as exc:
                        owned.cleanup_uncertain = True
                        self._mark_cleanup_failed(original)
                        failures.append(exc)
                try:
                    owned.job_close_attempted = True
                    if require_tree_ownership and isinstance(managed, _ManagedProcess):
                        managed.close(strict=True)
                    else:
                        managed.close()
                    owned.job_close_returned = True
                except BaseException as exc:
                    owned.cleanup_uncertain = True
                    self._mark_cleanup_failed(getattr(exc, "source", None) or managed)
                    failures.append(
                        ProcessCleanupError(
                            "process job close unproved",
                            managed=managed,
                            source=getattr(exc, "source", None) or managed,
                        )
                    )
                if failures:
                    self._mark_cleanup_failed(owned)
                    raise failures[0] from None
                self._retire_original(owned)
            # Failed strict cleanup intentionally retains Job/registration.
            # Closing the supervisor must not falsely release a service owner.

    async def cancel_run(self, run_id: str) -> None:
        if self.cleanup_failed:
            raise ProcessCleanupError("process cleanup quarantine", source=self)  # No uncertain-source retry.
        with self._lock:
            managed = list(self._active.get(run_id, ()))
            operations = [self._operations.get(id(item)) for item in managed]
        for item, owned in zip(managed, operations, strict=True):
            if owned is None:
                self._mark_cleanup_failed(item)
                raise ProcessCleanupError("process original operation unavailable", managed=item, source=item)
            self._terminate_original(owned)
        for owned in operations:
            wait_frame = next(
                (frame for frame in owned.workers if frame.kind in {"text_wait", "binary_wait"}), None
            )
            if wait_frame is None:
                self._fail_original(owned, owned, "process original waiter unavailable")
            try:
                await await_durable(wait_frame.task)  # SAME original wait, no second thread/process.wait.
            finally:
                self._parent_wait_original(owned, wait_frame)
        # Parent-wait join ONLY. Original run/drain owns pipes/Job/unregister;
        # close separately joins their SAME original drainer events.

    async def close(self) -> None:
        self._closing = True
        if self.cleanup_failed:
            raise ProcessCleanupError("process cleanup quarantine", source=self)
        try:
            if self._close_source is None:
                self._close_source = _ProcessOperationLifetime("supervisor_close", None, "close")
                worker = self._spawn_original_async(
                    self._close_source, "supervisor_close", self._close_original
                )
                self._close_task = worker.task
            if self._close_task is None:
                self._fail_original(
                    self._close_source, self._close_source, "process close allocation unproved"
                )
            await await_durable(self._close_task)  # SAME owned close Task, caller cancellation joined.
            worker = self._close_source.workers[0]
            error = self._inspect_original_worker(self._close_source, worker)
            if error is not None or worker.result is not None or not self._close_source.drained:
                self._fail_original(self._close_source, worker, "process close receipt unproved")
        except BaseException as exc:
            owned = self._close_source
            worker = owned.workers[0] if owned is not None and owned.workers else None
            task = self._close_task
            if (
                isinstance(exc, asyncio.CancelledError)
                and owned is not None
                and owned.drained
                and not owned.cleanup_uncertain
                and worker is not None
                and worker.finished
                and worker.returned
                and worker.result is None
                and isinstance(task, asyncio.Task)
                and task.done()
                and not task.cancelled()
                and task.exception() is None
                and task.result() is None
            ):
                raise  # Caller cancelled AFTER the SAME successful original close, not a cleanup fault.
            if owned is not None:
                self._retain_original_close_failure(owned, exc)
            else:
                self._mark_cleanup_failed(self)
            raise ProcessCleanupError("process close cleanup unproved", source=owned or self) from None

    async def _close_original(self) -> None:
        owned = self._close_source
        try:
            if owned.cleanup_uncertain or self.cleanup_failed:
                self._fail_original(owned, owned, "process close cleanup quarantine")
            with self._lock:
                run_ids = list(self._active)
                drainers = list(self._drainers.values())
                owned.close_resources = (
                    *self._operations.values(),
                    *drainers,
                    *(item for group in self._active.values() for item in group),
                )
            cancellations = [
                self._spawn_original_async(owned, "cancel_run", lambda run_id=run_id: self.cancel_run(run_id))
                for run_id in run_ids
            ]
            if cancellations:
                await await_durable(
                    self._gather_original(
                        owned, (frame.task for frame in cancellations), return_exceptions=True
                    )
                )
                for frame in cancellations:
                    error = self._inspect_original_worker(owned, frame)
                    if error is not None or frame.result is not None:
                        self._fail_original(
                            owned, getattr(error, "source", None) or frame, "process cancel join unproved"
                        )
            # Parent wait is not pipe/Job cleanup. Join ORIGINAL unregister gates,
            # inspect actual callback returns; gather(return_exceptions) is NOT proof.
            if drainers:
                workers = [
                    self._spawn_original_thread(owned, "drainer_wait", event.wait, 10, source=event)
                    for event in drainers
                ]
                await await_durable(
                    self._gather_original(owned, (frame.task for frame in workers), return_exceptions=True)
                )
                for frame in workers:
                    error = self._inspect_original_worker(owned, frame)
                    if error is not None or frame.result is not True:
                        self._fail_original(
                            owned, getattr(error, "source", None) or frame, "process close drain unproved"
                        )
            if self.cleanup_failed or self.active_count or self._operations:
                self._fail_original(owned, owned, "process close drain unproved")
            owned.drained = True  # Registry/drainer lifetime ONLY, no physical/native proof.
        except BaseException as exc:
            self._retain_original_close_failure(owned, exc)
            raise ProcessCleanupError("process close cleanup unproved", source=owned) from None
