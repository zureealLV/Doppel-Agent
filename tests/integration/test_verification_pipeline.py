import json
import sys
import tempfile
import unittest
from pathlib import Path

from doppel_agent.workspace.verification import VerificationPipeline


class VerificationPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def configure(root: Path, commands: list[dict], **options) -> None:
        config = root / ".doppel" / "verification.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"commands": commands, **options}), encoding="utf-8")

    async def test_runs_only_named_allowlisted_argv_and_returns_structured_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.configure(
                root,
                [
                    {
                        "name": "pass",
                        "argv": [sys.executable, "-c", "print('verified')"],
                        "timeout_seconds": 5,
                    },
                    {
                        "name": "fail",
                        "argv": [sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"],
                        "timeout_seconds": 5,
                    },
                ],
                stop_on_failure=False,
                max_output_bytes=1024,
            )
            pipeline = VerificationPipeline(root)

            report = await pipeline.run(["pass", "fail"], run_id="verification")

            self.assertFalse(report.success)
            self.assertEqual([item.name for item in report.results], ["pass", "fail"])
            self.assertTrue(report.results[0].success)
            self.assertIn("verified", report.results[0].stdout)
            self.assertEqual(report.results[1].exit_code, 3)
            self.assertGreaterEqual(report.results[0].duration_ms, 0)
            self.assertIn(
                report.results[0].supervision,
                {"job_object", "taskkill_fallback", "process_group"},
            )

    async def test_rejects_a_model_supplied_command_not_in_project_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.configure(
                root,
                [
                    {
                        "name": "safe",
                        "argv": [sys.executable, "-c", "print('safe')"],
                        "timeout_seconds": 5,
                    }
                ],
            )
            pipeline = VerificationPipeline(root)

            with self.assertRaisesRegex(PermissionError, "allowlist"):
                await pipeline.run(["rm -rf ."], run_id="verification")

    async def test_timeout_is_structured_and_does_not_leave_a_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.configure(
                root,
                [
                    {
                        "name": "slow",
                        "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                        "timeout_seconds": 0.1,
                    }
                ],
            )
            pipeline = VerificationPipeline(root)

            report = await pipeline.run(run_id="verification")

            self.assertFalse(report.success)
            self.assertIsNone(report.results[0].exit_code)
            self.assertIn("timed out", report.results[0].error)
            self.assertEqual(pipeline.supervisor.active_count, 0)


if __name__ == "__main__":
    unittest.main()
