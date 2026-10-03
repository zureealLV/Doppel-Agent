import sys
from pathlib import Path
import inspect
import unittest
sys.path.insert(0, str(Path.cwd()))
from service import normalize_name, format_greeting


class CompatibilityRegressions(unittest.TestCase):
    def test_signature_and_errors(self):
        self.assertEqual(str(inspect.signature(format_greeting)), "(name, prefix='Hello')")
        with self.assertRaisesRegex(TypeError, "^prefix must be text$"):
            format_greeting(None, 0)
        with self.assertRaisesRegex(TypeError, "^name must be text$"):
            format_greeting(None, "Hi")

    def test_prefix_empty_and_internal_spaces(self):
        self.assertEqual(format_greeting(" A  B ", " HI:\n"), " HI:\n: a  b")
        self.assertEqual(normalize_name("\u2003"), "")

    def test_unicode_casefold(self):
        self.assertEqual(normalize_name(" Stra\u00dfe "), "strasse")
        self.assertEqual(normalize_name("\u03a3"), "\u03c3")


if __name__ == "__main__":
    unittest.main()
