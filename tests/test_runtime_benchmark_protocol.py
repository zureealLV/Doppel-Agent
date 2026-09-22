import json
import unittest
from pathlib import Path

from bench.runtime_matrix import EXPECTED_CATEGORY_COUNTS, RuntimeMatrix


ROOT = Path(__file__).resolve().parents[1]


class RuntimeBenchmarkProtocolTests(unittest.TestCase):
    def test_fixed_matrix_contains_20_cases_three_runtimes_and_three_repeats(self):
        matrix = RuntimeMatrix.load(ROOT / "bench" / "cases" / "runtime" / "manifest.json")

        self.assertEqual(len(matrix.cases), 20)
        self.assertEqual(matrix.category_counts, EXPECTED_CATEGORY_COUNTS)
        runs = matrix.expand()
        self.assertEqual(len(runs), 180)
        self.assertEqual({run.runtime for run in runs}, {"legacy", "graph", "deep"})
        self.assertEqual({run.repeat for run in runs}, {1, 2, 3})
        self.assertEqual(len({run.run_key for run in runs}), 180)

    def test_manifest_has_deterministic_validators_and_no_prefilled_scores(self):
        path = ROOT / "bench" / "cases" / "runtime" / "manifest.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        matrix = RuntimeMatrix.load(path)

        self.assertNotIn("results", raw)
        self.assertNotIn("success_rate", raw)
        for case in matrix.cases:
            self.assertTrue(case.prompt.strip())
            self.assertIn(case.validator["type"], {"answer_contains", "event_present"})
            self.assertTrue(case.validator.get("value"))


if __name__ == "__main__":
    unittest.main()
