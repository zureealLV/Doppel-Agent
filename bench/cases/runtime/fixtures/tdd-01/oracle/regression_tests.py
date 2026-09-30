import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import RunStore  # noqa: E402


class RegressionTests(unittest.TestCase):
    def test_distinct_keys_create_distinct_rows(self):
        store = RunStore()
        a, first = store.create("a", "key-a")
        b, second = store.create("b", "key-b")
        self.assertNotEqual(a["run_id"], b["run_id"])
        self.assertTrue(first and second)
        self.assertEqual(len(store.rows), 2)

    def test_no_key_is_not_deduplicated(self):
        store = RunStore()
        a, _ = store.create("a")
        b, _ = store.create("a")
        self.assertNotEqual(a["run_id"], b["run_id"])

    def test_store_instances_do_not_share_keys(self):
        left, right = RunStore(), RunStore()
        left.create("left", "same")
        row, created = right.create("right", "same")
        self.assertTrue(created)
        self.assertEqual(row["payload"], "right")


if __name__ == "__main__":
    unittest.main()
