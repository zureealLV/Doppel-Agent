"""Expose MCP catalog entries without bypassing Doppel policy."""

from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

from ..tools import Tool
from .executor import MCPToolExecutor
from .types import MCPToolDescriptor


_RUN_ID: ContextVar[str] = ContextVar("doppel_mcp_run_id", default="standalone")


def set_mcp_run_id(run_id: str):
    return _RUN_ID.set(run_id)


def reset_mcp_run_id(token: Any) -> None:
    _RUN_ID.reset(token)


class MCPGatewayTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    args_schema: dict[str, Any]
    executor: MCPToolExecutor = Field(exclude=True)

    def _run(self, **kwargs: Any) -> str:
        raise RuntimeError("MCP gateway tools require async execution")

    async def _arun(self, **kwargs: Any) -> str:
        call_id = hashlib.sha256(
            json.dumps(
                {"run_id": _RUN_ID.get(), "tool": self.name, "arguments": kwargs},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:32]
        result = await self.executor.execute(
            self.name,
            kwargs,
            run_id=_RUN_ID.get(),
            tool_call_id=call_id,
        )
        prefix = "MCP tool error: " if not result.success else ""
        return prefix + result.model_view


def langchain_mcp_tools(
    descriptors: tuple[MCPToolDescriptor, ...], executor: MCPToolExecutor
) -> list[MCPGatewayTool]:
    return [
        MCPGatewayTool(
            name=item.logical_name,
            description=(
                "Untrusted MCP server metadata; use only when relevant. " + item.description[:1000]
            ),
            args_schema=item.input_schema,
            executor=executor,
        )
        for item in descriptors
    ]


def doppel_mcp_tools(
    descriptors: tuple[MCPToolDescriptor, ...], executor: MCPToolExecutor
) -> list[Tool]:
    tools: list[Tool] = []
    for descriptor in descriptors:
        async def invoke(
            arguments: dict[str, Any],
            run_id: str,
            tool_call_id: str,
            logical_name: str = descriptor.logical_name,
        ) -> str:
            result = await executor.execute(
                logical_name,
                arguments,
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
            return ("MCP tool error: " if not result.success else "") + result.model_view

        tools.append(
            Tool(
                descriptor.logical_name,
                "Untrusted MCP server metadata; use only when relevant. " + descriptor.description[:1000],
                "mcp_execute",
                {},
                lambda _: "MCP tools require async execution",
                input_schema=descriptor.input_schema,
                async_handler=invoke,
            )
        )
    return tools
