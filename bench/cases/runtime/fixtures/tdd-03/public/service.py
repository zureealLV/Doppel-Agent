"""Reviewed single-file patches inside an owned root."""

from hashlib import sha256
from pathlib import Path


class FilePatch:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.prepared = {}

    def _target(self, relative):
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("path escapes root")
        return target

    @staticmethod
    def _hash(target):
        return sha256(target.read_bytes()).hexdigest() if target.is_file() else None

    def prepare(self, relative, content):
        target = self._target(relative)
        base_hash = self._hash(target)
        if base_hash is None:
            raise ValueError("missing base file")
        self.prepared[relative] = base_hash
        return {"path": relative, "content": content, "base_hash": base_hash}

    def apply(self, proposal):
        target = self._target(proposal["path"])
        if self.prepared.get(proposal["path"]) != proposal["base_hash"]:
            raise ValueError("stale base")
        target.write_text(proposal["content"], encoding="utf-8")
        return self._hash(target)
