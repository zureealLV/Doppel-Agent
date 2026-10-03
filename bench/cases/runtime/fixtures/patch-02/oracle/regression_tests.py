import sys
from pathlib import Path
import inspect
import unittest
sys.path.insert(0, str(Path.cwd()))
from service import normalize_name, format_greeting


class BehaviorRegressions(unittest.TestCase):
    def test_unicode_and_whitespace(self):
        for name, expected in [(" Stra\u00dfe ", "strasse"), ("\u03a3", "\u03c3"), (" A  B ", "a  b"), ("\u2003", "")]:
            with self.subTest(name=name):
                self.assertEqual(normalize_name(name), expected)
                self.assertEqual(format_greeting(name), "Hello: " + expected)

    def test_exact_errors_and_priority(self):
        for call, message in [(lambda: normalize_name(b"a"), "name must be text"), (lambda: format_greeting(None, "Hi"), "name must be text"), (lambda: format_greeting(None, 0), "prefix must be text")]:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TypeError, "^" + message + "$"):
                    call()

    def test_prefix_is_not_normalized(self):
        for prefix in ["", " Hi ", "A:B", "LINE\n"]:
            self.assertEqual(format_greeting(" Alice ", prefix), prefix + ": alice")

    def test_signature_positional_and_keyword_compatibility(self):
        self.assertEqual(str(inspect.signature(normalize_name)), "(name)")
        self.assertEqual(str(inspect.signature(format_greeting)), "(name, prefix='Hello')")
        self.assertEqual(format_greeting(name=" A ", prefix="X"), format_greeting(" A ", "X"))

    def test_calls_do_not_share_state_or_poison_later_calls(self):
        self.assertEqual(normalize_name(" A "), "a")
        with self.assertRaises(TypeError):
            normalize_name(0)
        self.assertEqual(normalize_name(" B "), "b")
        self.assertEqual(format_greeting(" A ", "First"), "First: a")
        self.assertEqual(format_greeting(" B ", "Second"), "Second: b")


if __name__ == "__main__":
    unittest.main()
