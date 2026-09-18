import unittest

from doppel_agent.skills.loader import SkillLoader
from support import workspace


class SkillLoaderTests(unittest.TestCase):
    def test_validates_and_reads_workspace_skill(self):
        with workspace() as root:
            skills = root / ".doppel" / "skills"
            good = skills / "review"
            good.mkdir(parents=True)
            (good / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review code changes\n---\nInspect diff before commenting.",
                encoding="utf-8",
            )
            bad = skills / "bad"
            bad.mkdir()
            (bad / "SKILL.md").write_text("not frontmatter", encoding="utf-8")
            loader = SkillLoader(root)
            self.assertEqual([skill.name for skill in loader.list()], ["review"])
            self.assertIn("Inspect diff", loader.get("review").content)
            with self.assertRaises(ValueError):
                loader.get("../review")
