import unittest

from service import RunStore


class PublicTests(unittest.TestCase):
    def test_first_create(self):
        store = RunStore()
        record, created = store.create("hello", "first")
        self.assertTrue(created)
        self.assertEqual(record["payload"], "hello")
        self.assertEqual(store.rows[record["run_id"]], record)


if __name__ == "__main__":
    unittest.main()
