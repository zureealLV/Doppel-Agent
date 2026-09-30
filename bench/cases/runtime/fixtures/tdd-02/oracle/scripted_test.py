import unittest

from service import BoundedQueue


class CandidateTests(unittest.TestCase):
    def test_queue_full_is_not_an_accept(self):
        queue = BoundedQueue(1)
        queue.submit("first")
        self.assertFalse(queue.submit("second"))
        self.assertEqual(queue.accepted, 1)
        self.assertEqual(queue.rejected, 1)


if __name__ == "__main__":
    unittest.main()
