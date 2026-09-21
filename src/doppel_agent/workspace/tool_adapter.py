"""LangChain adapter for Doppel's reviewed workspace patch tool."""

from __future__ import annotations

from pathlib import Path
from contextvars import ContextVar
import hashlib
import json
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from ..tools import Tool, patch_tool
from .verification import VerificationPipeline


_RUN_ID: ContextVar[str] = ContextVar("doppel_patch_run_id", default="deep")


def set_patch_run_id(run_id: str):
    return _RUN_ID.set(run_id)


def reset_patch_run_id(token: Any) -> None:
    _RUN_ID.reset(token)


class WorkspacePatchTool(BaseTool):
    """Expose patch proposals to Deep Agents without exposing direct writes."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "propose_patch"
    description: str = (
        "Propose complete UTF-8 contents for one or more workspace files. "
        "Doppel will show a unified diff and require approval before applying it. "
        "Provide only the changes field; internal review metadata is added by Doppel."
    )
    args_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            },
            "_doppel_patch": {"type": "object"},
        },
        "required": ["changes"],
        "additionalProperties": False,
    }
    doppel_tool: Tool = Field(exclude=True)

    def _run(self, **kwargs: Any) -> str:
        return self.doppel_tool.handler(kwargs)

    async def _arun(self, **kwargs: Any) -> str:
        if self.doppel_tool.async_handler is None:
            return self.doppel_tool.handler(kwargs)
        call_id = hashlib.sha256(
            json.dumps(kwargs, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:32]
        return await self.doppel_tool.async_handler(kwargs, _RUN_ID.get(), call_id)


def langchain_patch_tool(
    workspace: Path,
    *,
    verification: VerificationPipeline | None = None,
) -> WorkspacePatchTool:
    return WorkspacePatchTool(doppel_tool=patch_tool(workspace, verification=verification))
