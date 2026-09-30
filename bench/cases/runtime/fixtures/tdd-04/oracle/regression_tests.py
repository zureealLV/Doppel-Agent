import asyncio
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import TaskQueue  # noqa: E402


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifo_completion(self):
        queue = TaskQueue()
        await queue.start()
        order = []
        async def one():
            order.append(1)
        async def two():
            order.append(2)
        try:
            first, second = queue.submit("one", one), queue.submit("two", two)
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(order, [1, 2])
            self.assertTrue(first.done() and second.done())
        finally:
            await asyncio.wait_for(queue.close(), 2)

    async def test_unknown_cancellation_has_no_effects(self):
        queue = TaskQueue()
        await queue.start()
        try:
            self.assertFalse(queue.cancel("absent"))
            self.assertEqual(queue.futures, {})
            self.assertEqual(queue.status, {})
        finally:
            await asyncio.wait_for(queue.close(), 2)

    async def test_running_and_completed_tasks_cannot_be_cancelled_as_queued(self):
        queue = TaskQueue()
        entered, release = asyncio.Event(), asyncio.Event()
        async def callback():
            entered.set()
            await release.wait()
            return "completed"
        await queue.start()
        try:
            future = queue.submit("running", callback)
            await asyncio.wait_for(entered.wait(), 2)
            self.assertFalse(queue.cancel("running"))
            release.set()
            self.assertEqual(await asyncio.wait_for(future, 2), "completed")
            await asyncio.wait_for(queue.join(), 2)
            self.assertFalse(queue.cancel("running"))
            self.assertEqual(queue.status["running"], "completed")
        finally:
            release.set()
            await asyncio.wait_for(queue.close(), 2)


if __name__ == "__main__":
    unittest.main()
