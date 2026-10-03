import unittest
from service import normalize_name, format_greeting


class PublicTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_name(" Alice "), "alice")

    def test_default_greeting(self):
        self.assertEqual(format_greeting(" Alice "), "Hello: alice")

    def test_prefix(self):
        self.assertEqual(format_greeting(" Bob ", prefix="Hi"), "Hi: bob")

    def test_empty(self):
        self.assertEqual(normalize_name(" "), "")
        self.assertEqual(format_greeting("", ""), ": ")

    def test_bad_types(self):
        with self.assertRaisesRegex(TypeError, "^name must be text$"):
            normalize_name(None)
        with self.assertRaisesRegex(TypeError, "^prefix must be text$"):
            format_greeting("name", None)
