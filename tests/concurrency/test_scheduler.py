import asyncio
import unittest

from doppel_agent.concurrency import AsyncRunScheduler, QueueCapacityError


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_fifo_and_active_limit(self):
        scheduler = AsyncRunScheduler(max_active=1, queue_capacity=2)
        await scheduler.start()
        gate = asyncio.Event()
        order = []

        async def job(name, token):
            order.append(f"start-{name}")
            if name == "one":
                await gate.wait()
            token.raise_if_cancelled()
            order.append(f"end-{name}")
            return name

        one = await scheduler.submit("one", lambda token: job("one", token))
        await asyncio.sleep(0)
        two = await scheduler.submit("two", lambda token: job("two", token))
        three = await scheduler.submit("three", lambda token: job("three", token))
        with self.assertRaises(QueueCapacityError):
            await scheduler.submit("four", lambda token: job("four", token))
        self.assertEqual(scheduler.active_count, 1)
        gate.set()
        self.assertEqual(await one.future, "one")
        self.assertEqual(await two.future, "two")
        self.assertEqual(await three.future, "three")
        self.assertEqual(
            order,
            [
                "start-one",
                "end-one",
                "start-two",
                "end-two",
                "start-three",
                "end-three",
            ],
        )
        await scheduler.shutdown()

    async def test_cancel_running_job_propagates_cancelled_error(self):
        scheduler = AsyncRunScheduler(max_active=1, queue_capacity=2)
        await scheduler.start()
        started = asyncio.Event()

        async def job(token):
            started.set()
            await asyncio.sleep(30)

        handle = await scheduler.submit("cancel-me", job)
        await started.wait()
        self.assertTrue(await scheduler.cancel("cancel-me"))
        with self.assertRaises(asyncio.CancelledError):
            await handle.future
        self.assertEqual(scheduler.status("cancel-me"), "cancelled")
        await scheduler.shutdown()
