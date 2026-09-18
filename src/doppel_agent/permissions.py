"""Fail-closed capability decisions; no interactive approval yet."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str


class PermissionManager:
    def __init__(self, allowed_capabilities: frozenset[str] = frozenset({"workspace_read"})):
        self.allowed_capabilities = allowed_capabilities

    def check(self, capability: str) -> PermissionDecision:
        if capability in self.allowed_capabilities:
            return PermissionDecision(True, "capability allowed")
        return PermissionDecision(False, f"capability denied: {capability}")
