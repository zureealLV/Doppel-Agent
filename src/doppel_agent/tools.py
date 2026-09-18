"""Workspace tools with strict arguments and fail-closed capabilities."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .permissions import PermissionManager


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    capability: str
    properties: dict[str, dict[str, Any]]
    handler: Callable[[dict[str, Any]], str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": list(self.properties),
                    "additionalProperties": False,
                },
            },
        }


class ToolRegistry:
    def __init__(self, permissions: PermissionManager):
        self.permissions = permissions
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self.tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        if not isinstance(arguments, dict) or set(arguments) != set(tool.properties):
            raise ValueError(f"invalid arguments for {name}")
        for key, schema in tool.properties.items():
            value = arguments[key]
            if schema["type"] == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if schema["type"] == "array" and (
                not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value)
            ):
                raise ValueError(f"{key} must be a nonempty string array")
        decision = self.permissions.check(tool.capability)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return tool.handler(arguments)


def _target(workspace: Path, raw_path: str, *, must_exist: bool) -> Path:
    root = workspace.resolve(strict=True)
    relative = Path(raw_path)
    if not raw_path or relative.is_absolute() or relative.drive:
        raise PermissionError("path must be relative to workspace")
    path = root / relative
    if must_exist:
        target = path.resolve(strict=True)
    else:
        parent = path.parent.resolve(strict=True)
        target = (parent / path.name).resolve(strict=False)
    if not target.is_relative_to(root):
        raise PermissionError("path escapes workspace")
    return target


def read_file_tool(workspace: Path, max_bytes: int = 64 * 1024) -> Tool:
    def read(arguments: dict[str, Any]) -> str:
        target = _target(workspace, arguments["path"], must_exist=True)
        if not target.is_file():
            raise ValueError("path is not a file")
        if target.stat().st_size > max_bytes:
            raise ValueError("file exceeds read limit")
        return target.read_text(encoding="utf-8")

    return Tool("read_file", "Read a UTF-8 file within the workspace", "workspace_read", {"path": {"type": "string"}}, read)


def list_files_tool(workspace: Path, max_entries: int = 200) -> Tool:
    def list_files(arguments: dict[str, Any]) -> str:
        target = _target(workspace, arguments["path"], must_exist=True)
        if not target.is_dir():
            raise ValueError("path is not a directory")
        names = sorted(item.name + ("/" if item.is_dir() else "") for item in target.iterdir())
        return "\n".join(names[:max_entries]) + ("\n...truncated" if len(names) > max_entries else "")

    return Tool("list_files", "List one directory within the workspace; use '.' for root", "workspace_read", {"path": {"type": "string"}}, list_files)


def write_file_tool(workspace: Path, max_bytes: int = 256 * 1024) -> Tool:
    def write(arguments: dict[str, Any]) -> str:
        target = _target(workspace, arguments["path"], must_exist=False)
        if target.exists() and not target.is_file():
            raise ValueError("path is not a file")
        payload = arguments["content"].encode("utf-8")
        if len(payload) > max_bytes:
            raise ValueError("content exceeds write limit")
        temporary = target.with_name(f".{target.name}.doppel-{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return f"wrote {len(payload)} bytes to {arguments['path']}"

    return Tool(
        "write_file", "Create or replace a UTF-8 file within the workspace", "workspace_write",
        {"path": {"type": "string"}, "content": {"type": "string"}}, write,
    )


def run_command_tool(workspace: Path, timeout_seconds: int = 30) -> Tool:
    def run(arguments: dict[str, Any]) -> str:
        argv = arguments["argv"]
        if any("\x00" in arg for arg in argv):
            raise ValueError("NUL in command argument")
        try:
            completed = subprocess.run(
                argv, cwd=workspace, shell=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"command timed out after {timeout_seconds}s") from exc
        output = (completed.stdout + completed.stderr)[:64 * 1024]
        return f"exit_code={completed.returncode}\n{output}"

    return Tool(
        "run_command", "Run an argv command in the workspace without a shell; requires explicit command grant",
        "command_execute", {"argv": {"type": "array", "items": {"type": "string"}}}, run,
    )
