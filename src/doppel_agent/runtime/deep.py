"""Optional Deep Agents runtime hosted inside Doppel's policy boundary."""

from __future__ import annotations

import asyncio
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
from .base import EventSink, NullEventSink, ResumeCommand, RunRequest, RuntimeResult
from .deep_backend import DoppelBackend
from .deep_model import DoppelChatModel
from .graph import GraphRuntime


register_harness_profile(
    "doppel",
    HarnessProfile(
        excluded_tools=frozenset({"execute"}),
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

    async def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        await self.sink.emit("deep.tool_failed", error=f"{type(error).__name__}: {error}")

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
        interrupt_on: dict[str, bool] = {}
        if self.allow_write:
            interrupt_on.update(write_file=True, edit_file=True)
        interrupt_on.update({tool.name: True for tool in self.additional_tools})
        return create_deep_agent(
            model=model,
            tools=self.additional_tools,
            system_prompt=(
                "You are Doppel deep mode. Use progressive disclosure, keep work inside the workspace, "
                "delegate at most two read-only investigations, and ground the final answer in evidence paths."
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

    def _result(self, run_id: str, thread_id: str, result: dict[str, Any]) -> RuntimeResult:
        interrupts = result.get("__interrupt__") or ()
        metadata: dict[str, Any] = {
            "message_count": len(result.get("messages") or []),
            "subagent_limit": self.max_subagents,
        }
        if interrupts:
            metadata["interrupts"] = [
                {"id": getattr(item, "id", ""), "value": getattr(item, "value", None)}
                for item in interrupts
            ]
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
        await sink.emit(
            "runtime.finished",
            runtime=self.name,
            run_id=request.run_id,
            thread_id=request.thread_id,
            status=result.status,
        )
        return result

    @staticmethod
    def _resume_value(value: Any, interrupt_values: list[Any]) -> Any:
        if not isinstance(value, dict) or "action" not in value:
            return value
        requests = []
        for interrupt_value in interrupt_values:
            if isinstance(interrupt_value, dict):
                requests.extend(interrupt_value.get("action_requests") or [])
        action = value["action"]
        if action == "approve":
            decisions = [{"type": "approve"} for _ in requests]
        elif action == "reject":
            decisions = [{"type": "reject", "message": "Rejected by user"} for _ in requests]
        elif action == "edit":
            edited = value.get("tool_calls") or []
            if len(edited) != len(requests):
                raise ValueError("edited approval must include every pending action")
            decisions = [
                {
                    "type": "edit",
                    "edited_action": {
                        "name": item.get("name", request.get("name")),
                        "args": item.get("arguments", request.get("args", {})),
                    },
                }
                for item, request in zip(edited, requests, strict=True)
            ]
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
            token = set_mcp_run_id(command.run_id)
            try:
                result = await graph.ainvoke(
                    Command(resume=self._resume_value(command.value, interrupt_values)),
                    config=config,
                )
            finally:
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
