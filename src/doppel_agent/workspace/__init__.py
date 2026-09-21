"""Workspace policy shared by tools and third-party agent backends."""

from .service import WorkspacePolicyError, WorkspaceService

__all__ = ["WorkspacePolicyError", "WorkspaceService"]
