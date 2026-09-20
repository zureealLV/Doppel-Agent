import time
import unittest

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider
from support import workspace


class SseReplayTests(unittest.TestCase):
    def test_after_seq_replays_only_newer_durable_events(self):
        with workspace() as root, TestClient(create_app(root, provider=MockProvider())) as client:
            run_id = client.post("/api/v1/runs", json={"prompt": "hello"}).json()["run_id"]
            for _ in range(100):
                record = client.get(f"/api/v1/runs/{run_id}").json()
                if record["status"] == "completed":
                    break
                time.sleep(0.01)
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            self.assertGreaterEqual(len(events), 4)
            pivot = events[1]["seq"]
            replay = client.get(f"/api/v1/runs/{run_id}/events?after_seq={pivot}").json()
            self.assertEqual(replay, [event for event in events if event["seq"] > pivot])

            streamed = client.get(f"/api/v1/runs/{run_id}/events?after_seq={pivot}&stream=true")
            self.assertEqual(streamed.status_code, 200)
            self.assertIn("text/event-stream", streamed.headers["content-type"])
            self.assertIn(f"id: {replay[0]['seq']}", streamed.text)
