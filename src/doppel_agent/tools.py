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
from .persistence.tool_ledger import ToolExecutionLedger
from .concurrency.limits import ResourceLimits
from .persistence.owned import await_durable
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
    # Specialized effect owns its ledger reservation; never additionally wrap
    # it in execute_once/aexecute_once with the same key.
    effect_handler: Callable[[dict[str, Any], str, str, ToolExecutionLedger | None], tuple[str, bool]] | None = None
    after_effect: Callable[[dict[str, Any], str, str, str, bool], Awaitable[str]] | None = None
    after_effect_capability: str | None = None

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
        return tool.async_handler is not None or tool.effect_handler is not None

    def owns_effect(self, name: str) -> bool:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        return tool.effect_handler is not None

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

    async def aexecute(self, name: str, arguments: dict[str, Any], *, run_id: str, tool_call_id: str,
                       ledger: ToolExecutionLedger | None = None,
                       on_effect: Callable[[str, bool, bool], Awaitable[None]] | None = None) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        if tool.effect_handler is not None:
            if not isinstance(arguments, dict):
                raise ValueError(f"invalid arguments for {name}")
            decision = self.permissions.check(tool.capability, name, arguments)
            if not decision.allowed:
                raise PermissionError(decision.reason)
            worker = asyncio.create_task(asyncio.to_thread(tool.effect_handler, arguments, run_id, tool_call_id, ledger))
            try:
                output, replayed = await await_durable(worker)
            except asyncio.CancelledError:
                # The source worker/ledger is settled before the lock/owner may
                # leave. Expose only its actual completed effect, not intent.
                if on_effect is not None and not worker.cancelled() and worker.done() and worker.exception() is None:
                    output, replayed = worker.result()
                    try:
                        await await_durable(on_effect(output, replayed, True))
                    except asyncio.CancelledError:
                        pass
                raise
            if on_effect is not None:
                await await_durable(on_effect(output, replayed, False))
            # This phase is explicitly outside effect reservation. Verification
            # failure/cancel cannot turn the completed patch into a failed write.
            if tool.after_effect is not None:
                if tool.after_effect_capability is not None and tool.after_effect_capability not in self.permissions.allowed_capabilities:
                    applied = json.loads(output)
                    applied["verification"] = {"status": "not_run_command_grant_missing", "success": None, "results": []}
                    return json.dumps(applied, ensure_ascii=False, separators=(",", ":"))
                return await tool.after_effect(arguments, output, run_id, tool_call_id, replayed)
            return output
        if tool.async_handler is None:
            return await await_durable(asyncio.to_thread(self.execute, name, arguments))
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
    resource_limits: ResourceLimits | None = None,
    require_verification_review: bool = False,
) -> Tool:
    """Create one reviewable multi-file patch and apply only its prepared snapshot."""

    if type(require_verification_review) is not bool:
        raise ValueError("verification_review_policy_invalid")
    service = PatchService(workspace)

    def prepare(arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != {"changes"}:
            raise ValueError("a new patch proposal must contain only changes")
        proposal = service.prepare(arguments.get("changes"))
        return {"changes": arguments["changes"], "_doppel_patch": proposal.as_dict()}

    def reviewed(arguments: dict[str, Any]) -> PatchProposal:
        if set(arguments) != {"changes", "_doppel_patch"}:
            raise ValueError("propose_patch must be prepared before execution")
        proposal = PatchProposal.from_dict(arguments["_doppel_patch"])
        visible = [(item.get("path"), item.get("content")) for item in arguments["changes"]]
        prepared = [(change.path, change.content) for change in proposal.changes]
        if visible != prepared:
            raise ValueError("reviewed patch differs from execution arguments")
        if any(change.content is None or change.target_mode is not None for change in proposal.changes):
            raise ValueError("model patch tool cannot execute inverse changes")
        return proposal

    def apply(arguments: dict[str, Any]) -> str:
        proposal = reviewed(arguments)
        result = service.apply(proposal)
        return json.dumps(
            {"patch_id": result.patch_id, "changed_paths": result.changed_paths, "unified_diff": proposal.unified_diff},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def effect(arguments: dict[str, Any], run_id: str, tool_call_id: str,
               ledger: ToolExecutionLedger | None) -> tuple[str, bool]:
        if (not isinstance(run_id, str) or not run_id or len(run_id) > 128 or "\x00" in run_id
                or not isinstance(tool_call_id, str) or not tool_call_id or len(tool_call_id) > 256 or "\x00" in tool_call_id):
            raise ValueError("patch_execution_scope_unavailable")
        proposal = reviewed(arguments)
        if ledger is None:
            result = service.apply(proposal)
            output = json.dumps({"patch_id": result.patch_id, "changed_paths": result.changed_paths,
                                 "unified_diff": proposal.unified_diff,
                                 "patch_receipt": result.receipt.as_dict(include_preimage=False)}, ensure_ascii=False)
            replayed = False
        else:
            output, replayed = ledger.execute_patch_once(run_id, tool_call_id, arguments, service, proposal)
        applied = json.loads(output)
        applied["receipt_source"] = {"run_id": run_id, "tool_call_id": tool_call_id,
                                     "durability": "sealed_tool_ledger" if ledger is not None else "ephemeral"}
        applied["effect_replayed"] = replayed
        if require_verification_review:
            # Add only to returned/event metadata AFTER the exact patch result
            # is sealed. Never mutate the patch ledger's stored result shape or
            # treat its approval/grants as a reviewed command plan. No config
            # lookup, pending review creation, launch or claimed test success.
            applied["verification"] = {
                "status": "not_run_separate_review_required", "success": None, "results": [],
                "operation_kind": "manual_verification",
                "source": {**applied["receipt_source"], "patch_id": applied["patch_id"]},
                "target": "current_workspace_not_original_patch_snapshot",
            }
        return json.dumps(applied, ensure_ascii=False, separators=(",", ":")), replayed

    async def after_effect(_arguments: dict[str, Any], output: str, run_id: str, _tool_call_id: str, replayed: bool) -> str:
        if require_verification_review:
            return output
        applied = json.loads(output)
        if verification is not None and verification.available:
            if replayed:
                applied["verification"] = {"status": "not_repeated_for_patch_replay", "success": None, "results": []}
            else:
                try:
                    if resource_limits is not None:
                        async with resource_limits.command():
                            report = await verification.run(run_id=run_id)
                    else:
                        report = await verification.run(run_id=run_id)
                except Exception as exc:
                    applied["verification"] = {"status": "failed", "success": False, "results": [],
                                               "error": f"{type(exc).__name__}: verification failed"}
                else:
                    applied["verification"] = {"status": "completed", **report.as_dict()}
        return json.dumps(applied, ensure_ascii=False, separators=(",", ":"))

    def edit(arguments: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != {"changes"}:
            raise ValueError("an edited patch proposal must contain only changes")
        if "_doppel_patch" not in previous:
            raise ValueError("edited patch is missing its reviewed base")
        original = PatchProposal.from_dict(previous["_doppel_patch"])
        refreshed = service.prepare(arguments.get("changes"))
        original_bases = {change.path: (change.base_hash, change.base_mode) for change in original.changes}
        refreshed_bases = {change.path: (change.base_hash, change.base_mode) for change in refreshed.changes}
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
        approval_preparer=prepare,
        approval_editor=edit,
        effect_handler=effect,
        after_effect=after_effect,
        after_effect_capability=("command_execute" if not require_verification_review
                                 and verification is not None and verification.available else None),
    )


def run_command_tool(
    workspace: Path,
    timeout_seconds: int = 30,
    *,
    supervisor: ProcessSupervisor | None = None,
) -> Tool:
    process_supervisor = supervisor if supervisor is not None else ProcessSupervisor()

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
