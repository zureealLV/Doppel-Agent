"""Budget boundaries must request useful synthesis, not fabricate tool outages."""

import asyncio

import pytest

from doppel_agent.events import EventBus
from doppel_agent.graph.nodes import FocusedGraphNodes, initial_messages
from doppel_agent.loop import AgentLoop
from doppel_agent.provider import Message, ModelTurn, ToolCall
from doppel_agent.runtime.graph import focused_read_tools


class CapturingProvider:
    def __init__(self):
        self.requests = []

    def next_turn(self, messages, tools):
        self.requests.append((list(messages), tools))
        if tools:
            return ModelTurn(tool_calls=(ToolCall("read-1", "read_file", {"path": "hello.txt"}),))
        return ModelTurn(content="已检查 hello.txt；其他文件尚未检查。")


class CapturingSink:
    def __init__(self):
        self.events = []

    async def emit(self, kind, **payload):
        self.events.append((kind, payload))


@pytest.mark.parametrize("engine", ["legacy", "graph"])
def test_final_synthesis_is_system_guidance_and_keeps_original_user_goal(tmp_path, engine):
    (tmp_path / "hello.txt").write_text("review evidence", encoding="utf-8")
    provider = CapturingProvider()
    tools = focused_read_tools(tmp_path)
    prompt = "继续审查其他可能的漏洞"
    if engine == "legacy":
        events = []
        result = AgentLoop(provider, tools, EventBus("budget-test", events.append), max_steps=2).run(prompt)
        assert "已检查" in result
    else:
        nodes = FocusedGraphNodes(provider, tools)
        state = {"run_id": "budget-test", "messages": initial_messages(prompt, ()), "step_count": 1, "max_steps": 2}

        async def run():
            state.update(await nodes.reason(state))
            state.update(await nodes.execute_tools(state))
            return await nodes.reason(state)

        update = asyncio.run(run())
        assert update["status"] == "completed"
        # Runtime-only instructions must not become the user's next turn or
        # survive in a checkpoint that the next conversation turn reuses.
        assert [item["content"] for item in update["messages"] if item["role"] == "user"] == [prompt]
        assert not any("step budget" in item["content"] for item in update["messages"])
    messages, schemas = provider.requests[-1]
    assert len(provider.requests) == 2
    assert schemas == []
    assert [item.content for item in messages if item.role == "user"] == [prompt]
    assert any(item.role == "tool" and "review evidence" in item.content for item in messages)
    guidance = messages[-1]
    assert guidance.role == "system"
    assert "step budget" in guidance.content
    assert "substantive" in guidance.content
    assert "same language" in guidance.content
    assert "permission or connection failure" in guidance.content
    assert "Tool access is now closed" not in guidance.content


@pytest.mark.parametrize("engine", ["legacy", "graph"])
@pytest.mark.parametrize("max_steps", [1, 2])
def test_step_limit_emits_separate_notice_without_inventing_an_outage(tmp_path, engine, max_steps):
    (tmp_path / "hello.txt").write_text("evidence", encoding="utf-8")
    provider, sink = CapturingProvider(), CapturingSink()
    tools = focused_read_tools(tmp_path)
    if engine == "legacy":
        events = []
        AgentLoop(provider, tools, EventBus("budget-test", events.append), max_steps=max_steps).run("审查")
        notices = [item["payload"] for item in events if item["kind"] == "run.budget_exhausted"]
    else:
        nodes = FocusedGraphNodes(provider, tools, sink=sink)
        state = {"run_id": "budget-test", "messages": initial_messages("审查", ()),
                 "step_count": 1, "max_steps": max_steps}

        async def run():
            if max_steps > 1:
                state.update(await nodes.reason(state))
                state.update(await nodes.execute_tools(state))
            return await nodes.reason(state)

        asyncio.run(run())
        notices = [payload for kind, payload in sink.events if kind == "graph.budget_exhausted"]
    assert notices == [{"step": max_steps, "max_steps": max_steps, "reason": "step_limit"}]
    assert len(provider.requests) == max_steps


def test_legacy_rejects_tool_calls_after_final_synthesis_without_executing_them(tmp_path, monkeypatch):
    class IgnoresBudget:
        def next_turn(self, messages, tools):
            return ModelTurn(tool_calls=(ToolCall("late-read", "read_file", {"path": "hello.txt"}),))

    tools, executed = focused_read_tools(tmp_path), []
    monkeypatch.setattr(tools, "execute", lambda *args: executed.append(args) or "unexpected execution")
    with pytest.raises(RuntimeError, match="max_steps_exceeded"):
        AgentLoop(IgnoresBudget(), tools, EventBus("budget-test", lambda event: None), max_steps=1).run("审查")
    assert executed == []


def test_new_graph_run_does_not_inherit_prior_runs_budget_closure(tmp_path):
    old_guidance = (
        "Tool access is now closed. Do not emit tool calls. "
        "Using only the evidence already collected, write the final answer now."
    )
    provider = CapturingProvider()
    nodes = FocusedGraphNodes(provider, focused_read_tools(tmp_path))
    history = (Message("user", "审查"), Message("user", old_guidance), Message("assistant", "先前的报告"))
    state = {"run_id": "new-run", "messages": initial_messages("继续审查其他可能的漏洞", history),
             "step_count": 1, "max_steps": 8}
    update = asyncio.run(nodes.reason(state))
    messages, schemas = provider.requests[0]
    assert schemas
    assert messages[-1].content == "继续审查其他可能的漏洞"
    guidance = next(item for item in messages if item.role == "system" and "new execution" in item.content)
    assert "do not carry over" in guidance.content
    # Keep the original persisted evidence untouched rather than rewriting it.
    assert any(item["content"] == old_guidance for item in update["messages"])
    assert not any("new execution" in item["content"] for item in update["messages"])
