"""Load declarative SKILL.md files without executing them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
        skills = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            path = directory / "SKILL.md"
            if not path.is_file() or path.is_symlink() or path.stat().st_size > self.max_bytes:
                continue
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            if len(lines) < 4 or lines[0].strip() != "---":
                continue
            try:
                closing = lines.index("---", 1)
            except ValueError:
                continue
            metadata = {}
            for line in lines[1:closing]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip().strip('"\'')
            name = metadata.get("name", "")
            description = metadata.get("description", "")
            if not name or name != directory.name or not description:
                continue
            skills.append(Skill(name, description, "\n".join(lines[closing + 1:]).strip(), path))
        return skills

    def get(self, name: str) -> Skill:
        if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in name):
            raise ValueError("invalid skill name")
        for skill in self.list():
            if skill.name == name:
                return skill
        raise ValueError(f"unknown skill: {name}")
