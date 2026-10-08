"""Versioned HTTP request and response schemas."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PermissionGrants(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_write: bool = False
    command_execute: bool = False
    mcp_execute: bool = False
    delegate: bool = False


class SelectedNoteRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    manifest_id: str | None = Field(pattern=r"^[0-9a-f]{32}$")
    notes: list[SelectedNoteRef] = Field(max_length=16)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    prompt: str = Field(min_length=1, max_length=100_000)
    mode: Literal["legacy", "graph", "deep"] = "graph"
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    effort: Literal["quick", "balanced", "deep"] = "balanced"
    deadline_seconds: int = Field(default=600, ge=1, le=3600)
    permissions: PermissionGrants = Field(default_factory=PermissionGrants)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    context: RunContext | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "edit"]
    tool_calls: list[dict[str, Any]] | None = None


class RunAccepted(BaseModel):
    run_id: str
    conversation_id: str | None = None
    thread_id: str
    status: str
    replayed: bool = False


class CancelResponse(BaseModel):
    run_id: str
    cancel_requested: bool


class SubagentPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)


class SubagentRecord(BaseModel):
    subagent_id: str
    parent_run_id: str
    status: str
    prompt: str
    history: list[dict[str, str]]
    answer: str
    error: str
    generation: int
    created_at: str
    updated_at: str


class SubagentCancelResponse(BaseModel):
    subagent_id: str
    cancel_requested: bool


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["legacy", "graph", "deep"] = "graph"
    title: str = Field(default="新对话", min_length=1, max_length=80)
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    draft: bool = False


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=80)
    archived: bool | None = None
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    group_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class GroupWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class WorkspaceSelectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class WorkOrderTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=100_000)
    dependencies: list[str] = Field(default_factory=list, max_length=64)
    access: Literal["read", "write"] = "read"
    mode: Literal["legacy", "graph", "deep"] = "graph"
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class WorkOrderPlanWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    tasks: list[WorkOrderTaskSpec] = Field(min_length=1, max_length=64)

    def to_plan(self):
        from ..tasks.work_orders import plan_from_payload
        return plan_from_payload(self.model_dump())


class WorkOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan: WorkOrderPlanWrite
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


class WorkOrderPlanReplace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    plan: WorkOrderPlanWrite
    context: RunContext | None = None


class WorkOrderExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    effort: Literal["quick", "balanced", "deep"] = "balanced"
    deadline_seconds: int = Field(default=600, ge=1, le=3600)
    context: RunContext | None = None
    # Strict booleans: JSON "false" or 1 cannot silently grant capabilities.
    permissions: dict[Literal["workspace_write", "command_execute", "mcp_execute", "delegate"], bool] = Field(default_factory=dict)


class WorkOrderActivate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    settings: WorkOrderExecutionSettings = Field(default_factory=WorkOrderExecutionSettings)


class WorkOrderControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    action: Literal["pause", "resume", "cancel"]


class WorkOrderRetry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    expected_attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class WorkOrderSelectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    work_order_id: str | None = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ContextFileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=2048)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    expected_file_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ContextPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    files: list[ContextFileSpec] = Field(min_length=1, max_length=24)
    budget_bytes: int = Field(default=65536, ge=256, le=65536)


class ContextAccept(ContextPreview):
    confirmed: bool
    idempotency_key: str = Field(min_length=8, max_length=128)


class NoteUserSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["user"]
    label: str = Field(min_length=1, max_length=200)


class NoteFileSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["file"]
    manifest_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    index: int = Field(ge=0, lt=24)


class NoteRunSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["run"]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class NotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["fact", "constraint", "decision"]
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=16000)
    scope_work_order_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    sources: list[Annotated[NoteUserSource | NoteFileSource | NoteRunSource, Field(discriminator="kind")]] = Field(min_length=1, max_length=8)


class NoteConfirmedWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: NotePayload
    confirmed: bool
    idempotency_key: str = Field(min_length=8, max_length=128)


class NoteUpdate(NoteConfirmedWrite):
    expected_revision: int = Field(ge=1)


class NoteDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ContextSelectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scope_work_order_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    manifest_id: str | None = Field(pattern=r"^[0-9a-f]{32}$")
    notes: list[SelectedNoteRef] = Field(max_length=16)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


ChangeIdentifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
ChangeHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ChangeToolCall = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\x00]+$")]
ChangeCommandName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00]+$")]


class ChangesDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=4096)
    plane: Literal["staged", "worktree", "combined"] = "worktree"
    expected_fingerprint: ChangeHash
    conflict_stage: Literal[1, 2, 3] | None = None

    @field_validator("conflict_stage", mode="before")
    @classmethod
    def exact_stage(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("invalid_git_diff_selection")
        return value


class ChangesConfirmed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool


class InversePrepare(ChangesConfirmed):
    source_tool_call_id: ChangeToolCall
    source_patch_id: ChangeIdentifier
    operation_id: ChangeIdentifier
    workspace_write: bool = False


class InverseDecision(ChangesConfirmed):
    patch_id: ChangeIdentifier
    action: Literal["approve", "reject"]
    workspace_write: bool = False


class VerificationPrepare(InversePrepare):
    command_execute: bool = False
    names: list[ChangeCommandName] | None = Field(default=None, min_length=1, max_length=16)

    @field_validator("names")
    @classmethod
    def bounded_names(cls, value):
        if value is not None and (len(set(value)) != len(value) or any(len(name.encode("utf-8")) > 128 for name in value)):
            raise ValueError("invalid_verification_selection")
        return value


class VerificationDecision(ChangesConfirmed):
    plan_id: ChangeHash
    action: Literal["approve", "reject"]
    command_execute: bool = False
    workspace_write: bool = False


class VerificationCancel(ChangesConfirmed):
    plan_id: ChangeHash
