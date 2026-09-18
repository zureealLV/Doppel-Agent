"""Core owns runs, storage, tools and the provider boundary."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .events import EventBus
from .loop import AgentLoop
from .permissions import PermissionManager
from .provider import MockProvider, Provider
from .storage import RunStore
from .tools import ToolRegistry, list_files_tool, read_file_tool, run_command_tool, write_file_tool


class Core:
    def __init__(
        self, workspace: Path, provider: Provider | None = None,
        *, allow_write: bool = False, allow_command: bool = False,
    ):
        self.workspace = workspace.resolve(strict=True)
        self.provider = provider or MockProvider()
        self.store = RunStore(self.workspace / ".doppel-agent")
        capabilities = {"workspace_read"}
        if allow_write:
            capabilities.add("workspace_write")
        if allow_command:
            capabilities.add("command_execute")
        self.permissions = PermissionManager(frozenset(capabilities))

    def run(self, prompt: str) -> dict[str, str]:
        if not prompt or len(prompt) > 100_000:
            raise ValueError("prompt must contain 1 to 100000 characters")
        run_id = uuid4().hex
        bus = EventBus(run_id, lambda event: self.store.append_event(run_id, event))
        tools = ToolRegistry(self.permissions)
        tools.register(read_file_tool(self.workspace))
        tools.register(list_files_tool(self.workspace))
        tools.register(write_file_tool(self.workspace))
        tools.register(run_command_tool(self.workspace))
        status = "completed"
        try:
            answer = AgentLoop(self.provider, tools, bus).run(prompt)
        except Exception as exc:
            status = "failed"
            answer = f"{type(exc).__name__}: {exc}"
            bus.emit("run_failed", reason=answer)
        self.store.write_session(run_id, {"run_id": run_id, "prompt": prompt, "status": status, "answer": answer})
        return {"run_id": run_id, "status": status, "answer": answer}
