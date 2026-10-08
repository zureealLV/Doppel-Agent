"""S7 C Skills + original Deep engine definitions, ALL UNRUN until S9.

Header-only means no instructions returned/executed: original registry validation
reads SKILL.md locally. Per-run engine reads are not frozen to the UI catalog.
"""

import asyncio

import httpx
import pytest

from doppel_agent.api import create_app
from doppel_agent.provider import ModelTurn, ToolCall


class SkillsProvider:
    def __init__(self, load):
        self.load = load
        self.seen = []

    def next_turn(self, messages, tools):
        self.seen.append(tuple(messages))
        combined = "\n".join(item.content for item in messages)
        if messages[-1].role == "tool":
            assert "SKILL_BODY_ORACLE" in messages[-1].content
            return ModelTurn(content="explicitly loaded skill")
        assert "evidence" in combined and "Use local evidence fixture" in combined
        assert "SKILL_BODY_ORACLE" not in combined
        assert any(tool["function"]["name"] == "read_file" for tool in tools)
        if not self.load:
            return ModelTurn(content="headers only; no skill load")
        return ModelTurn(tool_calls=(ToolCall("s7-skill-read", "read_file", {"file_path": "/skills/evidence/SKILL.md"}),))


@pytest.mark.parametrize("load", [False, True])
def test_extension_headers_and_original_deep_progressive_instruction_read(tmp_path, load):
    async def scenario():
        directory = tmp_path / "skills" / "evidence"
        directory.mkdir(parents=True)
        source = directory / "SKILL.md"
        source.write_text("---\nname: evidence\ndescription: Use local evidence fixture\n---\n"
                          "SKILL_BODY_ORACLE\nRead supporting text only if needed: [support](support.ps1)", encoding="utf-8")
        support = directory / "support.ps1"
        support.write_text("throw 'SUPPORTING_SCRIPT_MUST_NOT_EXECUTE'", encoding="utf-8")
        provider = SkillsProvider(load)
        app = create_app(tmp_path, provider=provider)
        service = app.state.run_service
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                headers = await client.get("/api/v1/extensions/skills")
                assert headers.status_code == 200 and headers.json()["total"] == 1
                assert headers.json()["output"]["instructions_returned"] is False
                assert "SKILL_BODY_ORACLE" not in headers.text and "support.ps1" not in headers.text
                assert provider.seen == [] and service.scheduler.accepted_count == 0
                started = await client.post("/api/v1/runs", json={"prompt": "inspect skill metadata", "mode": "deep"})
                assert started.status_code == 202
                rid = started.json()["run_id"]
                await asyncio.wait_for(service.scheduler.wait(rid), 10)
                completed = await service.get(rid)
                assert completed["status"] == "completed" and "fallback_runtime" not in completed["metadata"]
                events = await service.list_events(rid)
                loaded = [item for item in events if item["type"] == "deep.skill_loaded"]
                assert len(loaded) == (1 if load else 0)
                assert len(provider.seen) == (2 if load else 1)
                assert completed["answer"] == ("explicitly loaded skill" if load else "headers only; no skill load")
                assert not any(item["type"] in {"command.started", "mcp.tool_executed", "subagent.started"} for item in events)
                assert support.read_text() == "throw 'SUPPORTING_SCRIPT_MUST_NOT_EXECUTE'"
                # Catalog atomicity is UI metadata behavior, not an engine-wide snapshot.
                accepted = service.scheduler.accepted_count
                source.write_text("---\nname: evidence\ndescription: Changed fixture header\n---\nCHANGED_BODY_ORACLE", encoding="utf-8")
                old = await client.get("/api/v1/extensions/skills")
                assert old.json()["skills"][0]["description"] == "Use local evidence fixture"
                denied = await client.post("/api/v1/extensions/skills/reload", json={"confirmed": False, "action": "reload_skills"})
                assert denied.status_code == 422
                reloaded = await client.post("/api/v1/extensions/skills/reload", json={"confirmed": True, "action": "reload_skills"})
                assert reloaded.status_code == 200 and reloaded.json()["skills"][0]["description"] == "Changed fixture header"
                assert "CHANGED_BODY_ORACLE" not in reloaded.text
                source.write_text("NOT_FRONTMATTER_PRIVATE_FIXTURE", encoding="utf-8")
                failed = await client.post("/api/v1/extensions/skills/reload", json={"confirmed": True, "action": "reload_skills"})
                assert failed.status_code == 422 and "PRIVATE_FIXTURE" not in failed.text
                retained = await client.get("/api/v1/extensions/skills")
                assert retained.json()["skills"] == reloaded.json()["skills"]
                assert service.scheduler.accepted_count == accepted  # never extra provider run/retry
        assert not service._owner.held
    asyncio.run(scenario())
