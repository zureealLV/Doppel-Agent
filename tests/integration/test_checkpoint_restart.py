import asyncio
import unittest

from support import workspace

from doppel_agent.provider import ModelTurn
from doppel_agent.runtime import RunRequest, create_runtime


class DirectProvider:
    def next_turn(self, messages, tools):
        return ModelTurn(content="durable answer")


class CheckpointRestartTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def test_graph_state_survives_runtime_recreation(self):
        state_root = self.root / "state"
        request = RunRequest("hello", run_id="a1", thread_id="durable-thread")
        first = create_runtime("graph", self.root, DirectProvider(), state_root=state_root)

        result = asyncio.run(first.run(request))
        self.assertEqual(result.status, "completed")

        second = create_runtime("graph", self.root, DirectProvider(), state_root=state_root)
        restored = asyncio.run(second.state("durable-thread"))

        self.assertEqual(restored["status"], "completed")
        self.assertEqual(restored["answer"], "durable answer")
        self.assertEqual(restored["run_id"], "a1")
        self.assertTrue((state_root / "checkpoints.sqlite3").is_file())
