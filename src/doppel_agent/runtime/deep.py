"""Optional Deep Agents runtime hosted inside Doppel's policy boundary."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage
from langchain.agents.middleware import ModelCallLimitMiddleware
from langgraph.types import Command

from ..concurrency.limits import ResourceLimits
from ..persistence import sqlite_checkpointer
from ..provider import Provider
from ..skills.registry import SkillRegistry
from ..mcp.tool_adapter import reset_mcp_run_id, set_mcp_run_id
from ..workspace.process_supervisor import ProcessSupervisor
from ..workspace.tool_adapter import (
    WorkspacePatchTool,
    langchain_patch_tool,
    reset_patch_run_id,
    set_patch_run_id,
)
from ..workspace.verification import VerificationPipeline
from .base import EventSink, NullEventSink, ResumeCommand, RunRequest, RuntimeResult
from .deep_backend import DoppelBackend
from .deep_model import DoppelChatModel
from .graph import GraphRuntime


register_harness_profile(
    "doppel",
    HarnessProfile(
        excluded_tools=frozenset({"execute", "write_file", "edit_file"}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)


class _DeepEventBridge(AsyncCallbackHandler):
    def __init__(self, sink: EventSink):
        self.sink = sink
        self._tool_names: dict[str, str] = {}

    async def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        name = serialized.get("name") or "unknown"
        callback_id = str(kwargs.get("run_id", ""))
        self._tool_names[callback_id] = name
        if name == "task":
            kind = "deep.subagent_started"
        elif name.startswith("mcp__"):
            kind = "deep.mcp_tool_started"
        elif name == "read_file" and "SKILL.md" in input_str:
            kind = "deep.skill_loaded"
        else:
            kind = "deep.tool_started"
        await self.sink.emit(kind, tool=name, input_preview=input_str[:500])

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        name = self._tool_names.pop(str(kwargs.get("run_id", "")), "unknown")
        kind = "deep.subagent_finished" if name == "task" else "deep.tool_finished"
        await self.sink.emit(kind, tool=name, output_preview=str(output)[:500])
        if name == "propose_patch":
            await self.sink.emit("patch.applied", result=str(output)[:2000])

    async def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        await self.sink.emit("deep.tool_failed", error=f"{type(error).__name__}: {error}")
        if type(error).__name__ == "PatchConflictError":
            await self.sink.emit("patch.conflict", error=str(error))

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {})
        await self.sink.emit("deep.model_finished", usage=usage)


class DeepAgentRuntime:
    name = "deep"
    supports_resume = True
    supports_cancel = True

    def __init__(
        self,
        workspace: Path,
        provider: Provider,
        *,
        checkpoint_path: Path | None = None,
        max_steps: int = 12,
        allow_write: bool = False,
        allow_command: bool = False,
        max_subagents: int = 2,
        resource_limits: ResourceLimits | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.provider = provider
        self.max_steps = max_steps
        self.allow_write = allow_write
        self.max_subagents = max(0, min(max_subagents, 2))
        self.resource_limits = resource_limits
        root = checkpoint_path or self.workspace / ".doppel-agent" / "deep-checkpoints.sqlite3"
        self.checkpoint_path = root
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self.additional_tools: list[Any] = []
        self.process_supervisor = ProcessSupervisor()
        verification = (
            VerificationPipeline(self.workspace, supervisor=self.process_supervisor)
            if allow_write and allow_command
            else None
        )
        self.patch_tool: WorkspacePatchTool | None = (
            langchain_patch_tool(self.workspace, verification=verification) if allow_write else None
        )
        self._pending_interrupts: dict[str, list[Any]] = {}
        self._fallback = GraphRuntime(
            self.workspace,
            provider,
            checkpoint_path=root.with_name("focused-fallback.sqlite3"),
            max_steps=min(max_steps, 8),
            resource_limits=resource_limits,
        )

    def _skill_sources(self) -> list[str]:
        registry = SkillRegistry(self.workspace)
        return registry.deep_agent_sources()

    def _permissions(self) -> list[FilesystemPermission]:
        protected = [
            "/**/.git/**",
            "/**/.doppel-agent/**",
            "/**/.env",
            "/**/.env.*",
            "/**/credentials.json",
            "/**/*.pem",
            "/**/*.key",
        ]
        rules = [
            FilesystemPermission(operations=["read", "write"], paths=protected, mode="deny")
        ]
        if self.allow_write:
            rules.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="interrupt"))
        else:
            rules.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
        return rules

    def _subagents(self) -> list[dict[str, Any]]:
        specs = [
            {
                "name": "general-purpose",
                "description": "Read-only code investigator. Return conclusions with workspace evidence paths.",
                "system_prompt": (
                    "Investigate only; never modify files or delegate. Return concise findings and cite every "
                    "claim with a workspace-relative evidence path."
                ),
                "model": DoppelChatModel(provider=self.provider, model_label="doppel-investigator", token_budget=8000),
                "tools": [],
                "middleware": [ModelCallLimitMiddleware(run_limit=6, exit_behavior="end")],
                "permissions": [
                    FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")
                ],
            },
            {
                "name": "verifier",
                "description": "Read-only verifier for tests, claims, and acceptance evidence.",
                "system_prompt": (
                    "Verify the proposed conclusion against files. Do not modify files or delegate. "
                    "Return evidence paths and explicitly state uncertainty."
                ),
                "model": DoppelChatModel(provider=self.provider, model_label="doppel-verifier", token_budget=8000),
                "tools": [],
                "middleware": [ModelCallLimitMiddleware(run_limit=6, exit_behavior="end")],
                "permissions": [
                    FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")
                ],
            },
        ]
        return specs[: self.max_subagents]

    def _build_graph(self, checkpointer: Any, sink: EventSink):
        model = DoppelChatModel(provider=self.provider)
        backend = DoppelBackend(
            self.workspace,
            allow_write=self.allow_write,
            audit=lambda operation, payload: None,
        )
        tools = ([self.patch_tool] if self.patch_tool is not None else []) + self.additional_tools
        interrupt_on = {tool.name: True for tool in tools}
        return create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=(
                "You are Doppel deep mode. Use progressive disclosure, keep work inside the workspace, "
                "delegate at most two read-only investigations, and ground the final answer in evidence paths. "
                "For workspace changes, use propose_patch and provide only its changes field."
            ),
            subagents=self._subagents(),
            skills=self._skill_sources() or None,
            permissions=self._permissions(),
            backend=backend,
            interrupt_on=interrupt_on or None,
            middleware=[ModelCallLimitMiddleware(run_limit=self.max_steps, exit_behavior="end")],
            checkpointer=checkpointer,
            name="doppel-deep",
        )

    @staticmethod
    def _answer(result: dict[str, Any]) -> str:
        for message in reversed(result.get("messages") or []):
            if isinstance(message, AIMessage):
                if isinstance(message.content, str):
                    return message.content
                return str(message.content)
        return ""

    def _prepare_interrupt_value(self, value: Any) -> Any:
        if not isinstance(value, dict) or self.patch_tool is None:
            return value
        prepared = deepcopy(value)
        for request in prepared.get("action_requests") or []:
            if request.get("name") != self.patch_tool.name:
                continue
            arguments = request.get("args")
            if not isinstance(arguments, dict):
                raise ValueError("propose_patch approval arguments must be an object")
            request["args"] = self.patch_tool.doppel_tool.approval_preparer(arguments)
        return prepared

    def _result(self, run_id: str, thread_id: str, result: dict[str, Any]) -> RuntimeResult:
        interrupts = result.get("__interrupt__") or ()
        metadata: dict[str, Any] = {
            "message_count": len(result.get("messages") or []),
            "subagent_limit": self.max_subagents,
        }
        if interrupts:
            values = [self._prepare_interrupt_value(getattr(item, "value", None)) for item in interrupts]
            self._pending_interrupts[thread_id] = values
            metadata["interrupts"] = [
                {"id": getattr(item, "id", ""), "value": value}
                for item, value in zip(interrupts, values, strict=True)
            ]
        else:
            self._pending_interrupts.pop(thread_id, None)
        return RuntimeResult(
            run_id=run_id,
            thread_id=thread_id,
            status="interrupted" if interrupts else "completed",
            answer=DeepAgentRuntime._answer(result),
            runtime="deep",
            metadata=metadata,
        )

    async def _invoke(self, request: RunRequest, sink: EventSink) -> RuntimeResult:
        async with sqlite_checkpointer(self.checkpoint_path) as checkpointer:
            graph = self._build_graph(checkpointer, sink)
            token = set_mcp_run_id(request.run_id)
            patch_token = set_patch_run_id(request.run_id)
            try:
                result = await graph.ainvoke(
                    {"messages": [{"role": "user", "content": request.prompt}]},
                    config={
                        "configurable": {"thread_id": request.thread_id},
                        "recursion_limit": max(40, self.max_steps * 8),
                        "callbacks": [_DeepEventBridge(sink)],
                    },
                )
            finally:
                reset_patch_run_id(patch_token)
                reset_mcp_run_id(token)
        return self._result(request.run_id, request.thread_id, result)

    async def run(self, request: RunRequest, sink: EventSink | None = None) -> RuntimeResult:
        sink = sink or NullEventSink()
        await sink.emit("runtime.started", runtime=self.name, run_id=request.run_id, thread_id=request.thread_id)
        task = asyncio.current_task()
        if task is not None:
            self._tasks[request.run_id] = task
        try:
            result = await self._invoke(request, sink)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - deep mode must degrade predictably
            await sink.emit("deep.fallback", error=f"{type(exc).__name__}: {exc}", runtime="graph")
            focused = await self._fallback.run(request, sink)
            result = RuntimeResult(
                run_id=focused.run_id,
                thread_id=focused.thread_id,
                status=focused.status,
                answer=focused.answer,
                runtime="deep",
                metadata={**focused.metadata, "fallback_runtime": "graph", "deep_error": str(exc)},
            )
        finally:
            self._tasks.pop(request.run_id, None)
        for item in result.metadata.get("interrupts", []):
            value = item.get("value")
            if not isinstance(value, dict):
                continue
            for action in value.get("action_requests") or []:
                if action.get("name") == "propose_patch" and "_doppel_patch" in action.get("args", {}):
                    proposal = action["args"]["_doppel_patch"]
                    await sink.emit(
                        "patch.proposed",
                        patch_id=proposal["patch_id"],
                        paths=[change["path"] for change in proposal["changes"]],
                        unified_diff=proposal["unified_diff"],
                    )
        await sink.emit(
            "runtime.finished",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
            status=result.status,
        )
        return result

    def _resume_value(
        self,
        value: Any,
        interrupt_values: list[Any],
        prepared_interrupt_values: list[Any] | None = None,
    ) -> Any:
        if not isinstance(value, dict) or "action" not in value:
            return value
        raw_requests = []
        for interrupt_value in interrupt_values:
            if isinstance(interrupt_value, dict):
                raw_requests.extend(interrupt_value.get("action_requests") or [])
        prepared_requests = []
        for interrupt_value in prepared_interrupt_values or []:
            if isinstance(interrupt_value, dict):
                prepared_requests.extend(interrupt_value.get("action_requests") or [])
        requests = prepared_requests or raw_requests
        if len(requests) != len(raw_requests):
            raise ValueError("stored approval does not match the pending Deep Agent actions")
        action = value["action"]
        if action == "approve":
            decisions = []
            for request in requests:
                if request.get("name") == "propose_patch":
                    decisions.append(
                        {
                            "type": "edit",
                            "edited_action": {"name": "propose_patch", "args": request["args"]},
                        }
                    )
                else:
                    decisions.append({"type": "approve"})
        elif action == "reject":
            decisions = [{"type": "reject", "message": "Rejected by user"} for _ in requests]
        elif action == "edit":
            edited = value.get("tool_calls") or []
            if len(edited) != len(requests):
                raise ValueError("edited approval must include every pending action")
            decisions = []
            for item, request in zip(edited, requests, strict=True):
                name = item.get("name", request.get("name"))
                arguments = item.get("arguments", request.get("args", {}))
                if request.get("name") == "propose_patch":
                    if name != "propose_patch" or self.patch_tool is None:
                        raise ValueError("an edited patch cannot change tool")
                    arguments = self.patch_tool.doppel_tool.approval_editor(
                        arguments, request["args"]
                    )
                decisions.append(
                    {
                        "type": "edit",
                        "edited_action": {"name": name, "args": arguments},
                    }
                )
        else:
            raise ValueError("unknown approval action")
        return {"decisions": decisions}

    async def resume(self, command: ResumeCommand, sink: EventSink | None = None) -> RuntimeResult:
        sink = sink or NullEventSink()
        await sink.emit(
            "runtime.resumed",
            runtime=self.name,
            run_id=command.run_id,
            thread_id=command.thread_id,
        )
        async with sqlite_checkpointer(self.checkpoint_path) as checkpointer:
            graph = self._build_graph(checkpointer, sink)
            config = {
                "configurable": {"thread_id": command.thread_id},
                "callbacks": [_DeepEventBridge(sink)],
            }
            snapshot = await graph.aget_state(config)
            interrupt_values = [
                item.value for task in snapshot.tasks for item in getattr(task, "interrupts", ())
            ]
            prepared_interrupt_values = None
            if isinstance(command.value, dict):
                prepared_interrupt_values = command.value.get("_prepared_interrupts")
            if prepared_interrupt_values is None:
                prepared_interrupt_values = self._pending_interrupts.get(command.thread_id)
            token = set_mcp_run_id(command.run_id)
            patch_token = set_patch_run_id(command.run_id)
            try:
                result = await graph.ainvoke(
                    Command(
                        resume=self._resume_value(
                            command.value,
                            interrupt_values,
                            prepared_interrupt_values,
                        )
                    ),
                    config=config,
                )
            finally:
                reset_patch_run_id(patch_token)
                reset_mcp_run_id(token)
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
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
        await self.process_supervisor.cancel_run(run_id)
