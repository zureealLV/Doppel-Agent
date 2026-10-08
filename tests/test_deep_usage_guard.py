"""S8 B2b1 original chat facade definitions FIRST, ALL UNRUN; offline only."""

import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.provider_usage import MAX_SAFE
from doppel_agent.reporting.run_report import build_report
from doppel_agent.runtime.deep import _DeepEventBridge
from doppel_agent.runtime.deep_model import DoppelChatModel


class ScriptedProvider:
    """Offline scripted response oracle, not a real model/billing receipt."""

    def __init__(self, *turns):
        self.turns = iter(turns)
        self.calls = []

    def next_turn(self, messages, tools):
        self.calls.append((messages, tools))
        return next(self.turns)


def metadata(result):
    return result.generations[0].message.response_metadata


def test_original_result_valid_zero_never_encodes_tool_arguments_for_estimate(monkeypatch):
    def no_estimate(_turn):
        raise AssertionError("numeric zero must not call the tool estimator")

    monkeypatch.setattr("doppel_agent.runtime.deep_model._guard_estimate", no_estimate)
    provider = ScriptedProvider()
    model = DoppelChatModel(provider=provider, token_budget=1)
    turn = ModelTurn("long response " * 100, (ToolCall("offline", "tool", {"x": 1}),),
                     {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    result = model._result(turn)
    data = metadata(result)
    assert data["doppel_spent_tokens"] == 0
    assert data["doppel_usage_meter"]["current_usage"]["total_tokens"] == 0
    assert data["doppel_usage_meter"]["estimated_guard_tokens"] == 0
    assert result.generations[0].message.content == turn.content
    assert len(result.generations[0].message.tool_calls) == 1


def test_aliases_are_not_charged_twice_at_original_tool_budget_boundary():
    model = DoppelChatModel(provider=ScriptedProvider(), token_budget=10)
    first = model._result(ModelTurn("answer", (ToolCall("offline", "read", {"path": "fixture"}),),
                         {"input_tokens": 7, "prompt_tokens": 7, "output_tokens": 3, "completion_tokens": 3}))
    assert metadata(first)["doppel_spent_tokens"] == 10
    assert first.generations[0].message.content == "answer"
    second = model._result(ModelTurn("new answer", (ToolCall("offline2", "read", {}),),
                          {"prompt_tokens": 1, "completion_tokens": 0}))
    assert metadata(second)["doppel_spent_tokens"] == 11
    assert second.generations[0].message.tool_calls == []
    assert second.generations[0].message.content.startswith("Subagent token budget exhausted")
    assert metadata(second)["doppel_usage_meter"]["account_cap_guaranteed"] is False


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": 900},
    {"input_tokens": -20, "output_tokens": True},
    {"input_tokens": 1, "prompt_tokens": 100, "output_tokens": 2},
    {"input_tokens": 1, "output_tokens": 2, "total_tokens": False}])
def test_invalid_missing_usage_is_post_response_estimate_not_actual(usage):
    model = DoppelChatModel(provider=ScriptedProvider(), token_budget=1)
    turn = ModelTurn("xxxxxxxx", (ToolCall("offline", "read", {"path": "abcdefgh"}),), usage)
    expected = 2 + max(1, len(json.dumps(turn.tool_calls[0].arguments, default=str)) // 4)
    result = model._result(turn)
    data = metadata(result)
    meter = data["doppel_usage_meter"]
    assert data["doppel_spent_tokens"] == expected
    assert meter["current_usage"] is None and meter["observed"]["input_tokens"] is None
    assert meter["estimated_guard_tokens"] == expected
    assert meter["current_guard_source"] == "content_tool_heuristic"
    assert data["doppel_spent_tokens_kind"] == "internal_post_response_guard_not_billing"
    assert result.generations[0].message.tool_calls == []
    # Existing raw callback usage is retained, not replaced by inferred counters.
    assert result.llm_output["token_usage"] == (usage or {})


def test_non_dictionary_usage_has_no_get_crash_or_invented_usage_mapping():
    model = DoppelChatModel(provider=ScriptedProvider())
    result = model._result(ModelTurn("xxxxxxxx", usage=["not counters"]))
    assert metadata(result)["doppel_usage_meter"]["current_usage"] is None
    assert metadata(result)["doppel_spent_tokens"] == 2
    assert result.llm_output["token_usage"] == {}


def test_private_estimator_and_unknown_metadata_do_not_change_raw_valid_receipt():
    model = DoppelChatModel(provider=ScriptedProvider())
    original = {"prompt_tokens": 4, "completion_tokens": 2,
                "prompt_cache_hit_tokens": 3, "reasoning_tokens": 1}
    model._result(ModelTurn("unknown turn", usage=None))
    result = model._result(ModelTurn("known turn", usage=original))
    assert result.llm_output["token_usage"] == original
    assert original == {"prompt_tokens": 4, "completion_tokens": 2,
                        "prompt_cache_hit_tokens": 3, "reasoning_tokens": 1}
    observed = metadata(result)["doppel_usage_meter"]["observed"]
    assert observed["state"] == "partial" and observed["input_tokens"] == 4
    assert observed["unknown_usage_turns"] == 1 and observed["known_usage_turns"] == 1


def test_unsafe_cumulative_guard_is_not_rounded_or_exported_as_safe_billing():
    model = DoppelChatModel(provider=ScriptedProvider(), token_budget=MAX_SAFE)
    model._result(ModelTurn("first", usage={"input_tokens": MAX_SAFE, "output_tokens": 0}))
    result = model._result(ModelTurn("second", (ToolCall("offline", "read", {}),),
                                   {"input_tokens": 1, "output_tokens": 0}))
    data = metadata(result)
    assert data["doppel_spent_tokens"] is None
    assert data["doppel_usage_meter"]["guard_overflow"] is True
    assert data["doppel_usage_meter"]["observed"]["input_tokens"] is None
    assert result.generations[0].message.tool_calls == []


@pytest.mark.parametrize("budget", [True, False, 0, -1, 1.0, "1", MAX_SAFE + 1])
def test_original_chat_facade_rejects_invalid_budget_without_calling_provider(budget):
    provider = ScriptedProvider()
    with pytest.raises(ValidationError):
        DoppelChatModel(provider=provider, token_budget=budget)
    assert provider.calls == []


def test_original_sync_and_async_facade_calls_use_independent_meters():
    left_provider = ScriptedProvider(ModelTurn("sync", usage={"input_tokens": 4, "output_tokens": 2}))
    right_provider = ScriptedProvider(ModelTurn("async", usage={"input_tokens": 0, "output_tokens": 0}))
    left = DoppelChatModel(provider=left_provider)
    right = DoppelChatModel(provider=right_provider)
    sync = left._generate([HumanMessage(content="fixture")])
    async_result = asyncio.run(right._agenerate([HumanMessage(content="fixture")]))
    assert len(left_provider.calls) == len(right_provider.calls) == 1
    assert metadata(sync)["doppel_spent_tokens"] == 6
    assert metadata(async_result)["doppel_spent_tokens"] == 0
    assert metadata(async_result)["doppel_usage_meter"]["observed"]["known_usage_turns"] == 1


def test_original_deep_callback_emits_raw_usage_never_estimator_subtotal():
    class Sink:
        def __init__(self):
            self.events = []

        async def emit(self, kind, **payload):
            self.events.append((kind, payload))

    model = DoppelChatModel(provider=ScriptedProvider())
    model._result(ModelTurn("xxxxxxxx", usage=None))
    known = model._result(ModelTurn("known", usage={"input_tokens": 3, "output_tokens": 1}))
    sink = Sink()
    asyncio.run(_DeepEventBridge(sink).on_llm_end(known))
    assert sink.events == [("deep.model_finished", {"usage": {"input_tokens": 3, "output_tokens": 1}})]
    assert metadata(known)["doppel_spent_tokens"] == 6
    assert metadata(known)["doppel_usage_meter"]["estimated_guard_tokens"] == 2
    report = build_report("a" * 32, {"status": "completed", "mode": "deep", "lease_active": False},
        [{"seq": 1, "type": sink.events[0][0], "payload": sink.events[0][1]}],
        total=1, high_water=1, omitted_usage_rows=0, truncated=False)
    assert report["usage"]["observed"]["input_tokens"] == 3
    assert report["usage"]["observed"]["output_tokens"] == 1
    assert report["cost"]["amount"] is None and report["usage"]["billing_complete"] is False
