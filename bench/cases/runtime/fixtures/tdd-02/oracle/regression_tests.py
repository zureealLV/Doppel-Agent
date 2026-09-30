import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import BoundedQueue  # noqa: E402


class RegressionTests(unittest.TestCase):
    def test_fifo_is_preserved(self):
        queue = BoundedQueue(3)
        for value in ("a", "b", "c"):
            self.assertTrue(queue.submit(value))
        self.assertEqual([queue.finish_next() for _ in range(3)], ["a", "b", "c"])
        self.assertEqual(queue.completed, 3)

    def test_zero_capacity_accepts_no_payload(self):
        queue = BoundedQueue(0)
        self.assertFalse(queue.submit("x"))
        self.assertEqual(queue.queued, [])
        self.assertEqual(queue.rejected, 1)

    def test_instances_and_capacity_validation(self):
        left, right = BoundedQueue(1), BoundedQueue(1)
        left.submit("left")
        self.assertEqual(right.queued, [])
        self.assertEqual(right.attempts, 0)
        with self.assertRaises(ValueError):
            BoundedQueue(-1)


if __name__ == "__main__":
    unittest.main()
