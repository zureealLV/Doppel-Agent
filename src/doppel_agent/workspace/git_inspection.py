"""S6 binary plumbing foundations, not yet a repository inspection endpoint.

These pure parsers never invoke Git or read files. Syntax/identity validation is
not repository trust: the next slice must isolate Git metadata/config and apply
dynamic workspace ignore and bounded filesystem identity checks before reads.
Repository entries are not evidence that an agent generated a change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..context.manifest import ContextManifestError, relative_path


MAX_PROTOCOL_BYTES = 4 * 1024 * 1024
MAX_PROTOCOL_RECORDS = 20_000
ObjectFormat = Literal["sha1", "sha256"]
_MODE_KIND = {
    "100644": "blob",
    "100755": "blob",
    "120000": "blob",  # link metadata, never permission to dereference
    "160000": "commit",  # submodule metadata, never recurse
    "040000": "tree",  # sparse/directory metadata, not a regular file
}


class GitInspectionError(ValueError):
    """Stable reason only; do not surface raw Git paths/config/stderr."""


@dataclass(frozen=True)
class GitEntry:
    path: str
    mode: str
    object_id: str
    kind: str
    stage: int = 0

    @property
    def regular_file(self) -> bool:
        return self.mode in {"100644", "100755"}


@dataclass(frozen=True)
class GitEntries:
    entries: tuple[GitEntry, ...]
    excluded_count: int
    record_count: int
    object_format: ObjectFormat


def object_id(value: str, object_format: ObjectFormat, *, allow_zero: bool = False) -> str:
    length = {"sha1": 40, "sha256": 64}.get(object_format) if isinstance(object_format, str) else None
    if (
        length is None
        or not isinstance(value, str)
        or re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is None
        or not allow_zero and not value.strip("0")
    ):
        raise GitInspectionError("invalid_git_object_id")
    return value


def _records(payload: bytes, object_format: ObjectFormat) -> list[bytes]:
    if not isinstance(object_format, str) or object_format not in {"sha1", "sha256"}:
        raise GitInspectionError("unsupported_git_object_format")
    if not isinstance(payload, bytes) or len(payload) > MAX_PROTOCOL_BYTES:
        raise GitInspectionError("git_protocol_byte_limit")
    if not payload:
        return []
    if not payload.endswith(b"\x00"):
        raise GitInspectionError("incomplete_git_protocol")
    count = payload.count(b"\x00")
    if count > MAX_PROTOCOL_RECORDS:
        raise GitInspectionError("git_protocol_record_limit")
    records = payload[:-1].split(b"\x00")
    if any(not record for record in records):
        raise GitInspectionError("invalid_git_protocol_record")
    return records


def _record(record: bytes) -> tuple[list[str], str]:
    header, separator, raw_path = record.partition(b"\t")
    if not separator or not raw_path:
        raise GitInspectionError("invalid_git_protocol_record")
    try:
        fields = header.decode("ascii").split(" ")
        path = raw_path.decode("utf-8", errors="strict")
    except UnicodeError:
        raise GitInspectionError("invalid_git_protocol_encoding") from None
    if len(fields) != 3 or any(not field for field in fields):
        raise GitInspectionError("invalid_git_protocol_header")
    return fields, path


def _path(path: str, mode: str) -> str | None:
    # Sparse directory records may end in '/', but no other spelling is fixed
    # silently. Preserve the exact byte-decoded spelling of ordinary paths.
    if mode == "040000" and path.endswith("/"):
        path = path[:-1]
    if "\x7f" in path:
        raise GitInspectionError("invalid_git_path")
    try:
        return relative_path(path)
    except ContextManifestError as exc:
        if str(exc) == "context_path_excluded":
            return None
        raise GitInspectionError("invalid_git_path") from None


def _mode(mode: str) -> str:
    if mode not in _MODE_KIND:
        raise GitInspectionError("invalid_git_mode")
    return _MODE_KIND[mode]


def _remember_spelling(path: str, spellings: dict[str, str]) -> None:
    # Checking just the full path misses Dir/a + dir/b on Windows.
    parts = path.split("/")
    for end in range(1, len(parts) + 1):
        prefix = "/".join(parts[:end])
        key = prefix.casefold()
        if key in spellings and spellings[key] != prefix:
            raise GitInspectionError("ambiguous_git_path")
        spellings[key] = prefix


def parse_index(payload: bytes, object_format: ObjectFormat = "sha1") -> GitEntries:
    """Parse `ls-files --stage -z`; retain every unmerged stage, no content."""
    records = _records(payload, object_format)
    entries: list[GitEntry] = []
    excluded = 0
    spellings: dict[str, str] = {}
    stages: dict[str, set[int]] = {}
    for record in records:
        (mode, oid, raw_stage), raw_path = _record(record)
        kind = _mode(mode)
        oid = object_id(oid, object_format, allow_zero=True)
        if raw_stage not in {"0", "1", "2", "3"}:
            raise GitInspectionError("invalid_git_index_stage")
        stage = int(raw_stage)
        path = _path(raw_path, mode)
        if path is None:
            excluded += 1
            continue
        key = path.casefold()
        _remember_spelling(path, spellings)
        seen = stages.setdefault(key, set())
        if stage in seen or seen and (stage == 0 or 0 in seen):
            raise GitInspectionError("invalid_git_index_stages")
        if kind == "tree" and stage != 0:
            raise GitInspectionError("invalid_git_index_stages")
        seen.add(stage)
        entries.append(GitEntry(path, mode, oid, kind, stage))
    return GitEntries(tuple(entries), excluded, len(records), object_format)


def parse_tree(payload: bytes, object_format: ObjectFormat = "sha1") -> GitEntries:
    """Parse `ls-tree -rz --full-tree`; reject conflicting/aliased paths."""
    records = _records(payload, object_format)
    entries: list[GitEntry] = []
    excluded = 0
    seen: set[str] = set()
    spellings: dict[str, str] = {}
    for record in records:
        (mode, kind, oid), raw_path = _record(record)
        if kind != _mode(mode):
            raise GitInspectionError("invalid_git_tree_kind")
        oid = object_id(oid, object_format)
        path = _path(raw_path, mode)
        if path is None:
            excluded += 1
            continue
        key = path.casefold()
        if key in seen:
            raise GitInspectionError("ambiguous_git_path")
        _remember_spelling(path, spellings)
        seen.add(key)
        entries.append(GitEntry(path, mode, oid, kind))
    return GitEntries(tuple(entries), excluded, len(records), object_format)
