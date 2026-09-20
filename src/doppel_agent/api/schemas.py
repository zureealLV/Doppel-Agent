"""Versioned HTTP request and response schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PermissionGrants(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_write: bool = False
    command_execute: bool = False
    mcp_execute: bool = False
    delegate: bool = False


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=100_000)
    mode: Literal["legacy", "graph"] = "graph"
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    effort: Literal["quick", "balanced", "deep"] = "balanced"
    deadline_seconds: int = Field(default=600, ge=1, le=3600)
    permissions: PermissionGrants = Field(default_factory=PermissionGrants)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "edit"]
    tool_calls: list[dict[str, Any]] | None = None


class RunAccepted(BaseModel):
    run_id: str
    thread_id: str
    status: str
    replayed: bool = False


class CancelResponse(BaseModel):
    run_id: str
    cancel_requested: bool
