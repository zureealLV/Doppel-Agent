import unittest
from pathlib import Path

from doppel_agent.skills import SkillRegistry


class BuiltinSkillsTests(unittest.TestCase):
    def test_four_high_value_skills_have_required_sections(self):
        root = Path(__file__).resolve().parents[2]
        registry = SkillRegistry(root)
        skills = {skill.name: skill for skill in registry.list()}
        self.assertEqual(
            set(skills),
            {"code-review", "bugfix", "test-repair", "mcp-operations"},
        )
        for skill in skills.values():
            body = skill.instructions()
            for heading in (
                "## Trigger",
                "## Workflow",
                "## Tool selection",
                "## Stop conditions",
                "## Failure fallback",
                "## Output",
                "## Acceptance",
            ):
                self.assertIn(heading, body)
