import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import normalize_name  # noqa: E402


class TargetTests(unittest.TestCase):
    def test_unicode_canonical_behavior(self):
        for value, expected in [(" Alice ", "alice"), (" Straße ", "strasse"), (" Σ ", "σ")]:
            self.assertEqual(normalize_name(value), expected)

    def test_documented_examples_are_complete_and_executable(self):
        blocks = re.findall(r"```json\s*\n(.*?)\n```", Path("README.md").read_text(encoding="utf-8"), re.S)
        self.assertEqual(len(blocks), 1)
        examples = json.loads(blocks[0])
        self.assertEqual(examples, [{"input": " Alice ", "output": "alice"}, {"input": " Straße ", "output": "strasse"}])
        for example in examples:
            self.assertEqual(normalize_name(example["input"]), example["output"])


if __name__ == "__main__":
    unittest.main()
