"""Serializable state for the focused coding graph."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class SerializedMessage(TypedDict, total=False):
    role: str
    content: str
    tool_call_id: str
    tool_calls: list[dict[str, Any]]


class DoppelState(TypedDict, total=False):
    run_id: str
    thread_id: str
    messages: list[SerializedMessage]
    answer: str
    status: Literal["running", "completed", "failed", "interrupted", "cancelled"]
    step_count: int
    max_steps: int
    error: dict[str, Any] | None
    approval: dict[str, Any] | None
