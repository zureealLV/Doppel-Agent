"""Cancellable subprocess trees with Windows Job Object supervision."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    supervision: str


class _WindowsJob:
    """Small ctypes wrapper kept private so non-Windows imports stay harmless."""

    def __init__(self, process_handle: int):
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
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(handle, wintypes.HANDLE(process_handle)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "AssignProcessToJobObject failed")
        self._kernel32 = kernel32
        self._handle = handle

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


@dataclass
class _ManagedProcess:
    process: subprocess.Popen[Any]
    supervision: str
    job: _WindowsJob | None

    def terminate_tree(self) -> None:
        if self.process.poll() is not None:
            return
        if self.job is not None:
            self.job.terminate()
            return
        if os.name == "nt":
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

    def close(self) -> None:
        if self.job is not None:
            self.job.close()


class ProcessSupervisor:
    """Start shell-free commands and guarantee cancellation reaches descendants."""

    def __init__(self) -> None:
        self._active: dict[str, list[_ManagedProcess]] = {}
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._active.values())

    def _register(self, run_id: str, managed: _ManagedProcess) -> None:
        with self._lock:
            self._active.setdefault(run_id, []).append(managed)

    def _unregister(self, run_id: str, managed: _ManagedProcess) -> None:
        with self._lock:
            items = self._active.get(run_id, [])
            if managed in items:
                items.remove(managed)
            if not items:
                self._active.pop(run_id, None)

    @staticmethod
    def _start(
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str] | None,
        stdout: Any,
        stderr: Any,
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
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(list(argv), **kwargs)
        if os.name != "nt":
            return _ManagedProcess(process, "process_group", None)
        try:
            process_handle = int(process._handle)
            return _ManagedProcess(process, "job_object", _WindowsJob(process_handle))
        except (AttributeError, OSError, TypeError, ValueError):
            return _ManagedProcess(process, "taskkill_fallback", None)

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
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("argv must contain non-empty NUL-free strings")
        if timeout_seconds <= 0 or output_limit < 1:
            raise ValueError("process limits must be positive")
        root = cwd.resolve(strict=True)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            managed = self._start(argv, root, env, stdout, stderr)
            self._register(run_id, managed)
            try:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(managed.process.wait), timeout=timeout_seconds
                    )
                except TimeoutError as exc:
                    managed.terminate_tree()
                    await asyncio.shield(asyncio.to_thread(managed.process.wait))
                    raise TimeoutError(
                        f"command timed out after {timeout_seconds:g}s"
                    ) from exc
                except asyncio.CancelledError:
                    managed.terminate_tree()
                    await asyncio.shield(asyncio.to_thread(managed.process.wait))
                    raise
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
                self._unregister(run_id, managed)
                managed.close()

    async def cancel_run(self, run_id: str) -> None:
        with self._lock:
            managed = list(self._active.get(run_id, ()))
        for item in managed:
            item.terminate_tree()
        if managed:
            await asyncio.gather(
                *(asyncio.to_thread(item.process.wait) for item in managed),
                return_exceptions=True,
            )

    async def close(self) -> None:
        with self._lock:
            run_ids = list(self._active)
        await asyncio.gather(*(self.cancel_run(run_id) for run_id in run_ids))
