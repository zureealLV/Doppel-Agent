"""Owned OS process identity handles; PID text alone cannot prove cleanup."""

import asyncio
import os


class ProcessIdentity:
    def __init__(self, pid):
        if type(pid) is not int or pid <= 0:
            raise ValueError("process identity requires a positive PID")
        self.pid, self.closed = pid, False
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "could not bind owned process identity")
            self.backend = "windows-process-handle"
        elif hasattr(os, "pidfd_open"):
            self.handle = os.pidfd_open(pid)
            self.backend = "linux-pidfd"
        else:
            raise RuntimeError("OS identity handle verification unavailable on this platform")

    def exited(self):
        if self.closed:
            raise RuntimeError("cannot inspect a closed process identity")
        if self.backend == "windows-process-handle":
            state = self.kernel.WaitForSingleObject(self.handle, 0)
            if state not in {0, 258}:
                raise OSError("process identity wait failed")
            return state == 0
        import select
        return bool(select.select([self.handle], [], [], 0)[0])

    async def wait_exited(self, timeout=10):
        async with asyncio.timeout(timeout):
            while not self.exited():
                await asyncio.sleep(0.025)
        return {"pid": self.pid, "identity_backend": self.backend, "bound_before_shutdown": True, "exited": True}

    def close(self):
        if not self.closed:
            if self.backend == "windows-process-handle":
                self.kernel.CloseHandle(self.handle)
            else:
                os.close(self.handle)
            self.closed = True
