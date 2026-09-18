"""Windows sandbox-friendly test workspace fixture."""

from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree
from uuid import uuid4


@contextmanager
def workspace():
    root = Path.cwd() / ".test-tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        rmtree(root)
