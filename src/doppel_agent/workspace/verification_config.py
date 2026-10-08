"""Immutable bounded project verification previews, not authorization or proof.

Explicit argv may itself name an interpreter/script; shell=False is not an OS
sandbox. Config bytes/identity are pinned, NOT executable/dependency/file content.
Parent/file checks are fail-closed application checks, not hostile-race isolation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path


MAX_CONFIG_BYTES = 64 * 1024
MAX_COMMANDS = 16


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _identity(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _check(info, *, directory=False):
    if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or not directory and info.st_nlink != 1):
        raise ValueError("verification_config_unavailable")


def _pairs(items):
    data = {}
    for key, value in items:
        if key in data:
            raise ValueError("verification_config_duplicate_key")
        data[key] = value
    return data


@dataclass(frozen=True)
class VerificationCommand:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float

    def as_dict(self) -> dict:
        return {"name": self.name, "argv": list(self.argv), "timeout_seconds": self.timeout_seconds}


@dataclass(frozen=True)
class VerificationConfig:
    path: str
    config_hash: str
    snapshot_hash: str
    workspace_id: str
    commands: tuple[VerificationCommand, ...]
    max_output_bytes: int
    stop_on_failure: bool

    @property
    def source(self) -> dict:
        return {"path": self.path, "config_hash": self.config_hash,
                "snapshot_hash": self.snapshot_hash, "workspace_id": self.workspace_id}

    def select(self, names=None) -> "VerificationPlan":
        available = {command.name: command for command in self.commands}
        if names is not None and (isinstance(names, (str, bytes)) or not isinstance(names, (tuple, list))):
            raise ValueError("invalid_verification_selection")
        selected = list(available) if names is None else list(names)
        if (not selected or len(selected) > MAX_COMMANDS or any(not isinstance(name, str) for name in selected)
                or len(selected) != len(set(selected)) or any(name not in available for name in selected)):
            raise ValueError("invalid_verification_selection")
        return VerificationPlan(self, tuple(available[name] for name in selected))


@dataclass(frozen=True)
class VerificationPlan:
    config: VerificationConfig
    commands: tuple[VerificationCommand, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(command.name for command in self.commands)

    @property
    def plan_id(self) -> str:
        return _hash(self._payload())

    def _payload(self) -> dict:
        return {"schema": 1, "source": self.config.source,
                "commands": [command.as_dict() for command in self.commands],
                "max_output_bytes": self.config.max_output_bytes, "stop_on_failure": self.config.stop_on_failure,
                "operation_timeout_seconds": 600.0}

    def as_dict(self) -> dict:
        return {"plan_id": self.plan_id, **self._payload()}


def capture(workspace: Path, config_path: Path) -> VerificationConfig:
    """Read only this explicitly scoped regular config, never resolve an external link."""
    workspace = workspace.resolve(strict=True)
    path = config_path.absolute()
    try:
        try:
            relative = path.relative_to(workspace)
        except ValueError:
            raise ValueError("verification_config_unavailable") from None
        if not relative.parts or any(part in {".", ".."} for part in relative.parts):
            raise ValueError("verification_config_unavailable")
        parents = []
        current = workspace
        root_info = current.lstat()
        _check(root_info, directory=True)
        parents.append((current, _identity(root_info)))
        missing = False
        for part in relative.parts[:-1]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                missing = True
                break
            _check(info, directory=True)
            parents.append((current, _identity(info)))
        try:
            before = None if missing else path.lstat()
        except FileNotFoundError:
            before = None
        workspace_id = _hash(os.path.normcase(str(workspace)))
        if before is None:
            return VerificationConfig(relative.as_posix(), "missing", "missing", workspace_id, (), 65536, True)
        _check(before)
        if before.st_size > MAX_CONFIG_BYTES:
            raise ValueError("verification_config_budget")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            _check(opened)
            if _identity(opened) != _identity(before):
                raise ValueError("verification_config_unavailable")
            raw = b""
            while len(raw) <= MAX_CONFIG_BYTES:
                chunk = os.read(descriptor, MAX_CONFIG_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_CONFIG_BYTES or _identity(after) != _identity(before) or _identity(path.lstat()) != _identity(before):
            raise ValueError("verification_config_unavailable")
        for parent, identity in parents:
            # File reads may update access times, which identity deliberately omits.
            if _identity(parent.lstat()) != identity:
                raise ValueError("verification_config_unavailable")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if not isinstance(data, dict) or set(data) - {"commands", "max_output_bytes", "stop_on_failure"}:
            raise ValueError("verification_config_invalid")
        entries = data.get("commands")
        if not isinstance(entries, list) or len(entries) > MAX_COMMANDS:
            raise ValueError("verification_config_invalid")
        commands, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"name", "argv", "timeout_seconds"}:
                raise ValueError("verification_config_invalid")
            name, argv, timeout = entry["name"], entry["argv"], entry["timeout_seconds"]
            if (not isinstance(name, str) or not 1 <= len(name.encode("utf-8")) <= 128 or "\x00" in name or name in seen
                    or not isinstance(argv, list) or not 1 <= len(argv) <= 64
                    or any(not isinstance(arg, str) or not 1 <= len(arg.encode("utf-8")) <= 4096 or "\x00" in arg for arg in argv)
                    or type(timeout) not in {int, float} or not 0 < timeout <= 600 or not math.isfinite(timeout)):
                raise ValueError("verification_config_invalid")
            seen.add(name)
            commands.append(VerificationCommand(name, tuple(argv), float(timeout)))
        limit, stop = data.get("max_output_bytes", 65536), data.get("stop_on_failure", True)
        if type(limit) is not int or not 1 <= limit <= 1024 * 1024 or type(stop) is not bool:
            raise ValueError("verification_config_invalid")
        return VerificationConfig(relative.as_posix(), hashlib.sha256(raw).hexdigest(), _hash(_identity(before)),
                                  workspace_id, tuple(commands), limit, stop)
    except json.JSONDecodeError:
        raise ValueError("verification_config_invalid") from None
    except (OSError, TypeError, UnicodeError, RecursionError):
        raise ValueError("verification_config_unavailable") from None
