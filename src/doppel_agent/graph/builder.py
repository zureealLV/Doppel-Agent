"""Build the focused LangGraph without binding it to a web transport."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..concurrency.limits import ResourceLimits
from ..persistence.tool_ledger import ToolExecutionLedger
from ..provider import Provider
from ..tools import ToolRegistry
from .nodes import FocusedGraphNodes
from .routing import route_after_approval, route_after_reason
from .state import DoppelState


def build_focused_graph(
    provider: Provider,
    tools: ToolRegistry,
    *,
    checkpointer: Any = None,
    context_limit_tokens: int = 32_000,
    ledger: ToolExecutionLedger | None = None,
    resource_limits: ResourceLimits | None = None,
):
    nodes = FocusedGraphNodes(
        provider,
        tools,
        context_limit_tokens=context_limit_tokens,
        ledger=ledger,
        resource_limits=resource_limits,
    )
    builder = StateGraph(DoppelState)
    builder.add_node("reason", nodes.reason)
    builder.add_node("approval", nodes.request_approval)
    builder.add_node("tools", nodes.execute_tools)
    builder.add_edge(START, "reason")
    builder.add_conditional_edges(
        "reason",
        lambda state: route_after_reason(state, requires_approval=nodes.requires_approval),
        {"approval": "approval", "tools": "tools", "end": END},
    )
    builder.add_conditional_edges("approval", route_after_approval, {"reason": "reason", "tools": "tools"})
    builder.add_edge("tools", "reason")
    return builder.compile(checkpointer=checkpointer)
