import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd()))
import helpers
import service


class RefactorTargets(unittest.TestCase):
    def test_normalize_delegates_through_module(self):
        marker = object()
        calls = []
        def replacement(name):
            calls.append(name)
            return marker
        with patch.object(helpers, "normalize_name", side_effect=replacement):
            self.assertIs(service.normalize_name(" Alice "), marker)
        self.assertEqual(calls, [" Alice "])

    def test_greeting_forwards_both_arguments(self):
        marker = object()
        calls = []
        def replacement(name, prefix="Hello"):
            calls.append((name, prefix))
            return marker
        with patch.object(helpers, "format_greeting", side_effect=replacement):
            self.assertIs(service.format_greeting(" Bob ", prefix="Hi"), marker)
        self.assertEqual(calls, [(" Bob ", "Hi")])


if __name__ == "__main__":
    unittest.main()
