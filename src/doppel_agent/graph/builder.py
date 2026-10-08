"""Build the focused LangGraph without binding it to a web transport."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ..concurrency.limits import ResourceLimits
from ..events import EventSink
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
    sink: EventSink | None = None,
):
    nodes = FocusedGraphNodes(
        provider,
        tools,
        context_limit_tokens=context_limit_tokens,
        ledger=ledger,
        resource_limits=resource_limits,
        sink=sink,
    )
    builder = StateGraph(DoppelState)
    def observed(name, operation):
        async def invoke(state):
            await nodes.sink.emit("graph.node_started", node=name)
            result = await operation(state)
            # An interrupted/cancelled node does not invent a finished event.
            await nodes.sink.emit("graph.node_finished", node=name)
            return result
        return invoke

    builder.add_node("reason", observed("reason", nodes.reason))
    builder.add_node("prepare_approval", observed("prepare_approval", nodes.prepare_approval))
    builder.add_node("approval", observed("approval", nodes.request_approval))
    builder.add_node("tools", observed("tools", nodes.execute_tools))
    builder.add_edge(START, "reason")
    builder.add_conditional_edges(
        "reason",
        lambda state: route_after_reason(state, requires_approval=nodes.requires_approval),
        {"approval": "prepare_approval", "tools": "tools", "end": END},
    )
    builder.add_edge("prepare_approval", "approval")
    builder.add_conditional_edges("approval", route_after_approval, {"reason": "reason", "tools": "tools"})
    builder.add_edge("tools", "reason")
    return builder.compile(checkpointer=checkpointer)
