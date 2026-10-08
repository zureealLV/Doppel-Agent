"""Focused LangGraph runtime with the existing Doppel tool boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.types import Command

from ..concurrency.limits import ResourceLimits
from ..graph import build_focused_graph
from ..graph.nodes import initial_messages
from ..permissions import PermissionManager
from ..persistence import sqlite_checkpointer
from ..persistence.tool_ledger import ToolExecutionLedger
from ..provider import Provider, bounded_run_retries
from ..tools import (
    ToolRegistry,
    list_files_tool,
    read_file_range_tool,
    read_file_tool,
    search_text_tool,
    workspace_map_tool,
)
from .base import EventSink, NullEventSink, ResumeCommand, RunRequest, RuntimeResult
from .provider_recording import ProviderReceiptFault, receipt_sink, record_provider
from ..billing_tariff import validate_price_receipt


def focused_read_tools(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry(PermissionManager(frozenset({"workspace_read"})))
    registry.register(workspace_map_tool(workspace))
    registry.register(search_text_tool(workspace))
    registry.register(read_file_range_tool(workspace))
    registry.register(read_file_tool(workspace))
    registry.register(list_files_tool(workspace))
    return registry


class GraphRuntime:
    name = "graph"
    supports_resume = True
    supports_cancel = False

    def __init__(
        self,
        workspace: Path,
        provider: Provider,
        *,
        tools: ToolRegistry | None = None,
        checkpoint_path: Path | None = None,
        max_steps: int = 8,
        context_limit_tokens: int = 32_000,
        resource_limits: ResourceLimits | None = None,
        provider_receipt_fault: ProviderReceiptFault | None = None,
        billing_price_receipt: dict | None = None,
    ):
        self._billing_price_receipt = None if billing_price_receipt is None else validate_price_receipt(billing_price_receipt)
        self.workspace = workspace.resolve(strict=True)
        self.provider = provider
        self.tools = tools or focused_read_tools(self.workspace)
        self.max_steps = max_steps
        self.context_limit_tokens = context_limit_tokens
        self.resource_limits = resource_limits
        self.provider_receipt_fault = provider_receipt_fault if provider_receipt_fault is not None else ProviderReceiptFault()
        self.checkpoint_path = checkpoint_path or self.workspace / ".doppel-agent" / "checkpoints.sqlite3"
        self.ledger = ToolExecutionLedger(self.checkpoint_path.with_name("tool-executions.sqlite3"),
                                          failure=self.provider_receipt_fault.mark_failed,
                                          cleanup_failure=self.provider_receipt_fault.retain_cleanup)

    def _build_graph(self, checkpointer: Any, sink: EventSink | None = None):
        if sink is not None:
            sink = receipt_sink(sink, self.provider_receipt_fault)
        return build_focused_graph(
            (self.provider if sink is None else record_provider(
                self.provider, sink, fault=self.provider_receipt_fault, engine="graph",
                billing_price_receipt=self._billing_price_receipt,
            )),
            self.tools,
            checkpointer=checkpointer,
            context_limit_tokens=self.context_limit_tokens,
            ledger=self.ledger,
            resource_limits=self.resource_limits,
            sink=sink,
        )

    @staticmethod
    def _result(run_id: str, thread_id: str, result: dict[str, Any]) -> RuntimeResult:
        interrupts = result.get("__interrupt__") or ()
        status = "interrupted" if interrupts else result.get("status", "failed")
        metadata: dict[str, Any] = {
            "step_count": result.get("step_count", 1),
            "error": result.get("error"),
        }
        if interrupts:
            metadata["interrupts"] = [
                {"id": getattr(item, "id", ""), "value": getattr(item, "value", None)} for item in interrupts
            ]
        return RuntimeResult(
            run_id=run_id,
            thread_id=thread_id,
            status=status,
            answer=result.get("answer", ""),
            runtime="graph",
            metadata=metadata,
        )

    @bounded_run_retries()
    async def run(self, request: RunRequest, sink: EventSink | None = None) -> RuntimeResult:
        sink = sink or NullEventSink()
        await sink.emit(
            "runtime.started",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
        )
        state = {
            "run_id": request.run_id,
            "thread_id": request.thread_id,
            "messages": initial_messages(request.input_prompt, request.history),
            "status": "running",
            "step_count": 1,
            "max_steps": self.max_steps,
            "answer": "",
            "error": None,
            "approval": None,
        }
        async with sqlite_checkpointer(self.checkpoint_path, failure=self.provider_receipt_fault.mark_failed,
                cleanup_failure=self.provider_receipt_fault.retain_cleanup,
                cleanup_check=self.provider_receipt_fault.check_cleanup) as checkpointer:
            graph = self._build_graph(checkpointer, sink)
            snapshot = await graph.aget_state({"configurable": {"thread_id": request.thread_id}})
            if snapshot.next:
                raise ValueError("checkpoint has pending work; resume or start a new thread")
            if snapshot.values.get("messages"):
                # Focused state uses replacement lists, not an add_messages reducer.
                # Continue authoritative checkpoint context exactly once, never replay
                # the conversation UI projection over it.
                state["messages"] = [*snapshot.values["messages"], {"role": "user", "content": request.input_prompt}]
            result = await graph.ainvoke(
                state,
                config={"configurable": {"thread_id": request.thread_id}},
            )
        runtime_result = self._result(request.run_id, request.thread_id, result)
        await sink.emit(
            "runtime.finished",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
            status=runtime_result.status,
        )
        return runtime_result

    async def state(self, thread_id: str) -> dict[str, Any]:
        """Read the last durable graph state without starting another run."""
        async with sqlite_checkpointer(self.checkpoint_path, failure=self.provider_receipt_fault.mark_failed,
                cleanup_failure=self.provider_receipt_fault.retain_cleanup,
                cleanup_check=self.provider_receipt_fault.check_cleanup) as checkpointer:
            graph = self._build_graph(checkpointer)
            snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
            return dict(snapshot.values) if snapshot.values else {}

    @bounded_run_retries()
    async def resume(self, command: ResumeCommand, sink: EventSink | None = None) -> RuntimeResult:
        sink = sink or NullEventSink()
        await sink.emit(
            "runtime.resumed",
            runtime=self.name,
            run_id=command.run_id,
            thread_id=command.thread_id,
        )
        async with sqlite_checkpointer(self.checkpoint_path, failure=self.provider_receipt_fault.mark_failed,
                cleanup_failure=self.provider_receipt_fault.retain_cleanup,
                cleanup_check=self.provider_receipt_fault.check_cleanup) as checkpointer:
            graph = self._build_graph(checkpointer, sink)
            result = await graph.ainvoke(
                Command(resume=command.value),
                config={"configurable": {"thread_id": command.thread_id}},
            )
        runtime_result = self._result(command.run_id, command.thread_id, result)
        await sink.emit(
            "runtime.finished",
            runtime=self.name,
            run_id=command.run_id,
            thread_id=command.thread_id,
            status=runtime_result.status,
        )
        return runtime_result

    async def cancel(self, run_id: str) -> None:
        raise RuntimeError("graph runtime cancellation is not enabled")
