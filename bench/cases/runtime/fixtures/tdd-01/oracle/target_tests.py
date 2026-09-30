import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import RunStore  # noqa: E402


class TargetTests(unittest.TestCase):
    def test_replays_do_not_allocate_or_queue_again(self):
        store = RunStore()
        original, first = store.create("first", "same")
        for _ in range(3):
            replay, created = store.create("first", "same")
            self.assertEqual(replay, original)
            self.assertFalse(created)
        self.assertTrue(first)
        self.assertEqual(len(store.rows), 1)
        self.assertEqual(store.next_id, 2)

    def test_replay_preserves_original_payload(self):
        store = RunStore()
        original, _ = store.create("original", "key")
        replay, created = store.create("different", "key")
        self.assertEqual(replay, original)
        self.assertFalse(created)
        self.assertEqual(replay["payload"], "original")
        self.assertEqual(len(store.rows), 1)


if __name__ == "__main__":
    unittest.main()
