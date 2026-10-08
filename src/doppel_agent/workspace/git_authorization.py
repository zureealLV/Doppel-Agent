"""Ephemeral exact linked-worktree metadata bindings, supplied only by native owner.

Selection performs directory identity checks only. AFTER native confirmation,
capture reads three bounded pointer files, never config/HEAD/index/objects or Git.
This is application-level identity checking, not a hostile-race OS sandbox.
"""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from ..projects.catalog import local_project_path
from .git_inspection import GitInspectionError
from .git_metadata import GitMetadataReader, _MetadataGuard, _linked, _signature


def _identity(path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
        raise GitInspectionError("invalid_git_metadata_authorization")
    # Content churn in a Git directory is expected, not expanded authority.
    return info.st_dev, info.st_ino, info.st_mode, getattr(info, "st_file_attributes", 0)


def _directories(path, deadline):
    if len(path.parents) > 64:
        raise GitInspectionError("invalid_git_metadata_authorization")
    rows = []
    for candidate in (path, *path.parents):
        if monotonic() >= deadline:
            raise GitInspectionError("git_metadata_deadline")
        rows.append((candidate, _identity(candidate)))
    return tuple(rows)


def _check_directories(rows, deadline):
    for path, identity in rows:
        if monotonic() >= deadline:
            raise GitInspectionError("git_metadata_deadline")
        if _identity(path) != identity:
            raise GitInspectionError("git_metadata_authorization_changed")


@dataclass(frozen=True)
class SelectedMetadataRoot:
    path: Path
    directories: tuple
    workspace: Path | None = None
    workspace_directories: tuple = ()


@dataclass(frozen=True)
class LinkedMetadataBinding:
    workspace: Path
    common: Path
    local: Path
    directories: tuple
    pointers: tuple


def select_metadata_root(value: str | Path, *, workspace: Path | None = None) -> SelectedMetadataRoot:
    try:
        raw = str(value)
        path = Path(raw)
        if (len(raw.encode("utf-8")) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in raw)
                or ".." in path.parts or path.parent == path):
            raise GitInspectionError("invalid_git_metadata_authorization")
        # Refuse UNC/device/mapped network drives BEFORE filesystem probing.
        # Preserve original spelling: do not silently follow a junction/symlink.
        resolved = local_project_path(path)
        if path != resolved:
            raise GitInspectionError("invalid_git_metadata_authorization")
        deadline = monotonic() + 10
        scope = Path(workspace) if workspace is not None else None
        if scope is not None and (not scope.is_absolute() or scope.resolve(strict=True) != scope):
            raise GitInspectionError("invalid_git_metadata_authorization")
        return SelectedMetadataRoot(path, _directories(path, deadline), scope,
                                    _directories(scope, deadline) if scope is not None else ())
    except GitInspectionError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise GitInspectionError("invalid_git_metadata_authorization") from None


def capture_linked_metadata(workspace: Path, selected: SelectedMetadataRoot) -> LinkedMetadataBinding:
    try:
        if type(selected) is not SelectedMetadataRoot:
            raise GitInspectionError("invalid_git_metadata_authorization")
        deadline = monotonic() + 10
        _check_directories(selected.directories, deadline)
        if selected.workspace is not None:
            if Path(workspace) != selected.workspace:
                raise GitInspectionError("git_metadata_authorization_changed")
            _check_directories(selected.workspace_directories, deadline)
        # Revalidate path admission without recapturing consent after replacement.
        if select_metadata_root(selected.path).directories != selected.directories:
            raise GitInspectionError("git_metadata_authorization_changed")
        reader = GitMetadataReader(workspace, authorized_metadata_roots=(selected.path,))
        guard = _MetadataGuard((reader.root, selected.path), deadline=deadline)
        local, common, linked = reader._discover(guard)
        if not linked or common != selected.path:
            raise GitInspectionError("git_metadata_authorization_required")
        pointers = []
        for path, maximum in ((reader.root / ".git", 4096), (local / "commondir", 128), (local / "gitdir", 4096)):
            raw = guard.read(path, maximum)
            pointers.append((path, hashlib.sha256(raw).hexdigest(), _signature(path.lstat())))
        directories = _directories(reader.root, deadline) + _directories(local, deadline)
        guard.check_unchanged()
        _check_directories(selected.directories, deadline)
        _check_directories(selected.workspace_directories, deadline)
        return LinkedMetadataBinding(reader.root, common, local, directories, tuple(pointers))
    except GitInspectionError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise GitInspectionError("git_metadata_authorization_changed") from None


def check_linked_metadata(binding: LinkedMetadataBinding) -> None:
    try:
        deadline = monotonic() + 10
        _check_directories(binding.directories, deadline)
        # Recheck original local marker BEFORE opening any external pointer.
        guard = _MetadataGuard((binding.workspace, binding.common), deadline=deadline)
        for path, digest, signature in binding.pointers:
            raw = guard.read(path, 4096 if path.name != "commondir" else 128)
            if hashlib.sha256(raw).hexdigest() != digest or _signature(path.lstat()) != signature:
                raise GitInspectionError("git_metadata_authorization_changed")
        guard.check_unchanged()
    except GitInspectionError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise GitInspectionError("git_metadata_authorization_changed") from None
