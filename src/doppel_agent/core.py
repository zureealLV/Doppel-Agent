"""Core owns runs, storage, tools and the provider boundary."""

from __future__ import annotations

from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable
from uuid import uuid4

from .events import EventBus
from .loop import AgentLoop, LoopContinuation, LoopInterrupted, REVIEW_SYSTEM_PROMPT, SYSTEM_PROMPT
from .permissions import PermissionManager
from .provider import Message, MockProvider, Provider, ToolCall
from .storage import RunStore, LegacyStorageReadError
from .tools import (ToolRegistry, list_files_tool, read_file_range_tool, read_file_tool,
                    patch_tool, run_command_tool, search_text_tool, workspace_map_tool, write_file_tool)
from .workspace.process_supervisor import ProcessCleanupError, ProcessSupervisor
from .workspace.verification import VerificationPipeline
from .concurrency.limits import ResourceLimits
from .tasks.manager import TaskManager, TaskPersistenceError
from .tasks.tools import task_tools
from .skills.loader import SkillLoader
from .skills.tools import skill_tools
from .mcp_bridge import MCPBridge, mcp_tools
from .mcp.client_manager import MCPCleanupError, MCPInvocationError, MCPPublicationError
from .subagents import DelegateManager, delegate_tool


class Core:
    def __init__(
        self, workspace: Path, provider: Provider | None = None,
        *, allow_write: bool = False, allow_command: bool = False, allow_mcp: bool = False,
        allow_delegate: bool = False,
        approver: Callable[[str, str, dict[str, Any]], bool] | None = None,
        state_root: Path | None = None,
        max_steps: int = 8,
        review_mode: bool = False,
        reviewed_tools: bool = False,
        require_verification_review: bool = False,
        tool_executor: Callable[[ToolRegistry, ToolCall, LoopContinuation], str] | None = None,
        check_cancelled: Callable[[], None] | None = None,
        process_supervisor: ProcessSupervisor | None = None,
        resource_limits: ResourceLimits | None = None,
        mcp_cleanup_failure: Callable[[], None] | None = None,
        mcp_execution_failure: Callable[[], None] | None = None,
        mcp_publication_failure: Callable[[], None] | None = None,
        task_failure: Callable[[], None] | None = None,
        task_cleanup_failure: Callable[[TaskManager], None] | None = None,
        storage_failure: Callable[[], None] | None = None,
        storage_cleanup_failure: Callable[[RunStore], None] | None = None,
        process_failure: Callable[[], None] | None = None,
        process_cleanup_failure: Callable[[ProcessSupervisor], None] | None = None,
    ):
        if type(require_verification_review) is not bool:
            raise ValueError("verification_review_policy_invalid")
        self.require_verification_review = require_verification_review
        self.workspace = workspace.resolve(strict=True)
        self.provider = provider or MockProvider()
        self.state_root = (state_root or (self.workspace / ".doppel-agent")).resolve()
        self._storage_failure, self._storage_cleanup_failure = storage_failure, storage_cleanup_failure
        self._storage_failed = Event()
        self._storage_cleanup_uncertain = Event()
        self._storage_lifetime_lock = Lock()
        self._unresolved_storage_sources: dict[int, RunStore] = {}
        # Core's SAME separate original file store, hooks BEFORE constructor.
        self.store = RunStore(self.state_root, failure=self._mark_storage_persistence_failed,
                              cleanup_failure=self._retain_storage_cleanup)
        self.max_steps = max_steps
        self.review_mode = review_mode
        self.reviewed_tools, self.tool_executor = reviewed_tools, tool_executor
        self.check_cancelled = check_cancelled
        self._process_failure, self._process_cleanup_failure = process_failure, process_cleanup_failure
        self._process_failed = Event()
        self._process_cleanup_uncertain = Event()
        self._process_lifetime_lock = Lock()
        self._unresolved_process_sources: dict[int, ProcessSupervisor] = {}
        # Default command AND verification tools share ONE original supervisor.
        # Hooks exist before its constructor; injected service hooks are not replaced.
        self.process_supervisor = (process_supervisor if process_supervisor is not None else
                                   ProcessSupervisor(failure=self._mark_process_failed,
                                                     cleanup_failure=self._retain_process_cleanup))
        self.resource_limits = resource_limits
        self._mcp_cleanup_failure = mcp_cleanup_failure
        self._mcp_execution_failure = mcp_execution_failure
        self._mcp_publication_failure = mcp_publication_failure
        self._mcp_bridge: MCPBridge | None = None
        self._task_failure, self._task_cleanup_failure = task_failure, task_cleanup_failure
        self._task_failed = Event()
        self._task_cleanup_uncertain = Event()
        self._task_lifetime_lock = Lock()
        self._task_manager: TaskManager | None = None
        self._unresolved_task_sources: dict[int, TaskManager] = {}
        capabilities = {"workspace_read"} if review_mode else {"workspace_read", "task_manage"}
        if allow_write and not review_mode:
            capabilities.add("workspace_write")
        if allow_command and not review_mode:
            capabilities.add("command_execute")
        if allow_mcp and not review_mode:
            capabilities.add("mcp_execute")
        if allow_delegate and not review_mode:
            capabilities.add("delegate_readonly")
        self.allowed_capabilities = frozenset(capabilities)
        self.approver = approver

    @property
    def mcp_cleanup_failed(self) -> bool:
        return self._mcp_bridge is not None and self._mcp_bridge.cleanup_failed

    @property
    def mcp_execution_failed(self) -> bool:
        return self._mcp_bridge is not None and self._mcp_bridge.execution_failed

    @property
    def mcp_publication_failed(self) -> bool:
        return self._mcp_bridge is not None and self._mcp_bridge.publication_failed

    @property
    def task_persistence_failed(self) -> bool:
        return self._task_failed.is_set()

    @property
    def task_cleanup_uncertain(self) -> bool:
        return self._task_cleanup_uncertain.is_set()

    def _mark_task_persistence_failed(self) -> None:
        self._task_failed.set()
        if self._task_failure is not None:
            try:
                self._task_failure()  # SAME original owner, before SQL close/publication, no IO.
            except BaseException:
                pass

    def _retain_task_cleanup(self, source: TaskManager) -> None:
        self._task_failed.set()
        self._task_cleanup_uncertain.set()
        with self._task_lifetime_lock:
            self._unresolved_task_sources[id(source)] = source
        if self._task_cleanup_failure is not None:
            try:
                self._task_cleanup_failure(source)  # Exact source before original worker/constructor returns.
            except BaseException:
                pass

    @property
    def storage_persistence_failed(self) -> bool:
        return self._storage_failed.is_set()

    @property
    def storage_cleanup_uncertain(self) -> bool:
        return self._storage_cleanup_uncertain.is_set()

    def _mark_storage_persistence_failed(self) -> None:
        self._storage_failed.set()
        if self._storage_failure is not None:
            try:
                self._storage_failure()  # SAME original owner, before stream/iterator close.
            except BaseException:
                pass

    def _retain_storage_cleanup(self, source: RunStore) -> None:
        self._storage_failed.set()
        self._storage_cleanup_uncertain.set()
        with self._storage_lifetime_lock:
            self._unresolved_storage_sources[id(source)] = source
        if self._storage_cleanup_failure is not None:
            try:
                self._storage_cleanup_failure(source)  # Exact failed original source, no IO/replacement.
            except BaseException:
                pass

    @property
    def process_failed(self) -> bool:
        return self._process_failed.is_set() or self.process_supervisor.cleanup_failed

    @property
    def process_cleanup_uncertain(self) -> bool:
        return self._process_cleanup_uncertain.is_set() or self.process_supervisor.cleanup_failed

    def _mark_process_failed(self) -> None:
        self._process_failed.set()
        if self._process_failure is not None:
            try:
                self._process_failure()  # Original owner latch only, no worker/API/cleanup retry.
            except BaseException:
                pass

    def _retain_process_cleanup(self, source: ProcessSupervisor) -> None:
        self._mark_process_failed()
        self._process_cleanup_uncertain.set()
        with self._process_lifetime_lock:
            self._unresolved_process_sources[id(source)] = source
        if self._process_cleanup_failure is not None:
            try:
                self._process_cleanup_failure(source)  # Exact supervisor contains independent original sources.
            except BaseException:
                pass

    def _check_original_admission(self) -> None:
        if self.process_failed:
            self._retain_process_cleanup(self.process_supervisor)
            raise ProcessCleanupError('process cleanup quarantine', source=self.process_supervisor)
        if self.storage_persistence_failed:
            raise LegacyStorageReadError(self.store)
        if self.task_persistence_failed:
            raise TaskPersistenceError()
        if self.check_cancelled is not None:
            self.check_cancelled()

    def run(self, prompt: str, *, run_id: str | None = None, history: list[Message] | None = None,
            context_text: str = "", continuation: LoopContinuation | None = None) -> dict[str, Any]:
        from .context.input import compose_prompt

        if self.mcp_cleanup_failed:
            raise MCPCleanupError()  # Do not replace retained failed bridge/manager.
        if self.mcp_execution_failed:
            raise MCPInvocationError()
        if self.mcp_publication_failed:
            raise MCPPublicationError()
        self._check_original_admission()  # Before same task/store/provider/effect setup, no retry/reset.
        if not prompt or len(prompt) > 100_000:
            raise ValueError("prompt must contain 1 to 100000 characters")
        input_prompt = compose_prompt(prompt, context_text)
        run_id = run_id or uuid4().hex
        bus = EventBus(run_id, lambda event: self.store.append_event(run_id, event))
        mcp_bridge: MCPBridge | None = None

        def request_approval(capability: str, name: str, arguments: dict[str, Any]) -> bool:
            visible_arguments = mcp_bridge.approval_context(arguments) if capability == "mcp_execute" and mcp_bridge else arguments
            bus.emit("approval_requested", capability=capability, tool=name, arguments=visible_arguments)
            allowed = bool(self.approver and self.approver(capability, name, visible_arguments))
            bus.emit("approval_decided", capability=capability, tool=name, allowed=allowed)
            if allowed:
                # Original approval may have waited while shared owner faulted.
                # Consent event is not effect admission; recheck before execution.
                self._check_original_admission()
            return allowed

        permissions = PermissionManager(
            self.allowed_capabilities,
            request_approval if self.approver is not None else None,
        )
        tools = ToolRegistry(permissions)
        tools.register(workspace_map_tool(self.workspace))
        tools.register(search_text_tool(self.workspace))
        tools.register(read_file_range_tool(self.workspace))
        if not self.review_mode:
            tools.register(read_file_tool(self.workspace))
            tools.register(list_files_tool(self.workspace))
            if self.reviewed_tools:
                if "workspace_write" in self.allowed_capabilities:
                    verification = (VerificationPipeline(self.workspace, supervisor=self.process_supervisor)
                                    if not self.require_verification_review and "command_execute" in self.allowed_capabilities else None)
                    tools.register(patch_tool(self.workspace, verification=verification, resource_limits=self.resource_limits,
                                              require_verification_review=self.require_verification_review))
            else:
                tools.register(write_file_tool(self.workspace))
            tools.register(run_command_tool(self.workspace, supervisor=self.process_supervisor))
        if not self.review_mode:
            self._check_original_admission()
            manager = TaskManager(self.state_root / "tasks.sqlite3", failure=self._mark_task_persistence_failed,
                                  cleanup_failure=self._retain_task_cleanup)
            self._task_manager = manager  # Only actual original constructor return publishes this field.
            for tool in task_tools(manager, run_id):
                tools.register(tool)
            for tool in skill_tools(SkillLoader(self.workspace)):
                tools.register(tool)
            if "delegate_readonly" in self.allowed_capabilities:
                tools.register(delegate_tool(DelegateManager(self.workspace, self.provider, bus)))
        status = "completed"
        metadata: dict[str, Any] = {}
        try:
            if "mcp_execute" in self.allowed_capabilities:
                mcp_bridge = MCPBridge(self.workspace, failure=self._mcp_cleanup_failure,
                                       execution_failure=self._mcp_execution_failure,
                                       publication_failure=self._mcp_publication_failure)
                self._mcp_bridge = mcp_bridge
                for tool in mcp_tools(mcp_bridge):
                    tools.register(tool)
            answer = AgentLoop(
                self.provider, tools, bus, max_steps=self.max_steps,
                system_prompt=REVIEW_SYSTEM_PROMPT if self.review_mode else SYSTEM_PROMPT,
            ).run(input_prompt, history, continuation=continuation, tool_executor=self.tool_executor,
                  check_cancelled=self._check_original_admission)
        except LoopInterrupted as exc:
            status, answer = "interrupted", ""
            metadata["interrupts"] = [{"id": exc.interrupt_id, "value": exc.value}]
        except Exception as exc:
            status = "failed"
            answer = f"{type(exc).__name__}: run execution failed" if self.reviewed_tools else f"{type(exc).__name__}: {exc}"
            bus.emit("run_failed", reason=answer)
        self.store.write_session(run_id, {"run_id": run_id, "prompt": prompt, "status": status, "answer": answer})
        result: dict[str, Any] = {"run_id": run_id, "status": status, "answer": answer}
        if self.reviewed_tools:
            result["metadata"] = metadata
        return result
