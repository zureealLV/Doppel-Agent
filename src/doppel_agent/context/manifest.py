"""Explicit bounded UTF-8 file snapshots; no provider, shell or default upload.

Workspace ignore files are interpreted conservatively as deny-only rules, not
complete Git parity. Negation never overrides a deny/secret boundary. This is
application-level path/identity checking, not a strong OS sandbox.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..persistence.database import sqlite_connection
from ..persistence.migrations import apply_migrations
from ..workspace.service import WorkspaceService
from .policy import ContextPolicy


MAX_FILES = 24
MAX_FILE_BYTES = 1024 * 1024
MAX_ENTRY_BYTES = 32 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_LINES = 2000
_IGNORED_DIRS = frozenset({
    ".venv", "venv", "node_modules", "dist", ".dist", ".dist-debug", "build", ".build-tmp", ".next",
    "artifacts", ".artifacts", ".bench-results", "__pycache__", ".pytest_cache", ".cache", ".vs",
    ".codex", ".claude", ".config", ".azure", ".kube", ".gnupg",
})


class ContextManifestError(ValueError):
    """Stable reason only: never expose host paths or raw IO exception text."""


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ContextManifestError("invalid_context_identifier")
    return value


def request_key(value: Any) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 128 or "\x00" in value:
        raise ContextManifestError("invalid_context_request_key")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ContextManifestError("invalid_context_request_key") from None
    return value


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 or c in "\\:" for c in value):
        raise ContextManifestError("invalid_context_path")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ContextManifestError("invalid_context_path") from None
    parts = value.split("/")
    if len(parts) > 32 or any(not part or part in {".", ".."} or part.startswith("~") or part.endswith((".", " ")) for part in parts):
        raise ContextManifestError("invalid_context_path")
    devices = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
    if any(part.split(".", 1)[0].casefold() in devices for part in parts):
        raise ContextManifestError("invalid_context_path")
    if WorkspaceService.is_protected_parts(tuple(parts)) or any(part.casefold() in _IGNORED_DIRS for part in parts):
        raise ContextManifestError("context_path_excluded")
    return value


def file_specs(files: Any) -> list[dict[str, Any]]:
    if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= MAX_FILES:
        raise ContextManifestError("invalid_context_file_count")
    result, seen = [], set()
    for item in files:
        if not isinstance(item, dict) or set(item) - {"path", "start_line", "end_line", "expected_file_sha256"} or "path" not in item:
            raise ContextManifestError("invalid_context_file_fields")
        path = relative_path(item["path"])
        start, end, expected = item.get("start_line", 1), item.get("end_line"), item.get("expected_file_sha256")
        if type(start) is not int or start < 1 or end is not None and (type(end) is not int or end < start):
            raise ContextManifestError("invalid_context_line_range")
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise ContextManifestError("invalid_context_file_hash")
        identity = (path.casefold(), start, end)
        if identity in seen:
            raise ContextManifestError("duplicate_context_attachment")
        seen.add(identity)
        result.append({"path": path, "start_line": start, "end_line": end, "expected_file_sha256": expected})
    return result


def budget(value: Any) -> int:
    if type(value) is not int or not 256 <= value <= MAX_MANIFEST_BYTES:
        raise ContextManifestError("invalid_context_budget")
    return value


def _glob_matches(parts: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    @lru_cache(maxsize=4096)
    def match(i: int, j: int) -> bool:
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)
    return match(0, 0)


class ManifestReader:
    def __init__(self, workspace: Path):
        self.workspace = WorkspaceService(workspace)
        self.root = self.workspace.root

    def _safe_path(self, relative: str) -> Path:
        parts = relative_path(relative).split("/")
        current = self.root
        try:
            for part in parts:
                current = current / part
                info = current.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ContextManifestError("context_link_blocked")
            resolved = self.workspace.resolve(relative, must_exist=True)
            if resolved != current or not resolved.is_relative_to(self.root):
                raise ContextManifestError("context_path_changed")
            return current
        except ContextManifestError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ContextManifestError("context_path_unavailable") from None

    @staticmethod
    def _signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _read_regular(self, relative: str, maximum: int) -> bytes:
        try:
            path = self._safe_path(relative)
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ContextManifestError("context_nonregular_or_hardlink")
            if before.st_size > maximum:
                raise ContextManifestError("context_file_too_large")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                current = self._safe_path(relative).lstat()
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or self._signature(opened) != self._signature(current):
                    raise ContextManifestError("context_path_changed")
                raw = handle.read(maximum + 1)
                if len(raw) > maximum:
                    raise ContextManifestError("context_file_too_large")
                if self._signature(opened) != self._signature(os.fstat(handle.fileno())):
                    raise ContextManifestError("context_file_changed_during_read")
            if self._signature(opened) != self._signature(self._safe_path(relative).lstat()):
                raise ContextManifestError("context_file_changed_during_read")
            return raw
        except ContextManifestError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ContextManifestError("context_file_unavailable") from None

    def _ignore_info(self, relative: str) -> os.stat_result:
        """Policy probe hook; the default context behavior remains lstat-only."""
        return (self.root / relative).lstat()

    def _check_ignores(self, relative: str) -> None:
        parts, consumed, rules_count = relative.split("/"), 0, 0
        for depth in range(len(parts)):
            for name in (".gitignore", ".doppelignore"):
                ignore_path = "/".join([*parts[:depth], name])
                try:
                    self._ignore_info(ignore_path)
                except FileNotFoundError:
                    continue
                except OSError:
                    raise ContextManifestError("context_ignore_unavailable") from None
                raw = self._read_regular(ignore_path, 64 * 1024)
                consumed += len(raw)
                if consumed > 128 * 1024:
                    raise ContextManifestError("context_ignore_budget_exceeded")
                try:
                    lines = raw.decode("utf-8-sig").splitlines()
                except UnicodeError:
                    raise ContextManifestError("context_ignore_invalid_utf8") from None
                for line in lines:
                    pattern = line.rstrip()
                    if not pattern or pattern.startswith(("#", "!")):
                        continue  # Negation is deliberately not an allow grant.
                    if "\\" in pattern or "\x00" in pattern or len(pattern) > 2048:
                        raise ContextManifestError("context_ignore_unsupported_rule")
                    rules_count += 1
                    if rules_count > 2048:
                        raise ContextManifestError("context_ignore_rule_limit")
                    directory = pattern.endswith("/")
                    anchored = pattern.startswith("/")
                    pattern = pattern.strip("/").casefold()
                    if len(pattern.split("/")) > 64:
                        raise ContextManifestError("context_ignore_unsupported_rule")
                    tail = tuple(part.casefold() for part in parts[depth:])
                    candidates = [tail[:count] for count in range(1, len(tail) + (0 if directory else 1))]
                    if anchored or "/" in pattern:
                        matches = any(_glob_matches(candidate, tuple(pattern.split("/"))) for candidate in candidates)
                    else:
                        matches = any(fnmatch.fnmatchcase(candidate[-1], pattern) for candidate in candidates)
                    if matches:
                        raise ContextManifestError("context_path_ignored")

    def _raw(self, relative: str) -> bytes:
        self._safe_path(relative)  # Refuse links before even consulting ancestor ignore files.
        self._check_ignores(relative)
        raw = self._read_regular(relative, MAX_FILE_BYTES)
        self._check_ignores(relative)  # Changed policy must not silently expose the already-read bytes.
        return raw

    def preview(self, files: Any, *, budget_bytes: int = MAX_MANIFEST_BYTES, require_expected: bool = False) -> dict[str, Any]:
        specs, limit = file_specs(files), budget(budget_bytes)
        entries, used = [], 0
        for index, spec in enumerate(specs):
            if used >= limit:
                raise ContextManifestError("manifest_budget_exhausted")
            if require_expected and spec["expected_file_sha256"] is None:
                raise ContextManifestError("preview_hash_required")
            raw = self._raw(spec["path"])
            file_hash = hashlib.sha256(raw).hexdigest()
            if spec["expected_file_sha256"] is not None and spec["expected_file_sha256"] != file_hash:
                raise ContextManifestError("stale_file")
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeError:
                raise ContextManifestError("context_file_invalid_utf8") from None
            if "\x00" in text:
                raise ContextManifestError("context_binary_file")
            lines = text.splitlines(keepends=True) or [""]
            start, requested_end = spec["start_line"], spec["end_line"]
            end = len(lines) if requested_end is None else requested_end
            if start > len(lines) or end > len(lines):
                raise ContextManifestError("context_lines_out_of_bounds")
            actual_end, reasons = min(end, start + MAX_LINES - 1), []
            if actual_end < end:
                reasons.append("line_limit")
            selected = "".join(lines[start - 1:actual_end]).encode("utf-8")
            if len(selected) > MAX_ENTRY_BYTES:
                reasons.append("entry_byte_limit")
            if len(selected) > limit - used:
                reasons.append("manifest_byte_limit")
            included = selected[:min(MAX_ENTRY_BYTES, limit - used)].decode("utf-8", errors="ignore")
            size = len(included.encode("utf-8"))
            included_lines = len(included.splitlines(keepends=True))
            entries.append({"index": index, "path": spec["path"], "start_line": start,
                "requested_end_line": requested_end, "end_line": start + max(1, included_lines) - 1,
                "total_lines": len(lines), "file_bytes": len(raw), "file_sha256": file_hash,
                "captured_at": datetime.now(UTC).isoformat(),
                "content_sha256": hashlib.sha256(included.encode("utf-8")).hexdigest(),
                "text": included, "bytes": size, "truncation_reasons": reasons})
            used += size
        return {"entries": entries, "budget_bytes": limit, "total_bytes": used,
            "estimated_tokens": ContextPolicy.estimate_utf8_bytes(used), "estimate_method": "utf8_bytes_div4", "actual_tokens": None}

    def check(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for entry in manifest["entries"]:
            try:
                current = hashlib.sha256(self._raw(entry["path"])).hexdigest()
                result.append({"index": entry["index"], "status": "current" if current == entry["file_sha256"] else "stale", "reason": ""})
            except ContextManifestError as error:
                result.append({"index": entry["index"], "status": "unavailable", "reason": str(error)})
        return result


class ManifestStore:
    def __init__(self, database: Path):
        self.database = database
        apply_migrations(database)

    @staticmethod
    def _project(row) -> dict[str, Any]:
        return {**json.loads(row["snapshot_json"]), "manifest_id": row["manifest_id"],
            "request_key": row["request_key"], "created_at": row["created_at"]}

    def get(self, manifest_id: str) -> dict[str, Any] | None:
        identifier(manifest_id)
        with sqlite_connection(self.database) as db:
            row = db.execute("SELECT * FROM context_manifests WHERE manifest_id=?", (manifest_id,)).fetchone()
            return self._project(row) if row else None

    def accept(self, reader: ManifestReader, files: Any, *, budget_bytes: int, confirmed: bool, idempotency_key: str) -> dict[str, Any]:
        if confirmed is not True:
            raise ContextManifestError("confirmation_required")
        key, specs, limit = request_key(idempotency_key), file_specs(files), budget(budget_bytes)
        encoded = canonical({"files": specs, "budget_bytes": limit})
        # A lost ACK must return the originally accepted bytes even after edits.
        with sqlite_connection(self.database) as db:
            row = db.execute("SELECT * FROM context_manifests WHERE request_key=?", (key,)).fetchone()
            if row:
                if row["request_json"] != encoded:
                    raise ContextManifestError("manifest_key_reused")
                return self._project(row)
        snapshot = reader.preview(specs, budget_bytes=limit, require_expected=True)
        with sqlite_connection(self.database) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM context_manifests WHERE request_key=?", (key,)).fetchone()
            if row:
                if row["request_json"] != encoded:
                    raise ContextManifestError("manifest_key_reused")
                return self._project(row)
            mid, now = uuid4().hex, datetime.now(UTC).isoformat()
            db.execute("INSERT INTO context_manifests VALUES (?,?,?,?,?)", (mid, key, encoded, canonical(snapshot), now))
            return self._project(db.execute("SELECT * FROM context_manifests WHERE manifest_id=?", (mid,)).fetchone())
