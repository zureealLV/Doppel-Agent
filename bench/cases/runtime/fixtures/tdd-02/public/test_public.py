import unittest

from service import BoundedQueue


class PublicTests(unittest.TestCase):
    def test_accept_one(self):
        queue = BoundedQueue(2)
        self.assertTrue(queue.submit("first"))
        self.assertEqual(queue.accepted, 1)
        self.assertEqual(queue.finish_next(), "first")
        self.assertEqual(queue.outstanding, 0)


if __name__ == "__main__":
    unittest.main()
