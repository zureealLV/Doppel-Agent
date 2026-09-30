"""Fail-closed capability decisions with optional per-call approval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str


class PermissionManager:
    def __init__(
        self,
        allowed_capabilities: frozenset[str] = frozenset({"workspace_read"}),
        approver: Callable[[str, str, dict[str, Any]], bool] | None = None,
    ):
        self.allowed_capabilities = allowed_capabilities
        self.approver = approver

    def check(self, capability: str, tool_name: str = "", arguments: dict[str, Any] | None = None) -> PermissionDecision:
        if capability not in self.allowed_capabilities:
            return PermissionDecision(False, f"capability denied: {capability}")
        if capability in {"workspace_write", "command_execute", "mcp_execute"} and self.approver is not None:
            if not self.approver(capability, tool_name, arguments or {}):
                return PermissionDecision(False, "tool call was denied or approval timed out")
            return PermissionDecision(True, "approved for this tool call")
        return PermissionDecision(True, "capability allowed")
