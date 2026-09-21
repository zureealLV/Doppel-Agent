import tempfile
import unittest
from pathlib import Path

from doppel_agent.skills import SkillRegistry, SkillResolver

from .test_registry import write_skill


class SkillResolverTests(unittest.TestCase):
    def test_explicit_and_lexical_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "bugfix", "Reproduce and repair a concrete bug")
            write_skill(root, "review", "Review code changes for regressions")
            resolver = SkillResolver(SkillRegistry(root))
            self.assertEqual(resolver.resolve("Please review these code changes")[0].name, "review")
            self.assertEqual(resolver.resolve("Use $bugfix now")[0].name, "bugfix")
