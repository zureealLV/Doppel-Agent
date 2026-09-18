"""Conservative context estimates and structure-preserving compaction."""

from __future__ import annotations

import json
from dataclasses import replace

from ..provider import Message


class ContextPolicy:
    def __init__(self, limit_tokens: int = 32_000, warning_ratio: float = 0.8):
        if limit_tokens < 256 or not 0 < warning_ratio < 1:
            raise ValueError("invalid context policy")
        self.limit_tokens = limit_tokens
        self.warning_ratio = warning_ratio

    @staticmethod
    def estimate_tokens(messages: list[Message]) -> int:
        """UTF-8/4 estimate only; provider usage is the authoritative count."""
        encoded = json.dumps([message.to_api() for message in messages], ensure_ascii=False).encode("utf-8")
        return max(1, (len(encoded) + 3) // 4)

    def compact(self, messages: list[Message]) -> tuple[list[Message], dict]:
        before = self.estimate_tokens(messages)
        report = {"estimated_before": before, "estimated_after": before, "limit": self.limit_tokens, "compacted": False}
        if before < self.limit_tokens * self.warning_ratio:
            return messages, report
        if len(messages) < 3:
            if before >= self.limit_tokens:
                raise RuntimeError("context_budget_exceeded")
            return messages, report

        prefix = messages[:2]  # System instructions and original user goal must survive.
        groups: list[list[Message]] = []
        for message in messages[2:]:
            if message.role == "assistant":
                groups.append([message])
            elif groups:
                groups[-1].append(message)
            else:
                raise RuntimeError("invalid message history")
        dropped: list[list[Message]] = []
        while len(groups) > 2 and self.estimate_tokens(prefix + [item for group in groups for item in group]) >= self.limit_tokens * self.warning_ratio:
            dropped.append(groups.pop(0))
        note = ""
        if dropped:
            names = [call.name for group in dropped for call in group[0].tool_calls]
            note = f"Earlier completed tool exchanges were compacted ({len(dropped)} groups; tools: {', '.join(names[:20])}). Inspect files or task state again if needed."
        compacted = prefix + ([Message("system", note)] if note else []) + [item for group in groups for item in group]
        if self.estimate_tokens(compacted) >= self.limit_tokens * self.warning_ratio:
            compacted = [
                replace(message, content=message.content[:4000] + "\n[tool output truncated by context policy]")
                if message.role == "tool" and len(message.content) > 4000 else message
                for message in compacted
            ]
        after = self.estimate_tokens(compacted)
        if after >= self.limit_tokens:
            raise RuntimeError("context_budget_exceeded")
        report.update(estimated_after=after, compacted=after < before, dropped_groups=len(dropped))
        return compacted, report
