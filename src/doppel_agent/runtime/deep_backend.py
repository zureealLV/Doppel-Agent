"""Deep Agents backend constrained by Doppel workspace policy."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from ..workspace import WorkspacePolicyError, WorkspaceService


AuditCallback = Callable[[str, dict[str, Any]], None]


class DoppelBackend(FilesystemBackend):
    """Filesystem backend with virtual paths, secret blocking and audit hooks.

    It intentionally does not implement the sandbox execution protocol, so the
    Deep Agents ``execute`` tool cannot bypass Doppel's command policy.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        allow_write: bool = False,
        audit: AuditCallback | None = None,
        max_file_size_mb: int = 10,
    ) -> None:
        self.policy = WorkspaceService(workspace)
        self.allow_write = allow_write
        self.audit = audit
        super().__init__(self.policy.root, virtual_mode=True, max_file_size_mb=max_file_size_mb)

    def _record(self, operation: str, path: str, *, allowed: bool, detail: str = "") -> None:
        if self.audit is not None:
            self.audit(operation, {"path": path, "allowed": allowed, "detail": detail})

    def _check(self, operation: str, path: str, *, write: bool = False, must_exist: bool = True) -> str | None:
        try:
            if write and not self.allow_write:
                raise WorkspacePolicyError("workspace write capability is disabled")
            self.policy.resolve(path, must_exist=must_exist)
        except (WorkspacePolicyError, FileNotFoundError) as exc:
            self._record(operation, path, allowed=False, detail=str(exc))
            return f"Permission denied for '{path}': {exc}"
        self._record(operation, path, allowed=True)
        return None

    def ls(self, path: str) -> LsResult:
        if error := self._check("ls", path):
            return LsResult(error=error)
        result = super().ls(path)
        if result.entries is not None:
            result.entries = [entry for entry in result.entries if self.policy.can_surface(entry["path"])]
        return result

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if error := self._check("read", file_path):
            return ReadResult(error=error)
        return super().read(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        search_root = path or "/"
        if error := self._check("grep", search_root):
            return GrepResult(error=error)
        if ".." in (glob or ""):
            return GrepResult(error="Path traversal is blocked")
        result = super().grep(pattern, path, glob, max_count=max_count)
        if result.matches is not None:
            result.matches = [match for match in result.matches if self.policy.can_surface(match["path"])]
        return result

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        search_root = path or "/"
        if error := self._check("glob", search_root):
            return GlobResult(error=error)
        if ".." in pattern:
            return GlobResult(error="Path traversal is blocked")
        result = super().glob(pattern, path)
        if result.matches is not None:
            result.matches = [match for match in result.matches if self.policy.can_surface(match["path"])]
        return result

    def write(self, file_path: str, content: str) -> WriteResult:
        if error := self._check("write", file_path, write=True, must_exist=False):
            return WriteResult(error=error)
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        if error := self._check("edit", file_path, write=True):
            return EditResult(error=error)
        return super().edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        if error := self._check("delete", file_path, write=True):
            return DeleteResult(error=error)
        return super().delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        denied = [
            (path, self._check("upload", path, write=True, must_exist=False)) for path, _ in files
        ]
        if any(error for _, error in denied):
            return [FileUploadResponse(path=path, error=error) for path, error in denied]
        return super().upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        denied = [(path, self._check("download", path)) for path in paths]
        if any(error for _, error in denied):
            return [FileDownloadResponse(path=path, error=error) for path, error in denied]
        return super().download_files(paths)
