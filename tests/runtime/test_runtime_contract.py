import asyncio
import unittest

from support import workspace

from doppel_agent.provider import MockProvider
from doppel_agent.runtime import (
    NullEventSink,
    ResumeCommand,
    RunRequest,
    create_runtime,
)


class RecordingSink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    def test_request_validates_prompt(self):
        with self.assertRaises(ValueError):
            RunRequest("   ")
        with self.assertRaisesRegex(ValueError, "lowercase hexadecimal"):
            RunRequest("hello", run_id="run-1")

    def test_factory_rejects_unknown_runtime(self):
        with self.assertRaisesRegex(ValueError, "unsupported runtime mode"):
            create_runtime("missing", self.root, MockProvider())

    def test_legacy_runtime_fulfils_async_contract(self):
        sink = RecordingSink()
        request = RunRequest("hello", run_id="a1", thread_id="thread-1")
        runtime = create_runtime("legacy", self.root, MockProvider())

        result = asyncio.run(runtime.run(request, sink))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.runtime, "legacy")
        self.assertEqual(result.thread_id, "thread-1")
        self.assertEqual([kind for kind, _ in sink.events], ["runtime.started", "runtime.finished"])

    def test_legacy_runtime_declares_unsupported_control_operations(self):
        runtime = create_runtime("legacy", self.root, MockProvider())
        self.assertFalse(runtime.supports_resume)
        self.assertFalse(runtime.supports_cancel)
        with self.assertRaisesRegex(RuntimeError, "does not support resume"):
            asyncio.run(runtime.resume(ResumeCommand("a1", "thread-1", True), NullEventSink()))
        with self.assertRaisesRegex(RuntimeError, "does not support cancellation"):
            asyncio.run(runtime.cancel("run-1"))
