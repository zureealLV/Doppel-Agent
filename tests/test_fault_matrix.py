import unittest

from bench.fault_matrix import SCENARIOS, run_fault_matrix


class FaultMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_matrix_records_denominator_and_raw_outcomes(self):
        report = await run_fault_matrix()

        self.assertEqual(report["denominator"], 10)
        self.assertEqual(report["denominator"], len(SCENARIOS))
        self.assertEqual(report["passed_count"], 10)
        self.assertEqual(report["failed_count"], 0)
        self.assertTrue(report["passed"])
        self.assertEqual(
            set(report["scenarios"]),
            {
                "provider_429_retry_after",
                "provider_connection_refused",
                "provider_read_timeout",
                "provider_5xx",
                "provider_half_open",
                "mcp_disconnect_no_retry",
                "mcp_schema_reconnect",
                "sse_slow_replay",
                "restart_lease_recovery",
                "process_tree_timeout",
            },
        )
        for result in report["scenarios"].values():
            self.assertIn("injected_fault", result)
            self.assertIn("expected", result)
            self.assertIn("observed", result)
            self.assertIn("failure_reason", result)


if __name__ == "__main__":
    unittest.main()
