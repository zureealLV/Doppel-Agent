import unittest
from service import normalize_name, format_greeting


class PublicBehavior(unittest.TestCase):
    def test_short_name(self):
        self.assertEqual(normalize_name(" A "), "a")

    def test_prefix(self):
        self.assertEqual(format_greeting(" B ", " Hi "), " Hi : b")

    def test_empty(self):
        self.assertEqual(format_greeting("  "), "Hello: ")

    def test_types(self):
        with self.assertRaisesRegex(TypeError, "^name must be text$"):
            normalize_name(0)
        with self.assertRaisesRegex(TypeError, "^prefix must be text$"):
            format_greeting(None, 0)


if __name__ == "__main__":
    unittest.main()
