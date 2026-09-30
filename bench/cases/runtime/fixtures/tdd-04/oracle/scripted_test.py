import asyncio
import unittest

from service import TaskQueue


class CandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_callback_is_skipped(self):
        queue = TaskQueue()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def blocker():
            entered.set()
            await release.wait()
        async def victim():
            calls.append("victim")
        await queue.start()
        try:
            first = queue.submit("first", blocker)
            await asyncio.wait_for(entered.wait(), 2)
            queue.submit("victim", victim)
            self.assertTrue(queue.cancel("victim"))
            release.set()
            await asyncio.wait_for(first, 2)
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(calls, [])
        finally:
            release.set()
            await asyncio.wait_for(queue.close(), 2)
