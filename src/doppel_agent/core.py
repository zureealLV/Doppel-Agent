"""Core owns runs, storage, tools and the provider boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .events import EventBus
from .loop import AgentLoop
from .permissions import PermissionManager
from .provider import Message, MockProvider, Provider
from .storage import RunStore
from .tools import ToolRegistry, list_files_tool, read_file_tool, run_command_tool, write_file_tool
from .tasks.manager import TaskManager
from .tasks.tools import task_tools
from .skills.loader import SkillLoader
from .skills.tools import skill_tools
from .mcp_bridge import MCPBridge, mcp_tools
from .subagents import DelegateManager, delegate_tool


class Core:
    def __init__(
        self, workspace: Path, provider: Provider | None = None,
        *, allow_write: bool = False, allow_command: bool = False, allow_mcp: bool = False,
        allow_delegate: bool = False,
        approver: Callable[[str, str, dict[str, Any]], bool] | None = None,
        state_root: Path | None = None,
        max_steps: int = 8,
    ):
        self.workspace = workspace.resolve(strict=True)
        self.provider = provider or MockProvider()
        self.state_root = (state_root or (self.workspace / ".doppel-agent")).resolve()
        self.store = RunStore(self.state_root)
        self.max_steps = max_steps
        capabilities = {"workspace_read", "task_manage"}
        if allow_write:
            capabilities.add("workspace_write")
        if allow_command:
            capabilities.add("command_execute")
        if allow_mcp:
            capabilities.add("mcp_execute")
        if allow_delegate:
            capabilities.add("delegate_readonly")
        self.allowed_capabilities = frozenset(capabilities)
        self.approver = approver

    def run(self, prompt: str, *, run_id: str | None = None, history: list[Message] | None = None) -> dict[str, str]:
        if not prompt or len(prompt) > 100_000:
            raise ValueError("prompt must contain 1 to 100000 characters")
        run_id = run_id or uuid4().hex
        bus = EventBus(run_id, lambda event: self.store.append_event(run_id, event))
        mcp_bridge: MCPBridge | None = None

        def request_approval(capability: str, name: str, arguments: dict[str, Any]) -> bool:
            visible_arguments = mcp_bridge.approval_context(arguments) if capability == "mcp_execute" and mcp_bridge else arguments
            bus.emit("approval_requested", capability=capability, tool=name, arguments=visible_arguments)
            allowed = bool(self.approver and self.approver(capability, name, visible_arguments))
            bus.emit("approval_decided", capability=capability, tool=name, allowed=allowed)
            return allowed

        permissions = PermissionManager(
            self.allowed_capabilities,
            request_approval if self.approver is not None else None,
        )
        tools = ToolRegistry(permissions)
        tools.register(read_file_tool(self.workspace))
        tools.register(list_files_tool(self.workspace))
        tools.register(write_file_tool(self.workspace))
        tools.register(run_command_tool(self.workspace))
        manager = TaskManager(self.state_root / "tasks.sqlite3")
        for tool in task_tools(manager, run_id):
            tools.register(tool)
        for tool in skill_tools(SkillLoader(self.workspace)):
            tools.register(tool)
        if "delegate_readonly" in self.allowed_capabilities:
            tools.register(delegate_tool(DelegateManager(self.workspace, self.provider, bus)))
        status = "completed"
        try:
            if "mcp_execute" in self.allowed_capabilities:
                mcp_bridge = MCPBridge(self.workspace)
                for tool in mcp_tools(mcp_bridge):
                    tools.register(tool)
            answer = AgentLoop(self.provider, tools, bus, max_steps=self.max_steps).run(prompt, history)
        except Exception as exc:
            status = "failed"
            answer = f"{type(exc).__name__}: {exc}"
            bus.emit("run_failed", reason=answer)
        self.store.write_session(run_id, {"run_id": run_id, "prompt": prompt, "status": status, "answer": answer})
        return {"run_id": run_id, "status": status, "answer": answer}
