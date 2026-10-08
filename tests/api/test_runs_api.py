import time
import threading
import unittest
import asyncio
from urllib.request import urlopen

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider, ModelTurn, ToolCall
from doppel_agent.web.server import ConsoleServer
from support import workspace


class RunsApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace()
        self.root = self.fixture.__enter__()
        self.addCleanup(lambda: self.fixture.__exit__(None, None, None))

    @staticmethod
    def wait(client, run_id, statuses=("completed", "failed", "interrupted")):
        for _ in range(500):
            record = client.get(f"/api/v1/runs/{run_id}").json()
            if record["status"] in statuses:
                return record
            time.sleep(0.01)
        raise AssertionError(record)

    def test_create_get_and_idempotency_replay(self):
        with TestClient(create_app(self.root, provider=MockProvider()), base_url="http://127.0.0.1") as client:
            body = {
                "prompt": "hello",
                "mode": "graph",
                "idempotency_key": "same-request-key",
            }
            first = client.post("/api/v1/runs", json=body)
            self.assertEqual(first.status_code, 202, first.text)
            replay = client.post("/api/v1/runs", json=body)
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertEqual(first.json()["run_id"], replay.json()["run_id"])
            self.assertTrue(replay.json()["replayed"])
            record = self.wait(client, first.json()["run_id"])
            self.assertEqual(record["status"], "completed")
            self.assertIn("Offline mock", record["answer"])

    def test_unknown_run_is_404(self):
        with TestClient(create_app(self.root, provider=MockProvider()), base_url="http://127.0.0.1") as client:
            self.assertEqual(client.get("/api/v1/runs/missing").status_code, 404)

    def test_deep_mode_runs_through_versioned_api(self):
        with TestClient(create_app(self.root, provider=MockProvider()), base_url="http://127.0.0.1") as client:
            response = client.post(
                "/api/v1/runs",
                json={"prompt": "inspect", "mode": "deep", "permissions": {"delegate": True}},
            )
            self.assertEqual(response.status_code, 202, response.text)
            record = self.wait(client, response.json()["run_id"])
            self.assertEqual(record["status"], "completed", record)
            self.assertNotIn("fallback_runtime", record["metadata"])
            self.assertEqual(record["metadata"]["subagent_limit"], 2)

    def test_async_subagent_lifecycle_is_exposed_by_parent_run(self):
        with TestClient(create_app(self.root, provider=MockProvider()), base_url="http://127.0.0.1") as client:
            parent_response = client.post(
                "/api/v1/runs",
                json={
                    "prompt": "parent task",
                    "mode": "graph",
                    "permissions": {"delegate": True},
                },
            )
            self.assertEqual(parent_response.status_code, 202, parent_response.text)
            parent_id = parent_response.json()["run_id"]
            self.wait(client, parent_id)

            spawned = client.post(
                f"/api/v1/runs/{parent_id}/subagents",
                json={"prompt": "inspect README.md"},
            )
            self.assertEqual(spawned.status_code, 202, spawned.text)
            subagent_id = spawned.json()["subagent_id"]

            for _ in range(500):
                child = client.get(
                    f"/api/v1/runs/{parent_id}/subagents/{subagent_id}"
                ).json()
                if child["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)
            self.assertEqual(child["status"], "completed", child)
            self.assertIn("Offline mock", child["answer"])

            listing = client.get(f"/api/v1/runs/{parent_id}/subagents")
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertEqual([item["subagent_id"] for item in listing.json()], [subagent_id])

            followed = client.post(
                f"/api/v1/runs/{parent_id}/subagents/{subagent_id}/follow-ups",
                json={"prompt": "summarize the previous answer"},
            )
            self.assertEqual(followed.status_code, 202, followed.text)
            self.assertEqual(followed.json()["generation"], 2)
            for _ in range(500):
                child = client.get(
                    f"/api/v1/runs/{parent_id}/subagents/{subagent_id}"
                ).json()
                if child["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)
            self.assertEqual(child["status"], "completed", child)
            self.assertEqual(child["generation"], 2)
            self.assertEqual(len(child["history"]), 1)

            events = client.get(f"/api/v1/runs/{parent_id}/events").json()
            event_types = [event["type"] for event in events]
            self.assertIn("subagent.queued", event_types)
            self.assertIn("subagent.completed", event_types)

    def test_subagent_api_requires_parent_delegate_permission(self):
        with TestClient(create_app(self.root, provider=MockProvider()), base_url="http://127.0.0.1") as client:
            parent = client.post("/api/v1/runs", json={"prompt": "parent"}).json()
            self.wait(client, parent["run_id"])
            response = client.post(
                f"/api/v1/runs/{parent['run_id']}/subagents",
                json={"prompt": "should be denied"},
            )
            self.assertEqual(response.status_code, 403, response.text)

    def test_write_interrupt_resume_and_durable_events(self):
        class WriteProvider:
            calls = 0

            def next_turn(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurn(
                        tool_calls=(
                            ToolCall(
                                "write-1",
                                "propose_patch",
                                {
                                    "changes": [
                                        {"path": "approved.txt", "content": "approved"}
                                    ]
                                },
                            ),
                        )
                    )
                return ModelTurn(content="finished")

        with TestClient(create_app(self.root, provider=WriteProvider()), base_url="http://127.0.0.1") as client:
            response = client.post(
                "/api/v1/runs",
                json={
                    "prompt": "write",
                    "permissions": {"workspace_write": True},
                },
            )
            run_id = response.json()["run_id"]
            paused = self.wait(client, run_id)
            self.assertEqual(paused["status"], "interrupted")
            self.assertFalse((self.root / "approved.txt").exists())
            preview = paused["metadata"]["interrupts"][0]["value"]["tool_calls"][0]
            self.assertIn("approved", preview["arguments"]["_doppel_patch"]["unified_diff"])
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
            resumed = client.post(
                f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
                json={"action": "approve"},
            )
            self.assertEqual(resumed.status_code, 202, resumed.text)
            completed = self.wait(client, run_id, statuses=("completed", "failed"))
            self.assertEqual(completed["status"], "completed", completed)
            self.assertEqual(
                (self.root / "approved.txt").read_text(encoding="utf-8"),
                "approved",
            )
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            event_types = [event["type"] for event in events]
            self.assertIn("approval.requested", event_types)
            self.assertIn("approval.decided", event_types)
            self.assertIn("patch.proposed", event_types)
            self.assertIn("patch.applied", event_types)
            self.assertEqual(
                [event["seq"] for event in events],
                sorted(event["seq"] for event in events),
            )

    def test_deep_write_resume_reuses_the_persisted_reviewed_patch(self):
        class WriteProvider:
            calls = 0

            def next_turn(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return ModelTurn(
                        tool_calls=(
                            ToolCall(
                                "deep-write-1",
                                "propose_patch",
                                {
                                    "changes": [
                                        {"path": "deep-approved.txt", "content": "approved"}
                                    ]
                                },
                            ),
                        )
                    )
                return ModelTurn(content="finished")

        with TestClient(create_app(self.root, provider=WriteProvider()), base_url="http://127.0.0.1") as client:
            response = client.post(
                "/api/v1/runs",
                json={
                    "prompt": "write",
                    "mode": "deep",
                    "permissions": {"workspace_write": True},
                },
            )
            run_id = response.json()["run_id"]
            paused = self.wait(client, run_id)
            self.assertEqual(paused["status"], "interrupted", paused)
            interrupt = paused["metadata"]["interrupts"][0]
            action = interrupt["value"]["action_requests"][0]
            reviewed_patch_id = action["args"]["_doppel_patch"]["patch_id"]

            resumed = client.post(
                f"/api/v1/runs/{run_id}/interrupts/{interrupt['id']}/resume",
                json={"action": "approve"},
            )
            self.assertEqual(resumed.status_code, 202, resumed.text)
            completed = self.wait(client, run_id, statuses=("completed", "failed"))
            self.assertEqual(completed["status"], "completed", completed)
            self.assertEqual(
                (self.root / "deep-approved.txt").read_text(encoding="utf-8"),
                "approved",
            )
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            decided = next(event for event in events if event["type"] == "approval.decided")
            self.assertNotIn("_prepared_interrupts", decided["payload"]["decision"])
            self.assertEqual(action["args"]["_doppel_patch"]["patch_id"], reviewed_patch_id)

    def test_queue_full_returns_429(self):
        class SlowProvider:
            def next_turn(self, messages, tools):
                time.sleep(0.25)
                return ModelTurn(content="done")

        app = create_app(self.root, provider=SlowProvider(), max_active_runs=1, queue_capacity=1)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            first = client.post("/api/v1/runs", json={"prompt": "one"})
            self.assertEqual(first.status_code, 202)
            time.sleep(0.03)
            second = client.post("/api/v1/runs", json={"prompt": "two"})
            self.assertEqual(second.status_code, 202)
            third = client.post("/api/v1/runs", json={"prompt": "three"})
            self.assertEqual(third.status_code, 429, third.text)

    def test_expired_interrupt_is_gone_not_generic_failure(self):
        class WriteProvider:
            def next_turn(self, messages, tools):
                return ModelTurn(
                    tool_calls=(
                        ToolCall(
                            "write-expired",
                            "propose_patch",
                            {"changes": [{"path": "never.txt", "content": "no"}]},
                        ),
                    )
                )

        app = create_app(self.root, provider=WriteProvider(), approval_ttl_seconds=0)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            run_id = client.post(
                "/api/v1/runs",
                json={
                    "prompt": "write",
                    "permissions": {"workspace_write": True},
                },
            ).json()["run_id"]
            paused = self.wait(client, run_id)
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
            response = client.post(
                f"/api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume",
                json={"action": "approve"},
            )
            self.assertEqual(response.status_code, 410, response.text)
            expired = client.get(f"/api/v1/runs/{run_id}").json()
            self.assertEqual(expired["status"], "interrupted_expired")
            self.assertFalse((self.root / "never.txt").exists())

    def test_legacy_ui_is_available_through_same_origin_proxy(self):
        legacy = ConsoleServer(("127.0.0.1", 0), self.root)
        worker = threading.Thread(target=legacy.serve_forever, daemon=True)
        worker.start()
        app = create_app(
            self.root,
            provider=MockProvider(),
            legacy_base_url=f"http://127.0.0.1:{legacy.server_port}",
        )
        try:
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                redirect = client.get("/", follow_redirects=False)
                self.assertEqual(redirect.status_code, 307)
                self.assertEqual(redirect.headers["location"], "/runtime/")
                self.assertEqual(redirect.headers["cache-control"], "no-store")
                default = client.get("/")
                self.assertEqual(default.status_code, 200)
                self.assertEqual(default.url.path, "/runtime/")
                self.assertIn('id="app"', default.text)
                self.assertNotIn("run-form", default.text)
                page = client.get("/legacy/")
                self.assertEqual(page.status_code, 200)
                self.assertIn("run-form", page.text)
                self.assertEqual(client.get("/api/health").json()["status"], "ok")
                self.assertEqual(client.get("/api/v1/health").json()["status"], "ok")
                self.assertEqual(legacy.manager.recent(), [])
                self.assertEqual(app.state.run_service.scheduler.accepted_count, 0)
        finally:
            legacy.shutdown()
            worker.join(timeout=3)
            legacy.server_close()

    def test_vue_workspace_contract_keeps_persistent_chat_and_native_runs_separate(self):
        """Real loopback/proxy/storage integration, not frontend visual proof."""
        legacy = ConsoleServer(("127.0.0.1", 0), self.root)
        worker = threading.Thread(target=legacy.serve_forever, daemon=True)
        worker.start()
        app = create_app(self.root, provider=MockProvider(), legacy_base_url=f"http://127.0.0.1:{legacy.server_port}")
        headers = {"X-Doppel-UI": "1", "Origin": "http://127.0.0.1:8765"}
        try:
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                settings_response = client.post("/api/settings", headers=headers, json={"profile_id": "__new__", "config": {
                    "provider": "mock", "preset": "mock", "name": "Vue offline control", "model": "mock", "base_url": "",
                    "input_price": 0, "output_price": 0, "api_key": "",
                }})
                self.assertEqual(settings_response.status_code, 200, settings_response.text)
                settings = settings_response.json()
                self.assertTrue(all("api_key" not in profile and "api_key_dpapi" not in profile for profile in settings["profiles"]))
                profile_id = settings["active_profile_id"]
                first = client.post("/api/conversations/review-draft", headers=headers, json={}).json()
                second = client.post("/api/conversations/review-draft", headers=headers, json={}).json()
                self.assertEqual(first["id"], second["id"])
                self.assertEqual(legacy.manager.recent(), [])
                self.assertEqual(app.state.run_service.scheduler.accepted_count, 0)
                group = client.post("/api/groups", headers=headers, json={"name": "Vue migration"}).json()
                conversation_id = first["id"]
                self.assertEqual(client.post(f"/api/conversations/{conversation_id}/group", headers=headers,
                                             json={"group_id": group["id"]}).status_code, 200)
                accepted = client.post("/api/runs", headers=headers, json={"prompt": "vue-persistent-context", "mode": "review",
                    "effort": "quick", "conversation_id": conversation_id, "config": {"profile_id": profile_id},
                    "allow_write": False, "allow_command": False, "allow_mcp": False, "allow_delegate": False})
                self.assertEqual(accepted.status_code, 202, accepted.text)
                run_id = accepted.json()["run_id"]
                for _ in range(100):
                    record = client.get(f"/api/runs/{run_id}").json()
                    if record["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.03)
                self.assertEqual(record["status"], "completed")
                saved = client.get(f"/api/conversations/{conversation_id}").json()
                self.assertEqual([message["role"] for message in saved["messages"]], ["user", "assistant"])
                self.assertEqual(saved["profile_id"], profile_id)
                client.post(f"/api/conversations/{conversation_id}/archive", headers=headers, json={"archived": True})
                found = client.get("/api/conversations/search", params={"q": "vue-persistent-context"}).json()
                self.assertEqual(found[0]["id"], conversation_id)
                self.assertEqual(found[0]["archived"], 1)
                self.assertEqual(app.state.run_service.scheduler.accepted_count, 0)
        finally:
            legacy.shutdown()
            worker.join(timeout=3)
            legacy.server_close()

    def test_same_origin_proxy_rejects_foreign_origin_or_host_before_legacy_effects(self):
        legacy = ConsoleServer(("127.0.0.1", 0), self.root)
        worker = threading.Thread(target=legacy.serve_forever, daemon=True)
        worker.start()
        app = create_app(self.root, provider=MockProvider(), legacy_base_url=f"http://127.0.0.1:{legacy.server_port}")
        try:
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                for origin in ("https://attacker.invalid", "http://127.0.0.1:9999", "null"):
                    response = client.post("/api/conversations/review-draft", json={},
                                           headers={"X-Doppel-UI": "1", "Origin": origin})
                    self.assertEqual(response.status_code, 403, response.text)
                forged = client.post("/api/conversations/review-draft", json={}, headers={
                    "X-Doppel-UI": "1", "Host": "attacker.invalid", "Origin": "http://attacker.invalid",
                    "X-Forwarded-Host": "127.0.0.1:8765", "X-Forwarded-Proto": "http",
                })
                self.assertEqual(forged.status_code, 403, forged.text)
                self.assertEqual(client.post("/api/conversations/review-draft", json={},
                                             headers={"X-Doppel-UI": "1", "Host": "attacker.invalid"}).status_code, 403)
                self.assertEqual(client.get("/", headers={"Host": "attacker.invalid"}).status_code, 403)
                self.assertEqual(legacy.manager.conversations.list(), [])
                self.assertEqual(legacy.manager.recent(), [])
                self.assertEqual(app.state.run_service.scheduler.accepted_count, 0)
        finally:
            legacy.shutdown()
            worker.join(timeout=3)
            legacy.server_close()

    def test_compatibility_routes_and_native_hash_pages_preserve_existing_data(self):
        """Real proxy/assets/data seam, not packaged-window or JS interaction proof."""
        legacy = ConsoleServer(("127.0.0.1", 0), self.root)
        old = legacy.manager.conversations.create('Existing Legacy E')
        legacy.manager.conversations.add_message(old['id'], 'user', 'old history sentinel')
        legacy.manager.conversations.add_message(old['id'], 'assistant', 'old answer sentinel')
        worker = threading.Thread(target=legacy.serve_forever, daemon=True)
        worker.start()
        app = create_app(self.root, provider=MockProvider(), legacy_base_url=f"http://127.0.0.1:{legacy.server_port}")
        try:
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                # Compare with independent original-console bytes, not Vue root.
                with urlopen(f'http://127.0.0.1:{legacy.server_port}/', timeout=5) as upstream:
                    original = upstream.read()
                self.assertIn(b'run-form', original)
                for path in ('/legacy', '/legacy/'):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, original)
                    self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
                for name in ('app.js', 'app.css'):
                    self.assertEqual(client.get('/legacy/' + name).content, client.get('/' + name).content)
                for fragment in ('conversations', 'runtime', 'legacy'):
                    page = client.get('/runtime/#' + fragment)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('id="app"', page.text)
                import re
                for asset in re.findall(r'(?:src|href)="(/runtime/assets/[^" ]+)"', page.text):
                    response = client.get(asset)
                    self.assertEqual(response.status_code, 200, asset)
                    self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                visible = client.get('/api/conversations/' + old['id']).json()
                self.assertEqual([m['content'] for m in visible['messages']], ['old history sentinel', 'old answer sentinel'])
                self.assertEqual(client.get('/api/v1/conversations').json(), [])
                self.assertEqual(app.state.run_service.scheduler.accepted_count, 0)
                for path in ('/', '/legacy/', '/runtime/', '/api/v1/health'):
                    self.assertEqual(client.get(path, headers={'Host':'attacker.invalid'}).status_code, 403)
                    self.assertEqual(client.get(path, headers={'Origin':'https://attacker.invalid'}).status_code, 403)
        finally:
            legacy.shutdown()
            worker.join(timeout=3)
            legacy.server_close()

    def test_api_only_root_does_not_redirect_to_unavailable_workspace(self):
        with TestClient(create_app(self.root, provider=MockProvider()), base_url="http://127.0.0.1") as client:
            response = client.get("/", follow_redirects=False)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn("location", response.headers)
            self.assertEqual(client.get("/api/v1/health").json()["status"], "ok")

    def test_default_workspace_does_not_fallback_when_vue_bundle_is_missing(self):
        from unittest.mock import patch

        legacy = ConsoleServer(("127.0.0.1", 0), self.root)
        worker = threading.Thread(target=legacy.serve_forever, daemon=True)
        worker.start()
        app = create_app(self.root, provider=MockProvider(), legacy_base_url=f"http://127.0.0.1:{legacy.server_port}")
        try:
            with patch("doppel_agent.web.server.RUNTIME_ASSET_ROOT", self.root / "missing-bundle"):
                with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                    response = client.get("/")
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json()["error"], "runtime workbench has not been built")
                    self.assertIn("run-form", client.get("/legacy/").text)
                    self.assertEqual(legacy.manager.recent(), [])
                    self.assertEqual(app.state.run_service.scheduler.accepted_count, 0)
        finally:
            legacy.shutdown()
            worker.join(timeout=3)
            legacy.server_close()

    def test_cancel_running_async_provider_emits_one_terminal_event(self):
        entered, observed_cancel = threading.Event(), threading.Event()

        class SlowAsyncProvider:
            async def anext_turn(self, messages, tools):
                entered.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    observed_cancel.set()
                    raise

        app = create_app(self.root, provider=SlowAsyncProvider())
        with TestClient(app, base_url="http://127.0.0.1") as client:
            run_id = client.post("/api/v1/runs", json={"prompt": "slow"}).json()["run_id"]
            # "running" precedes original checkpoint/schema cursor acquisition.
            # This test's subject is an ENTERED provider, not opaque startup
            # cancellation (whose separate tests must retain the owner).
            self.assertTrue(entered.wait(timeout=3))  # Fixture watchdog, not SLA.
            record = client.get(f"/api/v1/runs/{run_id}").json()
            self.assertEqual(record["status"], "running")
            cancelled = client.post(f"/api/v1/runs/{run_id}/cancel")
            self.assertTrue(cancelled.json()["cancel_requested"])
            terminal = self.wait(client, run_id, statuses=("cancelled",))
            self.assertEqual(terminal["status"], "cancelled")
            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            self.assertEqual([event["type"] for event in events].count("run.cancelled"), 1)
            self.assertTrue(observed_cancel.is_set())
        self.assertTrue(app.state.run_service.cleanup_complete)
        self.assertFalse(app.state.run_service._owner.held)
        self.assertFalse(app.state.run_service._provider_receipt_fault.broken)
