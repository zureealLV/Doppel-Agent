import unittest
from service import normalize_name


class PublicTests(unittest.TestCase):
    def test_trim(self):
        self.assertEqual(normalize_name(" alice "), "alice")
