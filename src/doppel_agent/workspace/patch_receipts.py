"""Bounded patch-effect evidence, separate from Git and provider-facing output.

Preimages are private application state, never list/report/tool result contents.
A prepared intent is not evidence of a completed filesystem effect.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any


MAX_RECEIPT_FILES = 32
MAX_PREIMAGE_BYTES = 512 * 1024


def content_hash(content: str | None) -> str:
    return "missing" if content is None else "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def valid_hash(value: Any) -> bool:
    return isinstance(value, str) and (value == "missing" or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None)


def receipt_summary(value: Any) -> dict[str, Any]:
    """Validate content-free historical evidence without inventing preimages."""
    from ..context.manifest import ContextManifestError, relative_path

    if (not isinstance(value, dict) or set(value) != {"schema", "patch_id", "status", "files"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["patch_id"], str) or re.fullmatch(r"[0-9a-f]{32}", value["patch_id"]) is None
            or not isinstance(value["status"], str) or value["status"] not in {"prepared", "applied", "failed"}
            or not isinstance(value["files"], list) or not 1 <= len(value["files"]) <= MAX_RECEIPT_FILES):
        raise ValueError("invalid_patch_receipt_summary")
    files, paths, spellings = [], set(), {}
    for file in value["files"]:
        if not isinstance(file, dict) or set(file) != {"path", "base_hash", "base_mode", "after_hash", "after_mode", "outcome"}:
            raise ValueError("invalid_patch_receipt_summary")
        try:
            path = relative_path(file["path"])
        except ContextManifestError:
            raise ValueError("invalid_patch_receipt_summary") from None
        if path.casefold() in paths or "\x7f" in path:
            raise ValueError("invalid_patch_receipt_summary")
        paths.add(path.casefold())
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            spelling = "/".join(parts[:count])
            if spellings.get(spelling.casefold(), spelling) != spelling:
                raise ValueError("invalid_patch_receipt_summary")
            spellings[spelling.casefold()] = spelling
        for hash_key, mode_key in (("base_hash", "base_mode"), ("after_hash", "after_mode")):
            mode = file[mode_key]
            if (not valid_hash(file[hash_key]) or (file[hash_key] == "missing") != (mode is None)
                    or mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777)):
                raise ValueError("invalid_patch_receipt_summary")
        outcome = file["outcome"]
        if (not isinstance(outcome, str) or outcome not in {"pending", "applied", "unchanged", "rolled_back", "preserved", "unknown"}
                or value["status"] == "prepared" and outcome != "pending"
                or value["status"] == "applied" and outcome != "applied"
                or value["status"] == "failed" and outcome == "pending"):
            raise ValueError("invalid_patch_receipt_summary")
        files.append(dict(file))
    return {"schema": 1, "patch_id": value["patch_id"], "status": value["status"], "files": files}


@dataclass(frozen=True)
class PatchFileReceipt:
    path: str
    before_content: str | None
    base_hash: str
    base_mode: int | None
    after_hash: str
    after_mode: int | None
    outcome: str = "pending"

    def as_dict(self, *, include_preimage: bool = True) -> dict[str, Any]:
        value = {"path": self.path, "base_hash": self.base_hash, "base_mode": self.base_mode,
                 "after_hash": self.after_hash, "after_mode": self.after_mode, "outcome": self.outcome}
        if include_preimage:
            value["before_content"] = self.before_content
        return value


@dataclass(frozen=True)
class PatchReceipt:
    patch_id: str
    status: str
    files: tuple[PatchFileReceipt, ...]

    def as_dict(self, *, include_preimage: bool = True) -> dict[str, Any]:
        return {"schema": 1, "patch_id": self.patch_id, "status": self.status,
                "files": [file.as_dict(include_preimage=include_preimage) for file in self.files]}

    def completed(self, outcomes: list[str], *, success: bool) -> "PatchReceipt":
        if len(outcomes) != len(self.files):
            raise ValueError("invalid_patch_receipt")
        return replace(self, status="applied" if success else "failed",
                       files=tuple(replace(file, outcome=outcome) for file, outcome in zip(self.files, outcomes, strict=True)))

    @classmethod
    def from_dict(cls, value: Any) -> "PatchReceipt":
        from ..context.manifest import ContextManifestError, relative_path

        if (not isinstance(value, dict) or set(value) != {"schema", "patch_id", "status", "files"}
                or type(value["schema"]) is not int or value["schema"] != 1
                or not isinstance(value["patch_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["patch_id"])
                or not isinstance(value["status"], str) or value["status"] not in {"prepared", "applied", "failed"}
                or not isinstance(value["files"], list) or not 1 <= len(value["files"]) <= MAX_RECEIPT_FILES):
            raise ValueError("invalid_patch_receipt")
        files, spellings, total = [], {}, 0
        for item in value["files"]:
            if not isinstance(item, dict) or set(item) != {
                "path", "before_content", "base_hash", "base_mode", "after_hash", "after_mode", "outcome"
            }:
                raise ValueError("invalid_patch_receipt")
            try:
                path = relative_path(item["path"])
            except ContextManifestError:
                raise ValueError("invalid_patch_receipt") from None
            if "\x7f" in path:
                raise ValueError("invalid_patch_receipt")
            parts = path.split("/")
            for count in range(1, len(parts) + 1):
                spelling = "/".join(parts[:count])
                key = spelling.casefold()
                if key in spellings and spellings[key] != spelling:
                    raise ValueError("invalid_patch_receipt")
                spellings[key] = spelling
            if any(file.path.casefold() == path.casefold() for file in files):
                raise ValueError("invalid_patch_receipt")
            content = item["before_content"]
            try:
                if content is not None and (not isinstance(content, str) or "\x00" in content):
                    raise ValueError("invalid_patch_receipt")
                size = 0 if content is None else len(content.encode("utf-8"))
                total += size
                if total > MAX_PREIMAGE_BYTES or content_hash(content) != item["base_hash"]:
                    raise ValueError("invalid_patch_receipt")
            except UnicodeError:
                raise ValueError("invalid_patch_receipt") from None
            if not valid_hash(item["after_hash"]):
                raise ValueError("invalid_patch_receipt")
            for hash_key, mode_key in (("base_hash", "base_mode"), ("after_hash", "after_mode")):
                mode = item[mode_key]
                if ((item[hash_key] == "missing") != (mode is None)
                        or mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777)):
                    raise ValueError("invalid_patch_receipt")
            outcome = item["outcome"]
            if (not isinstance(outcome, str) or outcome not in {"pending", "applied", "unchanged", "rolled_back", "preserved", "unknown"}
                    or value["status"] == "prepared" and outcome != "pending"
                    or value["status"] == "applied" and outcome != "applied"
                    or value["status"] == "failed" and outcome == "pending"):
                raise ValueError("invalid_patch_receipt")
            files.append(PatchFileReceipt(**item))
        return cls(value["patch_id"], value["status"], tuple(files))
