import asyncio
import unittest

from doppel_agent.concurrency import AsyncRunScheduler, QueueCapacityError


class SchedulerLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_jobs_never_exceed_four_active(self):
        scheduler = AsyncRunScheduler(max_active=4, queue_capacity=100)
        await scheduler.start()
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def operation(token):
            nonlocal active, peak
            token.raise_if_cancelled()
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.005)
            async with lock:
                active -= 1
            return "ok"

        handles = [await scheduler.submit(f"load-{index}", operation) for index in range(100)]
        results = await asyncio.gather(*(handle.future for handle in handles))
        await scheduler.shutdown()

        self.assertEqual(results, ["ok"] * 100)
        self.assertLessEqual(peak, 4)
        self.assertEqual(scheduler.peak_active_count, peak)

    async def test_queue_full_is_counted_and_rejected(self):
        scheduler = AsyncRunScheduler(max_active=1, queue_capacity=1)
        await scheduler.start()
        gate = asyncio.Event()

        async def operation(token):
            await gate.wait()

        first = await scheduler.submit("running", operation)
        for _ in range(100):
            if scheduler.active_count == 1:
                break
            await asyncio.sleep(0.001)
        second = await scheduler.submit("queued", operation)
        with self.assertRaises(QueueCapacityError):
            await scheduler.submit("rejected", operation)
        self.assertEqual(scheduler.rejected_count, 1)
        gate.set()
        await asyncio.gather(first.future, second.future)
        await scheduler.shutdown()


if __name__ == "__main__":
    unittest.main()
