"""Async compatibility adapter around the original synchronous Core."""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from pathlib import Path
from threading import Event
from typing import Any

from ..concurrency.limits import ResourceLimits
from ..core import Core
from ..loop import LoopContinuation, LoopInterrupted
from ..owned_async import await_durable
from ..persistence.legacy_review import LegacyReviewStore
from ..persistence.tool_ledger import ToolExecutionLedger
from ..provider import Provider, ToolCall
from ..tools import ToolRegistry
from ..workspace.process_supervisor import ProcessSupervisor
from .base import EventSink, NullEventSink, ResumeCommand, RunRequest, RuntimeResult
from .provider_recording import ProviderReceiptFault, receipt_sink, record_provider
from ..billing_tariff import validate_price_receipt


class LegacyRuntime:
    name = "legacy"
    supports_resume = False
    supports_cancel = False

    def __init__(
        self,
        workspace: Path,
        provider: Provider,
        *,
        state_root: Path | None = None,
        core_options: dict[str, Any] | None = None,
        reviewed: bool = False,
        require_verification_review: bool = False,
        resource_limits: ResourceLimits | None = None,
        process_supervisor: ProcessSupervisor | None = None,
        provider_receipt_fault: ProviderReceiptFault | None = None,
        billing_price_receipt: dict | None = None,
    ):
        if type(require_verification_review) is not bool:
            raise ValueError("verification_review_policy_invalid")
        self.require_verification_review = require_verification_review
        self._billing_price_receipt = None if billing_price_receipt is None else validate_price_receipt(billing_price_receipt)
        self.workspace = workspace
        self.provider = provider
        self.provider_receipt_fault = provider_receipt_fault if provider_receipt_fault is not None else ProviderReceiptFault()
        self.state_root = state_root
        self.core_options = dict(core_options or {})
        # Retain SAME original failed Core/Bridge/manager, not a new cleanup
        # worker or a Future result as proof that remote work was resolved.
        self._unresolved_mcp_cores: dict[int, Core] = {}
        self._unresolved_task_cores: dict[int, Core] = {}  # Exact original task SQL source, not SDK failure.
        self._unresolved_storage_cores: dict[int, Core] = {}  # Original file/iterator source, not SQL/SDK.
        self._unresolved_process_cores: dict[int, Core] = {}  # Original supervisor/worker/handle sources.
        self.reviewed = reviewed
        self.supports_resume = reviewed
        self.resource_limits = resource_limits
        self.process_supervisor = (process_supervisor if process_supervisor is not None else
                                   ProcessSupervisor(failure=self.provider_receipt_fault.mark_failed,
                                                     cleanup_failure=self.provider_receipt_fault.retain_cleanup))
        self.ledger = (ToolExecutionLedger((state_root or workspace / ".doppel-agent") / "tool-executions.sqlite3",
                                          failure=self.provider_receipt_fault.mark_failed,
                                          cleanup_failure=self.provider_receipt_fault.retain_cleanup)
                       if reviewed else None)
        self.reviews = LegacyReviewStore(self.ledger.database) if self.ledger is not None else None

    async def run(self, request: RunRequest, sink: EventSink | None = None) -> RuntimeResult:
        self.provider_receipt_fault.check()
        sink = sink or NullEventSink()
        await sink.emit(
            "runtime.started",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
        )
        if self.reviews is not None:
            await await_durable(asyncio.to_thread(self.reviews.ensure_new, request.run_id))
        return await self._invoke(request, sink)

    async def _execute_call(self, tools: ToolRegistry, call: ToolCall, run_id: str,
                            sink: EventSink, stopped: Event) -> str:
        assert self.ledger is not None
        if stopped.is_set():
            raise asyncio.CancelledError

        async def effect(output: str, replayed: bool, cancelled: bool) -> None:
            await sink.emit("patch.applied", tool_call_id=call.id, result=output,
                            replayed=replayed, cancel_requested=cancelled or stopped.is_set())
            if stopped.is_set():
                # The patch is settled; do not start post-effect verification
                # after cancellation was observed by the owning Core adapter.
                raise asyncio.CancelledError

        async def operation() -> str:
            if tools.owns_effect(call.name):
                return await tools.aexecute(call.name, call.arguments, run_id=run_id,
                                            tool_call_id=call.id, ledger=self.ledger, on_effect=effect)
            output, _ = await self.ledger.aexecute_once(
                run_id, call.id, call.name, call.arguments,
                lambda: tools.aexecute(call.name, call.arguments, run_id=run_id, tool_call_id=call.id),
            )
            return output

        # The specialized patch already owns its optional command slot. Generic
        # command calls use the same resource gate without reserving twice.
        if self.resource_limits is not None and tools.capability(call.name) == "command_execute":
            async with self.resource_limits.command():
                return await operation()
        return await operation()

    async def _invoke(self, request: RunRequest, sink: EventSink, *,
                      continuation: LoopContinuation | None = None,
                      resumed: tuple[str, str, dict[str, Any]] | None = None) -> RuntimeResult:
        self.provider_receipt_fault.check()
        loop, stopped = asyncio.get_running_loop(), Event()
        sink = receipt_sink(sink, self.provider_receipt_fault)
        # Core's original JSONL bus stays intact. New canonical call receipts
        # join the SAME original async sink from inside the owned Core worker.
        provider = record_provider(self.provider, sink, fault=self.provider_receipt_fault, engine="legacy",
                                   billing_price_receipt=self._billing_price_receipt)
        decision = resumed

        def check_cancelled() -> None:
            self.provider_receipt_fault.check()
            if stopped.is_set():
                raise asyncio.CancelledError
            original_check = self.core_options.get("check_cancelled")
            if original_check is not None:
                original_check()

        def execute(tools: ToolRegistry, call: ToolCall, frame: LoopContinuation) -> str:
            nonlocal decision
            check_cancelled()
            if (not isinstance(call.id, str) or not call.id or len(call.id) > 256 or "\x00" in call.id
                    or not isinstance(call.name, str) or not call.name or len(call.name) > 128
                    or "\x00" in call.name
                    or not isinstance(call.arguments, dict)):
                raise ValueError("legacy_review_invalid_call")
            if decision is not None and (continuation is None or call.id != continuation.pending_calls[0].id
                                         or call.name != continuation.pending_calls[0].name):
                raise RuntimeError("legacy_review_invalid_pending_boundary")
            if frame.step >= int(self.core_options.get("max_steps", 8)):
                raise RuntimeError("legacy_final_turn_tool_requested")
            try:
                capability = tools.capability(call.name)
            except ValueError:
                if decision is not None:
                    raise RuntimeError("legacy_review_invalid_pending_boundary") from None
                raise
            sensitive = capability in {"workspace_write", "command_execute", "mcp_execute"}
            if sensitive and capability in tools.permissions.allowed_capabilities:
                assert self.reviews is not None
                if decision is None:
                    arguments = dict(tools.prepare_approval(call.name, call.arguments))
                    # Keep the actual assistant/pending call objects consistent
                    # with the reviewed arguments before freezing continuation.
                    call.arguments.clear()
                    call.arguments.update(arguments)
                    interrupt_id = self.reviews.save(request.run_id, request.thread_id, request.prompt,
                                                     request.context_text, frame)
                    raise LoopInterrupted(interrupt_id, {"kind": "tool_approval", "tool_calls": [{
                        "id": call.id, "name": call.name, "arguments": call.arguments, "capability": capability,
                    }]})
                interrupt_id, raw, value = decision
                action = value["action"]
                approved = call.arguments
                if action == "edit":
                    changes = value.get("tool_calls")
                    if (not isinstance(changes, list) or len(changes) != 1 or not isinstance(changes[0], dict)
                            or set(changes[0]) != {"id", "name", "arguments"}
                            or changes[0]["id"] != call.id or changes[0]["name"] != call.name):
                        raise RuntimeError("legacy_review_edit_scope")
                    try:
                        approved = tools.prepare_approval_edit(call.name, changes[0]["arguments"], call.arguments)
                    except (TypeError, ValueError, OSError, PermissionError):
                        raise RuntimeError("legacy_review_edit_invalid") from None
                try:
                    self.reviews.consume(request.run_id, request.thread_id, interrupt_id, raw, value)
                except ValueError:
                    raise RuntimeError("legacy_review_scope_unavailable") from None
                decision = None
                if action == "reject":
                    raise PermissionError("rejected by user")
                if approved is not call.arguments:
                    call.arguments.clear()
                    call.arguments.update(approved)
            elif decision is not None:
                raise RuntimeError("legacy_review_invalid_pending_boundary")
            check_cancelled()
            future = asyncio.run_coroutine_threadsafe(self._execute_call(tools, call, request.run_id, sink, stopped), loop)
            try:
                return future.result()
            except FutureCancelledError:
                raise asyncio.CancelledError from None

        def run_core():
            check_cancelled()  # Before original Core/Bridge/source construction.
            options = dict(self.core_options)
            # Both direct compatibility and product reviewed Core share the
            # original owner fault. Post-approval checks must fence accepted
            # siblings too, without cancelling/replacing entered operations.
            options.update(check_cancelled=check_cancelled,
                           mcp_cleanup_failure=self.provider_receipt_fault.mark_failed,
                           mcp_execution_failure=self.provider_receipt_fault.mark_failed,
                           mcp_publication_failure=self.provider_receipt_fault.mark_failed,
                           task_failure=self.provider_receipt_fault.mark_failed,
                           task_cleanup_failure=self.provider_receipt_fault.retain_cleanup,
                           storage_failure=self.provider_receipt_fault.mark_failed,
                           storage_cleanup_failure=self.provider_receipt_fault.retain_cleanup,
                           process_supervisor=self.process_supervisor,
                           process_failure=self.provider_receipt_fault.mark_failed,
                           process_cleanup_failure=self.provider_receipt_fault.retain_cleanup)
            if self.reviewed:
                check_cancelled()
                # Product review is not bypassable through a synchronous
                # approver. The console/direct Core path keeps its old contract.
                options.update(reviewed_tools=True, tool_executor=execute, check_cancelled=check_cancelled,
                               approver=None, process_supervisor=self.process_supervisor,
                               resource_limits=self.resource_limits,
                               require_verification_review=self.require_verification_review)
            arguments = {"run_id": request.run_id, "history": list(request.history), "context_text": request.context_text}
            if continuation is not None:
                arguments["continuation"] = continuation
            core = Core(self.workspace, provider, state_root=self.state_root, **options)
            try:
                return core.run(request.prompt, **arguments)
            finally:
                if core.mcp_cleanup_failed or core.mcp_execution_failed or core.mcp_publication_failed:
                    self._unresolved_mcp_cores[id(core)] = core
                if core.task_persistence_failed:
                    self._unresolved_task_cores[id(core)] = core
                if core.storage_persistence_failed:
                    self._unresolved_storage_cores[id(core)] = core
                if core.process_cleanup_uncertain:
                    self._unresolved_process_cores[id(core)] = core

        worker = asyncio.create_task(asyncio.to_thread(run_core))
        # Core is synchronous and cannot be force-aborted safely. Retain its
        # lifetime despite caller cancellation, including repeated cancellation,
        # so RunService cannot release workspace ownership under a live worker.
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                if worker.cancelled():
                    raise
                cancelled = True
                stopped.set()
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            else:
                if cancelled:
                    raise asyncio.CancelledError
                break
        # Core/delegate tools may convert an exception to a failed result/tool
        # output; recording failure must still fence all subsequent provider IO.
        self.provider_receipt_fault.check()
        if decision is not None and result["status"] == "completed":
            raise RuntimeError("legacy_review_decision_not_consumed")
        runtime_result = RuntimeResult(
            run_id=result["run_id"],
            thread_id=request.thread_id,
            status=result["status"],
            answer=result["answer"],
            runtime=self.name,
            metadata=result.get("metadata", {}),
        )
        await sink.emit(
            "runtime.finished",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
            status=runtime_result.status,
        )
        return runtime_result

    async def resume(self, command: ResumeCommand, sink: EventSink | None = None) -> RuntimeResult:
        self.provider_receipt_fault.check()
        if self.reviews is None:
            raise RuntimeError("legacy runtime does not support resume")
        value = command.value
        if not isinstance(value, dict) or value.get("action") not in {"approve", "reject", "edit"}:
            raise ValueError("approval decision must be approve, reject, or edit")
        value = dict(value)
        if value["action"] != "edit" and value.get("tool_calls") is None:
            value.pop("tool_calls", None)  # ResumeRequest.model_dump includes its nullable default.
        expected = {"action", "_legacy_interrupt_id"} | ({"tool_calls"} if value["action"] == "edit" else set())
        if set(value) != expected or not isinstance(value.get("_legacy_interrupt_id"), str):
            raise ValueError("legacy_review_scope_unavailable")
        interrupt_id = value["_legacy_interrupt_id"]
        raw, payload, continuation = await await_durable(asyncio.to_thread(
            self.reviews.load, command.run_id, command.thread_id, interrupt_id,
        ))
        request = RunRequest(payload["prompt"], run_id=command.run_id, thread_id=command.thread_id,
                             context_text=payload["context_text"])
        sink = sink or NullEventSink()
        await sink.emit("runtime.resumed", runtime=self.name, run_id=command.run_id, thread_id=command.thread_id)
        return await self._invoke(request, sink, continuation=continuation, resumed=(interrupt_id, raw, value))

    async def cancel(self, run_id: str) -> None:
        raise RuntimeError("legacy runtime does not support cancellation")
