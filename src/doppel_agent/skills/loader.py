"""Load declarative SKILL.md files without executing them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .registry import SkillRegistry
from .spec import SkillValidationError


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    content: str
    path: Path


class SkillLoader:
    def __init__(self, workspace: Path, max_bytes: int = 64 * 1024):
        self.root = (workspace.resolve(strict=True) / ".doppel" / "skills")
        self.max_bytes = max_bytes

    def list(self) -> list[Skill]:
        if not self.root.is_dir():
            return []
        # Compatibility API remains tolerant: one malformed skill must not
        # hide every valid legacy workspace skill.
        skills: list[Skill] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir():
                continue
            try:
                temporary = SkillRegistry(self.root.parents[1], max_bytes=self.max_bytes)
                spec = temporary._parse(directory)  # noqa: SLF001 - compatibility shim
            except (OSError, UnicodeError, SkillValidationError):
                continue
            skills.append(Skill(spec.name, spec.description, spec.instructions(), spec.path))
        return skills

    def get(self, name: str) -> Skill:
        if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in name):
            raise ValueError("invalid skill name")
        for skill in self.list():
            if skill.name == name:
                return skill
        raise ValueError(f"unknown skill: {name}")
