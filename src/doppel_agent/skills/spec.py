"""Validated Agent Skill metadata with lazy instruction loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class SkillValidationError(ValueError):
    """A SKILL.md file is unsafe or structurally invalid."""


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    path: Path
    root: Path
    line_count: int
    supporting_files: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()
    _body: str = field(default="", repr=False)

    def instructions(self) -> str:
        """Load the detailed body only after the skill has been selected."""
        return self._body

    def summary(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}
