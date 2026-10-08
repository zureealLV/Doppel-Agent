"""Owned scheduler/frozen-input fixture definitions; execution deferred to S9."""

import asyncio

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.base import RunRequest, RuntimeResult
from doppel_agent.runtime.service import RunService


def test_queued_execution_uses_durable_acceptance_input_not_later_files_notes_or_picks(tmp_path, monkeypatch):
    async def scenario():
        (tmp_path / "source.txt").write_text("frozen file marker", encoding="utf-8")
        service = RunService(tmp_path, provider=MockProvider(), max_active_runs=1, queue_capacity=3)
        entered, release = asyncio.Event(), asyncio.Event()
        received = []

        class RecordingRuntime:
            async def run(self, request: RunRequest, sink):
                received.append(request)
                if request.prompt == "hold first turn":
                    entered.set()
                    await release.wait()
                return RuntimeResult(request.run_id, request.thread_id, "completed", "fixture", "scripted")

        async def runtime(_record):
            return RecordingRuntime()

        monkeypatch.setattr(service, "_runtime", runtime)
        await service.start()
        try:
            first, _ = await service.create({"mode": "graph", "prompt": "hold first turn", "permissions": {}})
            await asyncio.wait_for(entered.wait(), 10)
            files = [{"path": "source.txt"}]
            preview = await service.context_preview(files, 65536)
            manifest = await service.context_accept([{**files[0], "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
                budget_bytes=65536, confirmed=True, idempotency_key="queue-input-manifest")
            body = {"kind": "constraint", "title": "fixture", "body": "frozen note marker", "sources": [{"kind": "user", "label": "human"}]}
            note = await service.create_project_note(body, confirmed=True, idempotency_key="queue-input-note")
            payload = {"mode": "graph", "prompt": "second raw prompt", "permissions": {}, "idempotency_key": "queue-input-turn",
                "context": {"manifest_id": manifest["manifest_id"], "notes": [{"note_id": note["note_id"], "revision": 1}]}}
            second, fresh = await service.create(payload)
            assert fresh and second["status"] == "queued" and len(received) == 1
            (tmp_path / "source.txt").write_text("changed after acceptance", encoding="utf-8")
            await service.update_project_note(note["note_id"], {**body, "body": "changed after acceptance"},
                expected_revision=1, confirmed=True, idempotency_key="queue-input-note-change")
            replay, fresh = await service.create(payload)
            assert not fresh and replay["input_snapshot"] == second["input_snapshot"]
            release.set()
            await service.scheduler.wait(first["run_id"])
            await service.scheduler.wait(second["run_id"])
            assert received[1].prompt == "second raw prompt"
            assert received[1].input_prompt.count("frozen file marker") == received[1].input_prompt.count("frozen note marker") == 1
            assert "changed after acceptance" not in received[1].input_prompt
            assert (await service.get(second["run_id"]))["input_snapshot"] == second["input_snapshot"]
        finally:
            release.set()
            await service.close()

    asyncio.run(scenario())
