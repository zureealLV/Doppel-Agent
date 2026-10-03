"""Finite child; no workspace writes, shell, network or model calls."""
import json
import os
from pathlib import Path
import sys
import time


if __name__ == "__main__":
    ready = Path(sys.argv[1])
    (ready / "child.json").write_text(json.dumps({"pid": os.getpid(), "parent_pid": os.getppid()}), encoding="utf-8")
    time.sleep(45)
