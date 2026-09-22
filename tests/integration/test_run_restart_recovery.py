import unittest

from doppel_agent.runtime.service import RunService
from support import workspace


class RunRestartRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_atomically_fails_incomplete_runs_and_appends_terminal_events(self):
        with workspace() as root:
            crashed = RunService(root)
            queued, _ = crashed.runs.create(
                "queued-before-crash",
                "thread-queued",
                "graph",
                {"prompt": "queued"},
                None,
            )
            running, _ = crashed.runs.create(
                "running-before-crash",
                "thread-running",
                "deep",
                {"prompt": "running"},
                None,
            )
            running = crashed.runs.update(running["run_id"], "running")
            crashed.events.append(
                running["run_id"], running["thread_id"], "run.started", {}
            )

            restarted = RunService(root)
            await restarted.start()
            try:
                for original in (queued, running):
                    recovered = await restarted.get(original["run_id"])
                    self.assertEqual(recovered["status"], "failed")
                    self.assertEqual(
                        recovered["error"], "service restarted before completion"
                    )
                    events = await restarted.list_events(original["run_id"])
                    self.assertEqual(events[-1]["type"], "run.recovered_after_restart")
                    self.assertEqual(events[-1]["payload"]["previous"], original["status"])
                    self.assertEqual(events[-1]["payload"]["status"], "failed")
            finally:
                await restarted.close()

    async def test_repeated_start_does_not_reconcile_current_generation(self):
        with workspace() as root:
            service = RunService(root)
            await service.start()
            try:
                record, _ = service.runs.create(
                    "current-generation",
                    "thread-current",
                    "graph",
                    {"prompt": "current"},
                    None,
                )
                await service.start()
                current = await service.get(record["run_id"])
                self.assertEqual(current["status"], "queued")
                self.assertEqual(await service.list_events(record["run_id"]), [])
            finally:
                await service.close()


if __name__ == "__main__":
    unittest.main()
