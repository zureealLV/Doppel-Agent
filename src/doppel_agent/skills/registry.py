"""Strict Agent Skills registry with progressive disclosure."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .spec import SkillSpec, SkillValidationError


_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?(?!\$\{|<|YOUR_|REPLACE_)[^\s'\"]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if len(lines) < 4 or lines[0].strip() != "---":
        raise SkillValidationError("SKILL.md requires YAML-style frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise SkillValidationError("SKILL.md frontmatter is not closed") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillValidationError("frontmatter entries must use key: value")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, "\n".join(lines[closing + 1 :]).strip()


class SkillRegistry:
    def __init__(
        self,
        workspace: Path,
        *,
        max_bytes: int = 128 * 1024,
        max_lines: int = 500,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.max_bytes = max_bytes
        self.max_lines = max_lines
        self.roots = (self.workspace / "skills", self.workspace / ".doppel" / "skills")
        self._skills: dict[str, SkillSpec] | None = None
        self._warnings: list[str] = []

    @property
    def warnings(self) -> tuple[str, ...]:
        self._ensure_loaded()
        return tuple(self._warnings)

    def _parse(self, directory: Path) -> SkillSpec:
        path = directory / "SKILL.md"
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            raise SkillValidationError(f"{directory.name}: missing regular SKILL.md")
        if path.stat().st_size > self.max_bytes:
            raise SkillValidationError(f"{directory.name}: SKILL.md exceeds size limit")
        text = path.read_text(encoding="utf-8")
        for pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                raise SkillValidationError(f"{directory.name}: possible plaintext secret detected")
        metadata, body = _frontmatter(text)
        name = metadata.get("name", "")
        description = metadata.get("description", "")
        if not _NAME.fullmatch(name) or name != directory.name:
            raise SkillValidationError(f"{directory.name}: invalid or mismatched skill name")
        if not description or len(description) > 500:
            raise SkillValidationError(f"{name}: description must contain 1 to 500 characters")
        if not body:
            raise SkillValidationError(f"{name}: instructions are empty")
        supporting: list[Path] = []
        for target in _LINK.findall(body):
            target = target.split("#", 1)[0]
            if not target or "://" in target or target.startswith("#"):
                continue
            pure = PurePosixPath(target)
            if pure.is_absolute() or ".." in pure.parts:
                raise SkillValidationError(f"{name}: supporting file escapes skill root")
            resolved = (directory / Path(*pure.parts)).resolve(strict=False)
            try:
                resolved.relative_to(directory.resolve(strict=True))
            except ValueError as exc:
                raise SkillValidationError(f"{name}: supporting file escapes skill root") from exc
            supporting.append(resolved)
        lines = len(text.splitlines())
        warnings = () if lines < self.max_lines else (f"SKILL.md has {lines} lines; prefer < {self.max_lines}",)
        return SkillSpec(name, description, path, directory, lines, tuple(supporting), warnings, body)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9_\-\u4e00-\u9fff]+", text.casefold()) if len(token) > 1}

    def _ensure_loaded(self) -> None:
        if self._skills is not None:
            return
        skills: dict[str, SkillSpec] = {}
        warnings: list[str] = []
        for root in self.roots:
            if not root.is_dir() or root.is_symlink():
                continue
            for directory in sorted(root.iterdir()):
                if not directory.is_dir():
                    continue
                skill = self._parse(directory)
                if skill.name in skills:
                    raise SkillValidationError(f"duplicate skill name: {skill.name}")
                skills[skill.name] = skill
                warnings.extend(f"{skill.name}: {warning}" for warning in skill.warnings)
        ordered = list(skills.values())
        for index, left in enumerate(ordered):
            left_tokens = self._tokens(left.description)
            for right in ordered[index + 1 :]:
                right_tokens = self._tokens(right.description)
                union = left_tokens | right_tokens
                if union and len(left_tokens & right_tokens) / len(union) >= 0.75:
                    warnings.append(f"descriptions overlap: {left.name}, {right.name}")
        self._skills = skills
        self._warnings = warnings

    def catalog(self) -> tuple[dict[str, str], ...]:
        self._ensure_loaded()
        assert self._skills is not None
        return tuple(skill.summary() for skill in self._skills.values())

    def list(self) -> tuple[SkillSpec, ...]:
        self._ensure_loaded()
        assert self._skills is not None
        return tuple(self._skills.values())

    def get(self, name: str) -> SkillSpec:
        self._ensure_loaded()
        assert self._skills is not None
        try:
            return self._skills[name]
        except KeyError as exc:
            raise ValueError(f"unknown skill: {name}") from exc

    def deep_agent_sources(self) -> list[str]:
        """Return validated source roots as backend-virtual POSIX paths."""
        self._ensure_loaded()
        sources = []
        for root in self.roots:
            if root.is_dir() and any(path.parent.parent == root for path in (s.path for s in self.list())):
                sources.append("/" + root.relative_to(self.workspace).as_posix().rstrip("/") + "/")
        return sources
