"""Owned finite parent; the trusted harness supplies an external ready directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


if __name__ == "__main__":
    ready = Path(sys.argv[1])
    child = subprocess.Popen([sys.executable, "-B", "child.py", str(ready)])
    (ready / "parent.json").write_text(json.dumps({"pid": os.getpid(), "spawned_child_pid": child.pid}), encoding="utf-8")
    time.sleep(45)
