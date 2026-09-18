"""Agent tools exposing validated skill documents."""

from __future__ import annotations

import json

from ..tools import Tool
from .loader import SkillLoader


def skill_tools(loader: SkillLoader) -> list[Tool]:
    def list_skills(args: dict) -> str:
        return json.dumps([{"name": skill.name, "description": skill.description} for skill in loader.list()])

    def read_skill(args: dict) -> str:
        skill = loader.get(args["name"])
        return skill.content

    return [
        Tool("skill_list", "List workspace-local skills", "workspace_read", {}, list_skills),
        Tool("skill_read", "Read one validated workspace-local SKILL.md", "workspace_read",
             {"name": {"type": "string"}}, read_skill),
    ]
