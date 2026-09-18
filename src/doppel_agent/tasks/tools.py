"""Agent-facing durable task graph tools."""

from __future__ import annotations

import json

from ..tools import Tool
from .manager import TaskManager


def task_tools(manager: TaskManager, run_id: str) -> list[Tool]:
    def create(args: dict) -> str:
        task_id = manager.create(run_id, args["title"], args["dependencies"])
        return json.dumps({"task_id": task_id, "status": "pending"})

    def list_tasks(args: dict) -> str:
        return json.dumps({"tasks": manager.list(run_id), "ready": [task["id"] for task in manager.ready(run_id)]})

    def set_deps(args: dict) -> str:
        manager.set_dependencies(args["task_id"], args["dependencies"])
        return "dependencies updated"

    def transition(args: dict) -> str:
        return json.dumps(manager.transition(run_id, args["task_id"], args["action"], args["result"]))

    return [
        Tool("task_create", "Create a durable task; dependencies are task IDs from this run", "task_manage",
             {"title": {"type": "string"}, "dependencies": {"type": "array", "items": {"type": "string"}}}, create),
        Tool("task_list", "List durable tasks and tasks ready to start in this run", "task_manage", {}, list_tasks),
        Tool("task_set_dependencies", "Change dependencies of a pending task; cycles are rejected", "task_manage",
             {"task_id": {"type": "string"}, "dependencies": {"type": "array", "items": {"type": "string"}}}, set_deps),
        Tool("task_transition", "Transition a task: start, complete, fail, or retry", "task_manage",
             {"task_id": {"type": "string"}, "action": {"type": "string"}, "result": {"type": "string"}}, transition),
    ]
