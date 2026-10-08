"""LangChain adapter for Doppel's reviewed workspace patch tool."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId
from pydantic import BaseModel, ConfigDict, Field

from ..concurrency.limits import ResourceLimits
from ..permissions import PermissionManager
from ..persistence.tool_ledger import ToolExecutionLedger
from ..tools import Tool, ToolRegistry, patch_tool
from .verification import VerificationPipeline


_RUN_ID: ContextVar[str | None] = ContextVar("doppel_patch_run_id", default=None)
_SINK: ContextVar[Any] = ContextVar("doppel_patch_effect_sink", default=None)


def set_patch_run_id(run_id: str, sink: Any = None):
    return _RUN_ID.set(run_id), _SINK.set(sink)


def reset_patch_run_id(token: Any) -> None:
    run_token, sink_token = token
    _SINK.reset(sink_token)
    _RUN_ID.reset(run_token)


class PatchToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(strict=True)
    content: str = Field(strict=True)


class PatchToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: list[PatchToolChange] = Field(min_length=1, max_length=32)
    review: dict[str, Any] | None = Field(default=None, alias="_doppel_patch")
    tool_call_id: Annotated[str, InjectedToolCallId]


class WorkspacePatchTool(BaseTool):
    """Expose patch proposals to Deep Agents without exposing direct writes."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "propose_patch"
    description: str = (
        "Propose complete UTF-8 contents for one or more workspace files. "
        "Doppel will show a unified diff and require approval before applying it. "
        "Provide only the changes field; internal review metadata is added by Doppel."
    )
    args_schema: type[BaseModel] = PatchToolInput
    doppel_tool: Tool = Field(exclude=True)
    ledger: ToolExecutionLedger = Field(exclude=True)
    command_granted: bool = Field(default=False, exclude=True)

    @property
    def tool_call_schema(self) -> dict[str, Any]:
        # Provider only sees public changes. BaseTool validates the internal
        # edited HITL action against PatchToolInput and injects the actual ID.
        return {**self.doppel_tool.input_schema, "description": self.description}

    def _run(self, **kwargs: Any) -> str:
        raise RuntimeError("reviewed Deep patch requires owned async execution")

    async def _arun(self, changes: list[PatchToolChange], tool_call_id: str,
                    review: dict[str, Any] | None = None) -> str:
        run_id = _RUN_ID.get()
        if run_id is None or review is None:
            raise ValueError("patch_reviewed_run_scope_unavailable")
        arguments = {"changes": [change.model_dump() for change in changes], "_doppel_patch": review}
        capabilities = {"workspace_write", "command_execute"} if self.command_granted else {"workspace_write"}
        registry = ToolRegistry(PermissionManager(frozenset(capabilities)))
        registry.register(self.doppel_tool)

        async def receipt(output: str, replayed: bool, cancelled: bool) -> None:
            sink = _SINK.get()
            if sink is not None:
                await sink.emit("patch.applied", tool_call_id=tool_call_id, result=output,
                                replayed=replayed, cancel_requested=cancelled)

        return await registry.aexecute(self.name, arguments, run_id=run_id, tool_call_id=tool_call_id,
                                       ledger=self.ledger, on_effect=receipt)


def langchain_patch_tool(
    workspace: Path,
    *,
    verification: VerificationPipeline | None = None,
    ledger: ToolExecutionLedger | None = None,
    resource_limits: ResourceLimits | None = None,
    allow_command: bool = False,
    require_verification_review: bool = False,
) -> WorkspacePatchTool:
    return WorkspacePatchTool(doppel_tool=patch_tool(workspace, verification=verification, resource_limits=resource_limits,
                                                   require_verification_review=require_verification_review),
                              ledger=ledger or ToolExecutionLedger(workspace / ".doppel-agent" / "tool-executions.sqlite3"),
                              command_granted=allow_command)
