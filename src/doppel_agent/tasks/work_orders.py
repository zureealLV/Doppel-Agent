"""Immutable, bounded work-order plan domain; never dispatches provider work."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


MAX_TASKS = 64
MAX_TOTAL_PROMPT_BYTES = 512 * 1024
MAX_ATTEMPTS_PER_TASK = 3
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class PlanTask:
    id: str
    title: str
    prompt: str
    dependencies: tuple[str, ...] = ()
    access: str = "read"
    mode: str = "graph"
    profile_id: str | None = None


@dataclass(frozen=True)
class WorkOrderPlan:
    title: str
    tasks: tuple[PlanTask, ...]

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value.strip()


def plan_from_payload(payload: Any) -> WorkOrderPlan:
    if not isinstance(payload, dict) or set(payload) != {"title", "tasks"}:
        raise ValueError("a plan requires only title and tasks")
    title = _text(payload["title"], "plan title", 200)
    source = payload["tasks"]
    if not isinstance(source, (list, tuple)) or not 1 <= len(source) <= MAX_TASKS:
        raise ValueError("a plan requires 1 to 64 tasks")
    tasks = []
    total_bytes = 0
    allowed = {"id", "title", "prompt", "dependencies", "access", "mode", "profile_id"}
    for item in source:
        if not isinstance(item, dict) or set(item) - allowed or not {"id", "title", "prompt"} <= set(item):
            raise ValueError("invalid task fields")
        identifier = item["id"]
        if not isinstance(identifier, str) or not _TASK_ID.fullmatch(identifier):
            raise ValueError("invalid task id")
        dependencies = item.get("dependencies", ())
        if not isinstance(dependencies, (list, tuple)) or len(dependencies) > MAX_TASKS:
            raise ValueError("invalid dependencies")
        if any(not isinstance(dep, str) or not _TASK_ID.fullmatch(dep) for dep in dependencies):
            raise ValueError("invalid dependency id")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("duplicate dependency")
        access, mode = item.get("access", "read"), item.get("mode", "graph")
        if access not in ("read", "write") or mode not in ("legacy", "graph", "deep"):
            raise ValueError("invalid task access or mode")
        profile = item.get("profile_id")
        if profile is not None:
            profile = _text(profile, "profile id", 128)
        prompt = _text(item["prompt"], "task prompt", 100000)
        total_bytes += len(prompt.encode("utf-8"))
        if total_bytes > MAX_TOTAL_PROMPT_BYTES:
            raise ValueError("total plan prompt bytes exceed limit")
        tasks.append(PlanTask(identifier, _text(item["title"], "task title", 200), prompt,
                              tuple(dependencies), access, mode, profile))
    plan = WorkOrderPlan(title, tuple(tasks))
    topological_order(plan)
    return plan


def topological_order(plan: WorkOrderPlan) -> tuple[str, ...]:
    """Kahn ordering, preserving plan order for equally ready nodes."""
    tasks = {task.id: task for task in plan.tasks}
    if len(tasks) != len(plan.tasks):
        raise ValueError("duplicate task id")
    known = set(tasks)
    for task in plan.tasks:
        if task.id in task.dependencies or any(dep not in known for dep in task.dependencies):
            raise ValueError("self or unknown dependency")
    complete: set[str] = set()
    result = []
    while len(result) < len(tasks):
        ready = [task.id for task in plan.tasks if task.id not in complete and set(task.dependencies) <= complete]
        if not ready:
            raise ValueError("cyclic task dependencies")
        result.extend(ready)
        complete.update(ready)
    return tuple(result)


def execution_settings(payload: Any) -> dict[str, Any]:
    """Explicit activation options, separate from editable plan metadata."""
    allowed = {"permissions", "profile_id", "effort", "deadline_seconds", "profiles", "context"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid execution settings")
    permissions = payload.get("permissions", {})
    names = ("workspace_write", "command_execute", "mcp_execute", "delegate")
    if not isinstance(permissions, dict) or set(permissions) - set(names):
        raise ValueError("invalid execution permissions")
    if any(type(value) is not bool for value in permissions.values()):
        raise ValueError("execution grants must be explicit booleans")
    profile = payload.get("profile_id")
    if profile is not None:
        profile = _text(profile, "profile id", 128)
    effort = payload.get("effort", "balanced")
    deadline = payload.get("deadline_seconds", 600)
    if effort not in ("quick", "balanced", "deep") or type(deadline) is not int or not 1 <= deadline <= 3600:
        raise ValueError("invalid execution effort or deadline")
    result = {"permissions": {name: permissions.get(name, False) for name in names}, "profile_id": profile,
              "effort": effort, "deadline_seconds": deadline}
    if payload.get("context") is not None:
        from ..context.input import context_spec

        result["context"] = context_spec(payload["context"])
    if "profiles" in payload:
        profiles = payload["profiles"]
        if not isinstance(profiles, dict) or len(profiles) > MAX_TASKS + 1:
            raise ValueError("invalid frozen profiles")
        validated = {}
        for identifier, snapshot in profiles.items():
            identifier = _text(identifier, "profile id", 128)
            if not isinstance(snapshot, dict) or set(snapshot) - {"id", "provider", "model", "base_url", "implementation", "billing_price_receipt"}:
                raise ValueError("invalid frozen profile fields")
            if snapshot.get("id") != identifier or snapshot.get("provider") not in ("mock", "openai", "explicit_override"):
                raise ValueError("invalid frozen profile identity")
            item = {"id": identifier, "provider": snapshot["provider"], "model": _text(snapshot.get("model"), "model", 400)}
            if "base_url" in snapshot:
                value = snapshot["base_url"]
                if not isinstance(value, str) or len(value) > 4096 or "\x00" in value:
                    raise ValueError("invalid frozen profile URL")
                item["base_url"] = value
            if "implementation" in snapshot:
                item["implementation"] = _text(snapshot["implementation"], "provider implementation", 128)
            if "billing_price_receipt" in snapshot:
                from ..billing_tariff import validate_price_receipt

                # _text validates the existing bound but trims display input.
                # A new frozen snapshot must preserve EXACT execution identity:
                # trimming here changes the model and invalidates later replay.
                # Historical snapshots without this field keep their old shape.
                item["model"] = snapshot["model"]
                receipt = snapshot["billing_price_receipt"]
                item["billing_price_receipt"] = None if receipt is None else validate_price_receipt(receipt, profile=item)
            validated[identifier] = item
        result["profiles"] = validated
    return result


def dispatch_request(task: dict[str, Any], settings: dict[str, Any], admission_key: str) -> dict[str, Any]:
    settings = execution_settings(settings)
    # A read node gets no side-effect tools, including arbitrary commands/MCP or
    # delegation. The access label itself never grants workspace_write.
    permissions = settings["permissions"] if task["access"] == "write" else {
        name: False for name in settings["permissions"]
    }
    result = {"conversation_id": None, "prompt": task["prompt"], "mode": task["mode"],
            "profile_id": task["profile_id"] or settings["profile_id"], "effort": settings["effort"],
            "deadline_seconds": settings["deadline_seconds"], "permissions": permissions,
            "idempotency_key": admission_key}
    if "context" in settings:
        result["context"] = settings["context"]
    return result
