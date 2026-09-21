"""Reviewable, stale-safe and rollback-capable workspace patches."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .service import WorkspaceService


MISSING_HASH = "missing"


class PatchConflictError(RuntimeError):
    """Raised when a reviewed patch no longer matches the workspace base."""


@dataclass(frozen=True)
class PatchChange:
    path: str
    content: str
    base_hash: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "content": self.content, "base_hash": self.base_hash}


@dataclass(frozen=True)
class PatchProposal:
    patch_id: str
    changes: tuple[PatchChange, ...]
    unified_diff: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "changes": [change.as_dict() for change in self.changes],
            "unified_diff": self.unified_diff,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PatchProposal":
        changes = tuple(PatchChange(**item) for item in value["changes"])
        return cls(str(value["patch_id"]), changes, str(value["unified_diff"]))


@dataclass(frozen=True)
class PatchApplyResult:
    patch_id: str
    changed_paths: tuple[str, ...]


class PatchService:
    def __init__(
        self,
        workspace: Path,
        *,
        max_files: int = 32,
        max_total_bytes: int = 512 * 1024,
    ) -> None:
        self.workspace = WorkspaceService(workspace)
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes

    @staticmethod
    def _digest(payload: bytes) -> str:
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _proposal_id(
        changes: tuple[PatchChange, ...] | list[PatchChange], unified_diff: str
    ) -> str:
        identity = json.dumps(
            {"changes": [change.as_dict() for change in changes], "unified_diff": unified_diff},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(identity).hexdigest()[:32]

    def _snapshot(self, path: str) -> tuple[Path, bytes | None, str]:
        target = self.workspace.resolve(path, must_exist=False)
        if not target.exists():
            return target, None, MISSING_HASH
        if not target.is_file():
            raise ValueError(f"patch target is not a file: {path}")
        payload = target.read_bytes()
        return target, payload, self._digest(payload)

    def prepare(self, changes: list[dict[str, Any]]) -> PatchProposal:
        if not isinstance(changes, list) or not changes or len(changes) > self.max_files:
            raise ValueError(f"patch must contain 1 to {self.max_files} files")
        normalized: list[PatchChange] = []
        diff_parts: list[str] = []
        seen: set[str] = set()
        total_bytes = 0
        for raw in changes:
            if not isinstance(raw, dict) or set(raw) != {"path", "content"}:
                raise ValueError("each patch change requires only path and content")
            path, content = raw["path"], raw["content"]
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("patch path and content must be strings")
            virtual = self.workspace.normalize_virtual(path).lstrip("/")
            if not virtual or virtual in seen:
                raise ValueError("patch paths must be non-empty and unique")
            seen.add(virtual)
            _, before_bytes, base_hash = self._snapshot(virtual)
            before = "" if before_bytes is None else before_bytes.decode("utf-8")
            if before == content:
                raise ValueError(f"patch does not change file: {virtual}")
            payload = content.encode("utf-8")
            total_bytes += len(payload)
            if total_bytes > self.max_total_bytes:
                raise ValueError("patch exceeds total write limit")
            normalized.append(PatchChange(virtual, content, base_hash))
            diff_parts.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{virtual}" if before_bytes is not None else "/dev/null",
                    tofile=f"b/{virtual}",
                )
            )
        unified_diff = "".join(diff_parts)
        patch_id = self._proposal_id(normalized, unified_diff)
        return PatchProposal(patch_id, tuple(normalized), unified_diff)

    def apply(self, proposal: PatchProposal) -> PatchApplyResult:
        if proposal.patch_id != self._proposal_id(proposal.changes, proposal.unified_diff):
            raise ValueError("patch proposal integrity check failed")
        snapshots: list[tuple[PatchChange, Path, bytes | None]] = []
        for change in proposal.changes:
            target, before, current_hash = self._snapshot(change.path)
            if current_hash != change.base_hash:
                raise PatchConflictError(f"stale patch base for {change.path}")
            snapshots.append((change, target, before))

        temporary: list[tuple[Path, Path]] = []
        replaced: list[tuple[Path, bytes | None]] = []
        try:
            for change, target, _ in snapshots:
                temp = target.with_name(f".{target.name}.doppel-patch-{uuid4().hex}.tmp")
                temp.write_bytes(change.content.encode("utf-8"))
                temporary.append((target, temp))
            for (_change, target, before), (_, temp) in zip(snapshots, temporary, strict=True):
                os.replace(temp, target)
                replaced.append((target, before))
        except Exception:
            for target, before in reversed(replaced):
                if before is None:
                    target.unlink(missing_ok=True)
                else:
                    rollback = target.with_name(f".{target.name}.doppel-rollback-{uuid4().hex}.tmp")
                    rollback.write_bytes(before)
                    os.replace(rollback, target)
            raise
        finally:
            for _, temp in temporary:
                temp.unlink(missing_ok=True)
        return PatchApplyResult(proposal.patch_id, tuple(change.path for change in proposal.changes))
