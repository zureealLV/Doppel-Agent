"""Workspace tools with strict arguments and fail-closed capabilities."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable
from typing import Any, Callable
from uuid import uuid4

from .permissions import PermissionManager
from .workspace.patching import PatchProposal, PatchService
from .workspace.process_supervisor import ProcessSupervisor
from .workspace.verification import VerificationPipeline


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    capability: str
    properties: dict[str, dict[str, Any]]
    handler: Callable[[dict[str, Any]], str]
    input_schema: dict[str, Any] | None = None
    async_handler: Callable[[dict[str, Any], str, str], Awaitable[str]] | None = None
    approval_preparer: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    approval_editor: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {
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

    def capability(self, name: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        return tool.capability

    def is_async(self, name: str) -> bool:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        return tool.async_handler is not None

    def prepare_approval(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        if tool.approval_preparer is None:
            return arguments
        return tool.approval_preparer(arguments)

    def prepare_approval_edit(
        self,
        name: str,
        arguments: dict[str, Any],
        previous_arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        if tool.approval_editor is not None:
            return tool.approval_editor(arguments, previous_arguments)
        return self.prepare_approval(name, arguments)

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError(f"invalid arguments for {name}")
        if tool.input_schema is None and set(arguments) != set(tool.properties):
            raise ValueError(f"invalid arguments for {name}")
        for key, schema in tool.properties.items():
            value = arguments[key]
            if schema["type"] == "string" and not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if schema["type"] == "array" and (
                not isinstance(value, list) or not all(isinstance(item, str) for item in value)
            ):
                raise ValueError(f"{key} must be a string array")
            if schema["type"] == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{key} must be an integer")
        decision = self.permissions.check(tool.capability, name, arguments)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return tool.handler(arguments)

    async def aexecute(self, name: str, arguments: dict[str, Any], *, run_id: str, tool_call_id: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        if tool.async_handler is None:
            import asyncio

            return await asyncio.to_thread(self.execute, name, arguments)
        decision = self.permissions.check(tool.capability, name, arguments)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return await tool.async_handler(arguments, run_id, tool_call_id)


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


def _sensitive_path(path: Path) -> bool:
    name = path.name.lower()
    if name == ".env.example":
        return False
    return (
        name == ".env" or name.startswith(".env.") or name in {
            "credentials", "credentials.json", "secrets.json",
            "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
            ".npmrc", ".netrc", ".pgpass", ".git-credentials", ".htpasswd",
        } or path.suffix.lower() in {".pem", ".pfx", ".p12", ".key"}
    )


def _reserved_path(workspace: Path, path: Path) -> bool:
    relative = path.relative_to(workspace.resolve(strict=True))
    return any(part.lower() in {".git", ".doppel-agent", ".ssh", ".aws", ".docker"} for part in relative.parts)


def read_file_tool(workspace: Path, max_bytes: int = 64 * 1024) -> Tool:
    def read(arguments: dict[str, Any]) -> str:
        target = _target(workspace, arguments["path"], must_exist=True)
        if not target.is_file():
            raise ValueError("path is not a file")
        if _sensitive_path(target) or _reserved_path(workspace, target):
            raise PermissionError("reading secret-bearing files is blocked")
        if target.stat().st_size > max_bytes:
            raise ValueError("file exceeds read limit")
        return target.read_text(encoding="utf-8")

    return Tool("read_file", "Read one small UTF-8 file. Prefer search_text and read_file_range during review.", "workspace_read", {"path": {"type": "string"}}, read)


_IGNORED_DIRS = {".git", ".doppel-agent", ".venv", "node_modules", "dist", ".dist", ".dist-debug", "build", ".build-tmp", ".next", "artifacts", "__pycache__", ".pytest_cache", ".ssh", ".aws", ".docker"}
_TEXT_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".cs", ".java", ".go", ".rs", ".cpp", ".c", ".h", ".html", ".css", ".json", ".toml", ".yaml", ".yml", ".md", ".txt", ".sql", ".ps1", ".sh"}


def _source_files(root: Path):
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and name.lower() not in _IGNORED_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            if path.suffix.lower() in _TEXT_SUFFIXES and not _sensitive_path(path):
                yield path


def workspace_map_tool(workspace: Path, max_entries: int = 180) -> Tool:
    used = False

    def workspace_map(arguments: dict[str, Any]) -> str:
        nonlocal used
        if used:
            return "Workspace map was already returned. Use search_text and read_file_range now."
        used = True
        target = _target(workspace, arguments["path"], must_exist=True)
        if not target.is_dir() or _reserved_path(workspace, target):
            raise ValueError("path is not a readable directory")
        files = []
        for path in _source_files(target):
            files.append(f"{path.relative_to(workspace).as_posix()}\t{path.stat().st_size} B")
            if len(files) >= max_entries:
                files.append("...truncated")
                break
        return "\n".join(files)

    return Tool("workspace_map", "Return a compact recursive source-file map with sizes; use this first for code review.", "workspace_read", {"path": {"type": "string"}}, workspace_map)


def search_text_tool(workspace: Path, max_matches: int = 30) -> Tool:
    def search(arguments: dict[str, Any]) -> str:
        query = arguments["query"]
        if not query or len(query) > 200:
            raise ValueError("query must contain 1 to 200 characters")
        target = _target(workspace, arguments["path"], must_exist=True)
        candidates = [target] if target.is_file() else _source_files(target)
        matches: list[str] = []
        for path in candidates:
            if not path.is_file() or _sensitive_path(path) or _reserved_path(workspace, path) or path.stat().st_size > 1024 * 1024:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    matches.append(f"{path.relative_to(workspace).as_posix()}:{number}: {line.strip()[:180]}")
                    if len(matches) >= max_matches:
                        return "\n".join(matches) + "\n...truncated"
        return "\n".join(matches) if matches else "No matches."

    return Tool("search_text", "Search source text recursively and return concise file:line matches.", "workspace_read", {"query": {"type": "string"}, "path": {"type": "string"}}, search)


def read_file_range_tool(workspace: Path) -> Tool:
    def read_range(arguments: dict[str, Any]) -> str:
        start, end = arguments["start_line"], arguments["end_line"]
        if start < 1 or end < start or end - start > 400:
            raise ValueError("line range must be positive and no more than 401 lines")
        target = _target(workspace, arguments["path"], must_exist=True)
        if not target.is_file() or _sensitive_path(target) or _reserved_path(workspace, target):
            raise PermissionError("file is not readable")
        if target.stat().st_size > 1024 * 1024:
            raise ValueError("file exceeds range-read limit")
        lines = target.read_text(encoding="utf-8").splitlines()
        return "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1))

    return Tool("read_file_range", "Read only the relevant numbered line range from a UTF-8 file.", "workspace_read", {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, read_range)


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
        if _sensitive_path(target) or _reserved_path(workspace, target):
            raise PermissionError("writing secret-bearing or agent-state files is blocked")
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


def patch_tool(
    workspace: Path,
    *,
    verification: VerificationPipeline | None = None,
) -> Tool:
    """Create one reviewable multi-file patch and apply only its prepared snapshot."""

    service = PatchService(workspace)

    def prepare(arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != {"changes"}:
            raise ValueError("a new patch proposal must contain only changes")
        proposal = service.prepare(arguments.get("changes"))
        return {"changes": arguments["changes"], "_doppel_patch": proposal.as_dict()}

    def apply(arguments: dict[str, Any]) -> str:
        if set(arguments) != {"changes", "_doppel_patch"}:
            raise ValueError("propose_patch must be prepared before execution")
        proposal = PatchProposal.from_dict(arguments["_doppel_patch"])
        visible = [(item.get("path"), item.get("content")) for item in arguments["changes"]]
        prepared = [(change.path, change.content) for change in proposal.changes]
        if visible != prepared:
            raise ValueError("reviewed patch differs from execution arguments")
        result = service.apply(proposal)
        return json.dumps(
            {"patch_id": result.patch_id, "changed_paths": result.changed_paths},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def apply_and_verify(arguments: dict[str, Any], run_id: str, tool_call_id: str) -> str:
        applied = json.loads(apply(arguments))
        if verification is not None and verification.available:
            report = await verification.run(run_id=run_id)
            applied["verification"] = report.as_dict()
        return json.dumps(applied, ensure_ascii=False, separators=(",", ":"))

    def edit(arguments: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != {"changes"}:
            raise ValueError("an edited patch proposal must contain only changes")
        if "_doppel_patch" not in previous:
            raise ValueError("edited patch is missing its reviewed base")
        original = PatchProposal.from_dict(previous["_doppel_patch"])
        refreshed = service.prepare(arguments.get("changes"))
        original_bases = {change.path: change.base_hash for change in original.changes}
        refreshed_bases = {change.path: change.base_hash for change in refreshed.changes}
        if original_bases != refreshed_bases:
            raise ValueError("edited patch paths or workspace base changed; request a new proposal")
        return {"changes": arguments["changes"], "_doppel_patch": refreshed.as_dict()}

    schema = {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["changes"],
        "additionalProperties": False,
    }
    return Tool(
        "propose_patch",
        "Propose complete UTF-8 contents for one or more files; Doppel shows a diff before applying",
        "workspace_write",
        {},
        apply,
        input_schema=schema,
        async_handler=(
            apply_and_verify if verification is not None and verification.available else None
        ),
        approval_preparer=prepare,
        approval_editor=edit,
    )


def run_command_tool(
    workspace: Path,
    timeout_seconds: int = 30,
    *,
    supervisor: ProcessSupervisor | None = None,
) -> Tool:
    process_supervisor = supervisor or ProcessSupervisor()

    def run(arguments: dict[str, Any]) -> str:
        return asyncio.run(arun(arguments, "standalone", uuid4().hex))

    async def arun(arguments: dict[str, Any], run_id: str, tool_call_id: str) -> str:
        argv = arguments["argv"]
        if not argv:
            raise ValueError("argv must not be empty")
        if any("\x00" in arg for arg in argv):
            raise ValueError("NUL in command argument")
        result = await process_supervisor.run(
            argv,
            cwd=workspace,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
        )
        output = (result.stdout + result.stderr)[:64 * 1024]
        return (
            f"exit_code={result.exit_code}\n"
            f"supervision={result.supervision}\n"
            f"{output}"
        )

    return Tool(
        "run_command", "Run an argv command in the workspace without a shell; requires explicit command grant",
        "command_execute", {"argv": {"type": "array", "items": {"type": "string"}}}, run,
        async_handler=arun,
    )
