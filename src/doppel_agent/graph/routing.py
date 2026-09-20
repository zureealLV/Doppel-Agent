"""Conditional routes for the focused graph."""

from __future__ import annotations

from typing import Literal

from .state import DoppelState


def route_after_reason(state: DoppelState, *, requires_approval) -> Literal["approval", "tools", "end"]:
    if state.get("status") in {"completed", "failed", "cancelled"}:
        return "end"
    messages = state.get("messages", [])
    if messages and messages[-1].get("tool_calls"):
        if requires_approval(state):
            return "approval"
        return "tools"
    return "end"


def route_after_approval(state: DoppelState) -> Literal["reason", "tools"]:
    if (state.get("approval") or {}).get("action") == "reject":
        return "reason"
    return "tools"
