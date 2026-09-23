"""Background worker lifecycle for a cancellable task."""

import subprocess
import sys


_cancelled: set[int] = set()


def start_worker() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def cancel_worker(process: subprocess.Popen[bytes]) -> None:
    _cancelled.add(process.pid)
