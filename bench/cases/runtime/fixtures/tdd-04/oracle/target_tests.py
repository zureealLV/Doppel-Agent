import asyncio
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path.cwd()))
from service import TaskQueue  # noqa: E402


class TargetTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, survivor):
        queue = TaskQueue()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def blocker():
            entered.set()
            await release.wait()
        async def victim():
            calls.append("victim")
        async def remaining():
            calls.append("survivor")
            return "survived"
        await queue.start()
        try:
            first = queue.submit("first", blocker)
            await asyncio.wait_for(entered.wait(), 2)
            cancelled = queue.submit("victim", victim)
            kept = queue.submit("survivor", remaining) if survivor else None
            self.assertTrue(queue.cancel("victim"))
            self.assertFalse(queue.cancel("victim"))
            release.set()
            await asyncio.wait_for(first, 2)
            await asyncio.wait_for(queue.join(), 2)
            self.assertEqual(calls, ["survivor"] if survivor else [])
            self.assertTrue(cancelled.cancelled())
            self.assertEqual(queue.status["victim"], "cancelled")
            if kept is not None:
                self.assertTrue(kept.done())
                self.assertEqual(kept.result(), "survived")
                self.assertEqual(queue.status["survivor"], "completed")
        finally:
            release.set()
            await asyncio.wait_for(queue.close(), 2)

    async def test_cancelled_queued_callback_is_never_dispatched(self):
        await self.scenario(False)

    async def test_other_queued_work_survives_cancellation(self):
        await self.scenario(True)


if __name__ == "__main__":
    unittest.main()
