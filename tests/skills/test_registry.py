import tempfile
import unittest
from pathlib import Path

from doppel_agent.skills import SkillRegistry, SkillValidationError


def write_skill(root: Path, name: str, description: str, body: str = "Use read tools first."):
    directory = root / ".doppel" / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )


class SkillRegistryTests(unittest.TestCase):
    def test_catalog_then_lazy_instructions_and_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "review", "Review changed code and cite evidence")
            registry = SkillRegistry(root)
            self.assertEqual(registry.catalog(), ({"name": "review", "description": "Review changed code and cite evidence"},))
            self.assertIn("read tools", registry.get("review").instructions())
            self.assertEqual(registry.deep_agent_sources(), ["/.doppel/skills/"])

    def test_rejects_duplicate_names_and_plaintext_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "review", "Review code")
            built_in = root / "skills" / "review"
            built_in.mkdir(parents=True)
            (built_in / "SKILL.md").write_text(
                "---\nname: review\ndescription: Duplicate\n---\nInspect.", encoding="utf-8"
            )
            with self.assertRaisesRegex(SkillValidationError, "duplicate"):
                SkillRegistry(root).list()

            (built_in / "SKILL.md").unlink()
            built_in.rmdir()
            secret = root / "skills" / "secret"
            secret.mkdir()
            (secret / "SKILL.md").write_text(
                "---\nname: secret\ndescription: Bad\n---\napi_key=sk-abcdefghijklmnop1234",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SkillValidationError, "secret"):
                SkillRegistry(root).list()

    def test_rejects_supporting_file_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "review", "Review code", "Read [outside](../secret.txt).")
            with self.assertRaisesRegex(SkillValidationError, "escapes"):
                SkillRegistry(root).list()
