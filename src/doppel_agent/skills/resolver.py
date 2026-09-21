"""Deterministic low-cost skill candidate resolution."""

from __future__ import annotations

import re

from .registry import SkillRegistry
from .spec import SkillSpec


class SkillResolver:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = text.casefold()
        words = set(re.findall(r"[a-z0-9_\-]+", lowered))
        chinese = {lowered[index : index + 2] for index in range(max(0, len(lowered) - 1)) if "\u4e00" <= lowered[index] <= "\u9fff"}
        return {token for token in words | chinese if token}

    def resolve(self, prompt: str, *, limit: int = 3) -> tuple[SkillSpec, ...]:
        if limit < 1:
            return ()
        prompt_tokens = self._tokens(prompt)
        explicit = {
            match.group(1)
            for match in re.finditer(r"(?:\$|skill:)([a-z0-9][a-z0-9_-]{0,63})", prompt.casefold())
        }
        ranked: list[tuple[float, SkillSpec]] = []
        for skill in self.registry.list():
            tokens = self._tokens(f"{skill.name} {skill.description}")
            overlap = len(tokens & prompt_tokens)
            score = (1000 if skill.name in explicit else 0) + overlap / max(1, len(tokens))
            if score > 0:
                ranked.append((score, skill))
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        return tuple(skill for _, skill in ranked[:limit])
