import json
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path.cwd()))
from service import normalize_name


class ConfigurationTargets(unittest.TestCase):
    def test_reviewed_configuration_contract(self):
        self.assertEqual(json.loads(Path("settings.json").read_bytes()),
                         {"max_name_length": 8, "trim_names": True})

    def test_implementation_reads_current_configuration(self):
        path = Path("settings.json")
        original = path.read_bytes()
        settings = json.loads(original)
        canonical = "ABCDEFGHIJK".casefold()
        self.assertEqual(normalize_name("ABCDEFGHIJK"), canonical[:settings["max_name_length"]])
        try:
            path.write_bytes(b'{"max_name_length": 3, "trim_names": false}\n')
            self.assertEqual(normalize_name(" ABCDE "), " ab")
            path.write_bytes(b'{"max_name_length": 3, "trim_names": true}\n')
            self.assertEqual(normalize_name(" ABCDE "), "abc")
        finally:
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
