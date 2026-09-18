"""Opt-in, bounded, read-only delegation to the same configured provider."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .events import EventBus
from .loop import AgentLoop
from .permissions import PermissionManager
from .provider import Provider
from .tools import Tool, ToolRegistry, list_files_tool, read_file_tool


class DelegateManager:
    def __init__(self, workspace: Path, provider: Provider, bus: EventBus, max_delegations: int = 2):
        self.workspace = workspace
        self.provider = provider
        self.bus = bus
        self.max_delegations = max_delegations
        self.used = 0

    def execute(self, arguments: dict[str, Any]) -> str:
        prompt = arguments["prompt"].strip()
        if not prompt or len(prompt) > 4_000:
            raise ValueError("subagent prompt must contain 1 to 4000 characters")
        if self.used >= self.max_delegations:
            raise ValueError("subagent delegation budget exhausted")
        self.used += 1
        child_number = self.used
        self.bus.emit("subagent_started", child=child_number)
        tools = ToolRegistry(PermissionManager(frozenset({"workspace_read"})))
        tools.register(read_file_tool(self.workspace))
        tools.register(list_files_tool(self.workspace))
        child_bus = EventBus(
            self.bus.run_id,
            lambda event: self.bus.emit(
                "subagent_event", child=child_number,
                child_kind=event["kind"], child_payload=event["payload"],
            ),
        )
        try:
            answer = AgentLoop(self.provider, tools, child_bus, max_steps=4, context_limit_tokens=8_000).run(
                "Read-only delegated task. You cannot edit files, run commands, or delegate further. " + prompt
            )
        except Exception as exc:
            self.bus.emit("subagent_failed", child=child_number, error_type=type(exc).__name__)
            raise ValueError(f"subagent failed ({type(exc).__name__})") from exc
        self.bus.emit("subagent_completed", child=child_number, answer_chars=len(answer))
        return answer[:8_000]


def delegate_tool(manager: DelegateManager) -> Tool:
    return Tool(
        "delegate_readonly", "Ask a bounded read-only subagent to inspect the workspace and report findings",
        "delegate_readonly", {"prompt": {"type": "string"}}, manager.execute,
    )
