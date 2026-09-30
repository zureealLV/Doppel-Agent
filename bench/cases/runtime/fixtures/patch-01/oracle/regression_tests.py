from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import normalize_name  # noqa: E402


class RegressionTests(unittest.TestCase):
    def test_lowercase_and_internal_space(self):
        self.assertEqual(normalize_name(" a b "), "a b")

    def test_empty_and_unicode_whitespace(self):
        self.assertEqual(normalize_name(" \t\u2003"), "")

    def test_nontext_rejected(self):
        for value in (None, 3, [], b"name"):
            with self.assertRaises(TypeError):
                normalize_name(value)


if __name__ == "__main__":
    unittest.main()
