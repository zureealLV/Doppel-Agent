"""Read-only identity of the workspace already owned by RunService."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectIdentity:
    name: str
    path: str
    switching_available: bool = False


def current_project(workspace: Path) -> ProjectIdentity:
    """Never open settings, inspect Git, create state, or start another kernel.

    RunService supplies its validated resolved workspace. In particular, this
    query does not accept a browser-supplied path or resolve a changed symlink.
    Switching will require a separate owned drain/restart contract.
    """
    if not workspace.is_absolute():
        raise ValueError("project workspace must be absolute")
    return ProjectIdentity(name=workspace.name or workspace.anchor, path=str(workspace))
