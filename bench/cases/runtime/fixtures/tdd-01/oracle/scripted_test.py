import unittest

from service import RunStore


class CandidateTests(unittest.TestCase):
    def test_duplicate_key_creates_only_once(self):
        store = RunStore()
        original, _ = store.create("payload", "replay")
        replay, created = store.create("payload", "replay")
        self.assertFalse(created)
        self.assertEqual(replay, original)
        self.assertEqual(len(store.rows), 1)


if __name__ == "__main__":
    unittest.main()
