"""Fail-closed workspace path validation.

The agent-facing path space is POSIX-like and rooted at ``/``.  Host paths are
never accepted from a model.  Resolution is repeated after following symlinks
so an apparently safe path cannot point at a protected directory.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class WorkspacePolicyError(PermissionError):
    """Raised when an agent path violates the workspace boundary."""


class WorkspaceService:
    PROTECTED_DIRECTORIES = frozenset(
        {
            ".git",
            ".doppel-agent",
            ".ssh",
            ".aws",
            ".docker",
        }
    )
    SECRET_NAMES = frozenset(
        {
            ".env",
            "credentials",
            "credentials.json",
            "secrets.json",
            "id_rsa",
            "id_ed25519",
            "id_ecdsa",
            "id_dsa",
            ".npmrc",
            ".netrc",
            ".pgpass",
            ".git-credentials",
            ".htpasswd",
        }
    )
    SECRET_SUFFIXES = frozenset({".pem", ".pfx", ".p12", ".key"})

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace must be a directory")

    @classmethod
    def is_sensitive_name(cls, name: str) -> bool:
        lowered = name.casefold()
        if lowered == ".env.example":
            return False
        return (
            lowered in cls.SECRET_NAMES
            or lowered.startswith(".env.")
            or Path(lowered).suffix in cls.SECRET_SUFFIXES
        )

    @classmethod
    def is_protected_parts(cls, parts: tuple[str, ...]) -> bool:
        lowered = tuple(part.casefold() for part in parts)
        return any(part in cls.PROTECTED_DIRECTORIES for part in lowered) or any(
            cls.is_sensitive_name(part) for part in parts
        )

    @staticmethod
    def normalize_virtual(path: str) -> str:
        if not isinstance(path, str) or not path or "\x00" in path:
            raise WorkspacePolicyError("path must be a non-empty string")
        if "\\" in path or ":" in path:
            raise WorkspacePolicyError("host paths are not accepted")
        candidate = PurePosixPath(path if path.startswith("/") else f"/{path}")
        if ".." in candidate.parts or any(part == "~" or part.startswith("~") for part in candidate.parts):
            raise WorkspacePolicyError("path traversal is blocked")
        return "/" + "/".join(part for part in candidate.parts if part != "/")

    def resolve(self, path: str, *, must_exist: bool) -> Path:
        virtual = self.normalize_virtual(path)
        parts = tuple(part for part in PurePosixPath(virtual).parts if part != "/")
        if self.is_protected_parts(parts):
            raise WorkspacePolicyError("secret-bearing or agent-state paths are blocked")
        candidate = self.root.joinpath(*parts)
        try:
            if must_exist:
                resolved = candidate.resolve(strict=True)
            else:
                parent = candidate.parent.resolve(strict=True)
                resolved = (parent / candidate.name).resolve(strict=False)
            relative = resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspacePolicyError("path escapes workspace or cannot be resolved") from exc
        if self.is_protected_parts(relative.parts):
            raise WorkspacePolicyError("resolved path enters a protected location")
        return resolved

    def can_surface(self, virtual_path: str) -> bool:
        try:
            self.resolve(virtual_path.rstrip("/") or "/", must_exist=True)
        except (WorkspacePolicyError, FileNotFoundError):
            return False
        return True
