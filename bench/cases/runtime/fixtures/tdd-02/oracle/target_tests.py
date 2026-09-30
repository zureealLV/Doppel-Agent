import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import BoundedQueue  # noqa: E402


class TargetTests(unittest.TestCase):
    def test_rejections_are_not_accepted_work(self):
        queue = BoundedQueue(1)
        self.assertTrue(queue.submit("accepted"))
        for _ in range(3):
            self.assertFalse(queue.submit("rejected"))
        self.assertEqual(queue.accepted, 1)
        self.assertEqual(queue.rejected, 3)
        self.assertEqual(queue.attempts, queue.accepted + queue.rejected)
        self.assertEqual(queue.queued, ["accepted"])

    def test_rejection_does_not_leave_phantom_outstanding_jobs(self):
        queue = BoundedQueue(1)
        queue.submit("first")
        queue.submit("rejected")
        self.assertEqual(queue.finish_next(), "first")
        self.assertEqual(queue.outstanding, 0)
        self.assertTrue(queue.submit("replacement"))
        self.assertEqual(queue.outstanding, len(queue.queued))
        self.assertEqual(queue.accepted, 2)


if __name__ == "__main__":
    unittest.main()
