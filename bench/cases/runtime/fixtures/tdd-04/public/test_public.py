import asyncio
import unittest

from service import TaskQueue


class PublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_completion(self):
        queue = TaskQueue()
        await queue.start()
        async def callback():
            return "done"
        try:
            future = queue.submit("normal", callback)
            self.assertEqual(await asyncio.wait_for(future, 2), "done")
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(queue.status["normal"], "completed")
        finally:
            await asyncio.wait_for(queue.close(), 2)
