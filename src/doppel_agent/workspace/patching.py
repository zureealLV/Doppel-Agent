"""Reviewable, stale-safe and rollback-capable workspace patches."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from .patch_receipts import MAX_PREIMAGE_BYTES, PatchFileReceipt, PatchReceipt, valid_hash
from .service import WorkspaceService


MISSING_HASH = "missing"


class PatchConflictError(RuntimeError):
    """Raised when a reviewed patch no longer matches the workspace base."""


@dataclass(frozen=True)
class PatchChange:
    path: str
    content: str | None
    base_hash: str
    base_mode: int | None = None
    target_mode: int | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"path": self.path, "content": self.content, "base_hash": self.base_hash}
        if self.base_mode is not None:
            result["base_mode"] = self.base_mode
        if self.target_mode is not None:
            result["target_mode"] = self.target_mode
        return result


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
    receipt: PatchReceipt | None = None


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
        if type(max_files) is not int or not 1 <= max_files <= 32 or type(max_total_bytes) is not int or not 1 <= max_total_bytes <= MAX_PREIMAGE_BYTES:
            raise ValueError("invalid patch limits")

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

    def _path(self, path: str) -> Path:
        from ..context.manifest import ContextManifestError, relative_path

        try:
            relative_path(path)
        except ContextManifestError:
            raise ValueError("patch_path_blocked") from None
        if "\x7f" in path:
            raise ValueError("patch_path_blocked")
        target = self.workspace.root
        parts = path.split("/")
        for number, part in enumerate(parts):
            # Refuse component aliases even on case-sensitive development hosts.
            with os.scandir(target) as entries:
                for number_of_entries, entry in enumerate(entries, 1):
                    if number_of_entries > 20_000:
                        raise ValueError("patch_directory_entry_limit")
                    if entry.name.casefold() == part.casefold() and entry.name != part:
                        raise ValueError("patch_path_case_alias")
            target /= part
            try:
                info = target.lstat()
            except FileNotFoundError:
                if number == len(parts) - 1:
                    return target
                raise ValueError("patch_parent_unavailable") from None
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("patch_link_blocked")
            if number < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise ValueError("patch_parent_unavailable")
        if self.workspace.resolve(path, must_exist=True) != target:
            raise ValueError("patch_path_changed")
        return target

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))

    def _snapshot(self, path: str) -> tuple[Path, bytes | None, str, int | None, tuple[int, ...] | None]:
        from ..context.manifest import ManifestReader

        # Existing dynamic deny policy, before any working-file content read.
        ManifestReader(self.workspace.root)._check_ignores(path)
        target = self._path(path)
        try:
            info = target.lstat()
        except FileNotFoundError:
            return target, None, MISSING_HASH, None, None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & ~0o777:
            raise ValueError("patch_nonregular_or_hardlink")
        if info.st_size > self.max_total_bytes:
            raise ValueError("patch_base_too_large")
        payload = ManifestReader(self.workspace.root)._raw(path)
        if self._identity(info) != self._identity(self._path(path).lstat()):
            raise PatchConflictError("patch_file_changed_during_read")
        self._text(payload)
        return target, payload, self._digest(payload), stat.S_IMODE(info.st_mode), self._identity(info)

    @staticmethod
    def _text(payload: bytes | None) -> str | None:
        if payload is None:
            return None
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ValueError("patch_base_non_utf8") from None
        if "\x00" in text:
            raise ValueError("patch_binary_blocked")
        return text

    @staticmethod
    def _diff(path: str, before: bytes | None, content: str | None) -> str:
        left = PatchService._text(before) or ""
        right = content or ""
        # LF is the only line delimiter; retain CR bytes and missing final LF.

        def lines(value):
            parts = value.split("\n")
            return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])

        left_lines, right_lines = lines(left), lines(right)
        if len(left_lines) * len(right_lines) > 1_000_000:
            raise ValueError("patch_diff_complexity_limit")
        chunks, size = [], 0
        for chunk in difflib.unified_diff(left_lines, right_lines,
                fromfile=f"a/{path}" if before is not None else "/dev/null",
                tofile=f"b/{path}" if content is not None else "/dev/null"):
            if chunk and chunk[0] in "+- " and not chunk.endswith("\n"):
                chunk += "\n\\ No newline at end of file\n"
            size += len(chunk.encode("utf-8"))
            if size > 2 * MAX_PREIMAGE_BYTES:
                raise ValueError("patch_diff_too_large")
            chunks.append(chunk)
        return "".join(chunks)

    def prepare(self, changes: list[dict[str, Any]]) -> PatchProposal:
        if not isinstance(changes, list) or not changes or len(changes) > self.max_files:
            raise ValueError(f"patch must contain 1 to {self.max_files} files")
        normalized: list[PatchChange] = []
        diff_parts: list[str] = []
        seen: set[str] = set()
        total_bytes, base_bytes, spellings = 0, 0, {}
        for raw in changes:
            if not isinstance(raw, dict) or set(raw) != {"path", "content"}:
                raise ValueError("each patch change requires only path and content")
            path, content = raw["path"], raw["content"]
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("patch path and content must be strings")
            virtual = self.workspace.normalize_virtual(path).lstrip("/")
            if not virtual or virtual.casefold() in seen:
                raise ValueError("patch paths must be non-empty and unique")
            seen.add(virtual.casefold())
            self._remember_path(virtual, spellings)
            _, before_bytes, base_hash, base_mode, _ = self._snapshot(virtual)
            if before_bytes is not None and self._text(before_bytes) == content:
                raise ValueError(f"patch does not change file: {virtual}")
            if "\x00" in content:
                raise ValueError("patch_binary_blocked")
            try:
                payload = content.encode("utf-8")
            except UnicodeError:
                raise ValueError("patch_content_non_utf8") from None
            total_bytes += len(payload)
            base_bytes += len(before_bytes or b"")
            if total_bytes > self.max_total_bytes or base_bytes > self.max_total_bytes:
                raise ValueError("patch exceeds total write limit")
            normalized.append(PatchChange(virtual, content, base_hash, base_mode))
            diff_parts.append(self._diff(virtual, before_bytes, content))
        unified_diff = "".join(diff_parts)
        patch_id = self._proposal_id(normalized, unified_diff)
        return PatchProposal(patch_id, tuple(normalized), unified_diff)

    @staticmethod
    def _remember_path(path: str, spellings: dict[str, str]) -> None:
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            spelling = "/".join(parts[:count])
            key = spelling.casefold()
            if key in spellings and spellings[key] != spelling:
                raise ValueError("patch_path_case_alias")
            spellings[key] = spelling

    def prepare_inverse(self, receipt: PatchReceipt) -> PatchProposal:
        receipt = PatchReceipt.from_dict(receipt.as_dict())
        if receipt.status != "applied":
            raise ValueError("patch_inverse_requires_applied_receipt")
        changes, diff_parts = [], []
        for file in receipt.files:
            _, current, current_hash, current_mode, _ = self._snapshot(file.path)
            if current_hash != file.after_hash or current_mode != file.after_mode:
                raise PatchConflictError("patch_inverse_user_change_conflict")
            # Original missing files become approved deletions, never empty writes.
            changes.append(PatchChange(file.path, file.before_content, current_hash, current_mode, file.base_mode))
            diff_parts.append(self._diff(file.path, current, file.before_content))
        diff = "".join(diff_parts)
        return PatchProposal(self._proposal_id(changes, diff), tuple(changes), diff)

    def _temporary(self, target: Path, payload: bytes, mode: int) -> Path:
        temp = target.with_name(f".{target.name}.doppel-patch-{uuid4().hex}.tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, mode)
            return temp
        except BaseException:
            self._remove_temporary(temp)
            raise

    @staticmethod
    def _remove_temporary(temp: Path) -> None:
        try:
            info = temp.lstat()
        except FileNotFoundError:
            return
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError("patch_temporary_identity_unavailable")
        if os.name == "nt" and not info.st_mode & stat.S_IWRITE:
            # Only our generated temp, never alter the readonly user target.
            os.chmod(temp, stat.S_IWRITE | stat.S_IREAD)
        temp.unlink()

    @staticmethod
    def _project_mode(path: str, mode: int) -> int:
        if os.name != "nt":
            return mode
        # CPython Windows lstat exposes readonly -> 0444/0666 and filename
        # execute suffixes, NOT POSIX permissions or the security descriptor.
        projected = 0o666 if mode & 0o200 else 0o444
        if Path(path).suffix.casefold() in {".exe", ".com", ".bat", ".cmd"}:
            projected |= 0o111
        return projected

    def apply(self, proposal: PatchProposal, *, record_intent: Callable[[PatchReceipt], None] | None = None) -> PatchApplyResult:
        if proposal.patch_id != self._proposal_id(proposal.changes, proposal.unified_diff):
            raise ValueError("patch proposal integrity check failed")
        if not 1 <= len(proposal.changes) <= self.max_files:
            raise ValueError("invalid patch file count")
        snapshots, files, spellings, bases, writes, differences = [], [], {}, 0, 0, []
        for change in proposal.changes:
            if not isinstance(change, PatchChange) or not valid_hash(change.base_hash):
                raise ValueError("invalid patch change")
            for permission in (change.base_mode, change.target_mode):
                if permission is not None and (type(permission) is not int or not 0 <= permission <= 0o777):
                    raise ValueError("invalid patch mode")
            if change.content is None and change.target_mode is not None:
                raise ValueError("invalid deleted patch mode")
            self._remember_path(change.path, spellings)
            if any(file.path.casefold() == change.path.casefold() for file in files):
                raise ValueError("duplicate patch path")
            target, before, current_hash, mode, identity = self._snapshot(change.path)
            if current_hash != change.base_hash or change.base_mode is not None and mode != change.base_mode:
                raise PatchConflictError(f"stale patch base for {change.path}")
            if change.content is not None and (not isinstance(change.content, str) or "\x00" in change.content):
                raise ValueError("invalid patch content")
            payload = None if change.content is None else change.content.encode("utf-8")
            if payload == before:
                raise ValueError("patch does not change file")
            bases += len(before or b"")
            writes += len(payload or b"")
            if max(bases, writes) > self.max_total_bytes:
                raise ValueError("patch exceeds total write limit")
            after_mode = None if payload is None else (
                change.target_mode if change.target_mode is not None else mode if mode is not None else 0o600
            )
            if after_mode is not None:
                after_mode = self._project_mode(change.path, after_mode)
            files.append(PatchFileReceipt(change.path, self._text(before), current_hash, mode,
                         MISSING_HASH if payload is None else self._digest(payload), after_mode))
            snapshots.append((change, target, before, mode, identity))
            differences.append(self._diff(change.path, before, change.content))
        if "".join(differences) != proposal.unified_diff:
            raise ValueError("patch proposal diff does not match base")
        intent = PatchReceipt(proposal.patch_id, "prepared", tuple(files))
        # Durable intent failure is pre-effect. A crash after intent is NOT success.
        if record_intent is not None:
            record_intent(intent)
        temporary, replaced = [], []
        outcomes = ["unchanged"] * len(files)
        try:
            for (change, target, _, _, _), file in zip(snapshots, files, strict=True):
                temp = None if change.content is None else self._temporary(target, change.content.encode("utf-8"), file.after_mode)
                temporary.append((target, temp))
            for number, ((change, target, _, _, identity), (_, temp)) in enumerate(zip(snapshots, temporary, strict=True)):
                _, _, current_hash, mode, current_identity = self._snapshot(change.path)
                if current_hash != files[number].base_hash or mode != files[number].base_mode or current_identity != identity:
                    raise PatchConflictError("patch_base_changed_before_replace")
                if temp is None:
                    target.unlink()
                else:
                    os.replace(temp, target)
                # Record the effect before observing it; a failed observation
                # cannot leave an unjournaled earlier replace to roll back blindly.
                replaced.append((number, None))
                _, _, actual_hash, actual_mode, actual_identity = self._snapshot(change.path)
                if actual_hash != files[number].after_hash or actual_mode != files[number].after_mode:
                    outcomes[number] = "preserved"
                    raise PatchConflictError("patch_changed_after_replace")
                replaced[-1] = number, actual_identity
                outcomes[number] = "applied"
        except Exception as exc:
            for number, committed_identity in reversed(replaced):
                change, target, before, mode, _ = snapshots[number]
                try:
                    _, _, actual_hash, actual_mode, actual_identity = self._snapshot(change.path)
                    file = files[number]
                    if (outcomes[number] != "applied" or actual_hash != file.after_hash
                            or actual_mode != file.after_mode or actual_identity != committed_identity):
                        outcomes[number] = "preserved"
                        continue
                    if before is None:
                        target.unlink()
                    else:
                        rollback = self._temporary(target, before, mode)
                        try:
                            os.replace(rollback, target)
                        finally:
                            self._remove_temporary(rollback)
                    outcomes[number] = "rolled_back"
                except Exception:
                    outcomes[number] = "unknown"
            # Private exception evidence; durable ledger records it without
            # leaking preimages or pretending failed/partial effects are absent.
            exc.patch_receipt = intent.completed(outcomes, success=False)
            raise
        finally:
            try:
                for _, temp in temporary:
                    if temp is not None:
                        self._remove_temporary(temp)
            except Exception:
                cleanup_error = ValueError("patch_temporary_cleanup_failed")
                cleanup_error.patch_receipt = intent.completed(outcomes, success=all(outcome == "applied" for outcome in outcomes))
                raise cleanup_error from None
        receipt = intent.completed(outcomes, success=True)
        return PatchApplyResult(proposal.patch_id, tuple(change.path for change in proposal.changes), receipt)
