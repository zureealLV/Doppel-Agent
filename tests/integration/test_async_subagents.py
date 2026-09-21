import asyncio
import tempfile
import unittest
from pathlib import Path

from doppel_agent.runtime.async_subagents import AsyncSubagentManager


class AsyncSubagentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_subagents_are_bounded_queryable_and_read_only(self):
        active = 0
        peak = 0
        requests = []
        gate = asyncio.Event()

        async def runner(request):
            nonlocal active, peak
            requests.append(request)
            active += 1
            peak = max(peak, active)
            await gate.wait()
            active -= 1
            return f"evidence:{request.prompt}"

        with tempfile.TemporaryDirectory() as directory:
            manager = AsyncSubagentManager(
                Path(directory) / "subagents.sqlite3",
                runner,
                max_active=2,
                max_per_parent=4,
            )
            records = [await manager.spawn("parent", f"inspect {index}") for index in range(4)]
            for _ in range(100):
                if active == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(active, 2)
            self.assertEqual(peak, 2)
            queued = await manager.get(records[-1]["subagent_id"])
            self.assertEqual(queued["status"], "queued")

            gate.set()
            completed = [await manager.wait(item["subagent_id"]) for item in records]

            self.assertTrue(all(item["status"] == "completed" for item in completed))
            self.assertTrue(all(item.permissions == frozenset({"workspace_read"}) for item in requests))
            self.assertTrue(all(not item.allow_delegate for item in requests))
            await manager.close()

    async def test_completed_subagent_can_be_followed_up_with_bounded_history(self):
        seen = []

        async def runner(request):
            seen.append(request)
            return f"answer-{len(seen)}"

        with tempfile.TemporaryDirectory() as directory:
            manager = AsyncSubagentManager(Path(directory) / "subagents.sqlite3", runner)
            record = await manager.spawn("parent", "first")
            first = await manager.wait(record["subagent_id"])
            self.assertEqual(first["answer"], "answer-1")

            followed = await manager.follow_up(record["subagent_id"], "clarify")
            second = await manager.wait(followed["subagent_id"])

            self.assertEqual(second["generation"], 2)
            self.assertEqual(second["answer"], "answer-2")
            self.assertEqual(seen[1].history[0]["prompt"], "first")
            self.assertEqual(seen[1].history[0]["answer"], "answer-1")
            await manager.close()

    async def test_running_subagent_can_be_cancelled(self):
        started = asyncio.Event()

        async def runner(request):
            started.set()
            await asyncio.sleep(30)
            return "never"

        with tempfile.TemporaryDirectory() as directory:
            manager = AsyncSubagentManager(Path(directory) / "subagents.sqlite3", runner)
            record = await manager.spawn("parent", "wait")
            await started.wait()

            self.assertTrue(await manager.cancel(record["subagent_id"]))
            cancelled = await manager.wait(record["subagent_id"])

            self.assertEqual(cancelled["status"], "cancelled")
            await manager.close()

    async def test_parent_budget_is_atomic_under_concurrent_spawn(self):
        gate = asyncio.Event()

        async def runner(request):
            await gate.wait()
            return "done"

        with tempfile.TemporaryDirectory() as directory:
            manager = AsyncSubagentManager(
                Path(directory) / "subagents.sqlite3",
                runner,
                max_per_parent=2,
            )
            results = await asyncio.gather(
                *(manager.spawn("same-parent", f"child-{index}") for index in range(3)),
                return_exceptions=True,
            )
            accepted = [item for item in results if isinstance(item, dict)]
            rejected = [item for item in results if isinstance(item, ValueError)]
            try:
                self.assertEqual(len(accepted), 2)
                self.assertEqual(len(rejected), 1)
            finally:
                gate.set()
            await asyncio.gather(*(manager.wait(item["subagent_id"]) for item in accepted))
            await manager.close()


if __name__ == "__main__":
    unittest.main()
