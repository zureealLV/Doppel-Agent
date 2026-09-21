import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from doppel_agent.workspace.process_supervisor import ProcessSupervisor


class ProcessCancellationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_parent_cannot_leave_a_child_process_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "child-finished.txt"
            child = (
                "import pathlib,time; time.sleep(1.5); "
                f"pathlib.Path({str(sentinel)!r}).write_text('orphan', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                "time.sleep(30)"
            )
            supervisor = ProcessSupervisor()
            task = asyncio.create_task(
                supervisor.run(
                    [sys.executable, "-c", parent],
                    cwd=root,
                    run_id="process-tree",
                    timeout_seconds=30,
                )
            )
            for _ in range(100):
                if supervisor.active_count:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(supervisor.active_count, 1)
            await asyncio.sleep(0.2)

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(1.6)

            self.assertFalse(sentinel.exists())
            self.assertEqual(supervisor.active_count, 0)

    async def test_timeout_terminates_process_tree_and_reports_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = ProcessSupervisor()
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                await supervisor.run(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    cwd=root,
                    run_id="process-timeout",
                    timeout_seconds=0.1,
                )
            self.assertEqual(supervisor.active_count, 0)


if __name__ == "__main__":
    unittest.main()
