import asyncio
import unittest

from doppel_agent.concurrency import WorkspaceLockManager


class WorkspaceLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_overlap_and_write_waits(self):
        manager = WorkspaceLockManager()
        release = asyncio.Event()
        entered = []

        async def reader(name):
            async with manager.read("."):
                entered.append(name)
                await release.wait()

        first = asyncio.create_task(reader("r1"))
        second = asyncio.create_task(reader("r2"))
        await asyncio.sleep(0.02)
        self.assertCountEqual(entered, ["r1", "r2"])

        async def writer():
            async with manager.write("."):
                entered.append("w")

        waiting = asyncio.create_task(writer())
        await asyncio.sleep(0.02)
        self.assertNotIn("w", entered)
        release.set()
        await asyncio.gather(first, second, waiting)
        self.assertEqual(entered[-1], "w")
