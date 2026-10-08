"""Bounded/private Core continuation definitions; executed only in S9."""

from dataclasses import asdict

import pytest

from doppel_agent.loop import LoopContinuation
from doppel_agent.persistence.legacy_review import MAX_BYTES, LegacyReviewStore, decode, encode
from doppel_agent.provider import Message, ToolCall


def frame():
    first, second = ToolCall("first", "tool", {}), ToolCall("second", "tool", {})
    return LoopContinuation(1, (Message("system", "private model context"),
                                Message("assistant", "", tool_calls=(first, second)),
                                Message("tool", "private prior result", tool_call_id="first")), (second,))


def payload():
    return {"schema": 1, "run_id": "run", "thread_id": "thread", "prompt": "fixture",
            "context_text": "", "continuation": asdict(frame())}


def test_pending_call_boundary_preserves_completed_prefix_and_actual_assistant_call_object():
    decoded, continuation = decode(encode(payload()))
    assert decoded["schema"] == 1 and continuation.pending_calls[0].id == "second"
    continuation.pending_calls[0].arguments["reviewed"] = "edit"
    assert continuation.messages[1].tool_calls[1].arguments == {"reviewed": "edit"}
    assert continuation.messages[2].tool_call_id == "first"


@pytest.mark.parametrize("corruption", ["prefix", "pending", "step", "schema", "fields"])
def test_invalid_pending_boundary_never_becomes_a_continuation(corruption):
    item = payload()
    if corruption == "prefix":
        item["continuation"]["messages"][2]["tool_call_id"] = "second"
    elif corruption == "pending":
        item["continuation"]["pending_calls"][0]["id"] = "foreign"
    elif corruption == "step":
        item["continuation"]["step"] = True
    elif corruption == "schema":
        item["schema"] = True
    else:
        item["foreign_field"] = "not accepted"
    with pytest.raises(ValueError, match="legacy_review_invalid"):
        decode(encode(item))


def test_snapshot_byte_limit_is_not_silent_truncation():
    item = payload()
    item["context_text"] = "x" * MAX_BYTES
    with pytest.raises(ValueError, match="snapshot_budget"):
        encode(item)


def test_scope_and_exact_snapshot_compare_and_consume_allow_only_one_decision(tmp_path):
    store = LegacyReviewStore(tmp_path / "tool-executions.sqlite3")
    interrupt = store.save("run", "thread", "fixture", "", frame())
    raw, _, continuation = store.load("run", "thread", interrupt)
    assert continuation.pending_calls[0].id == "second"
    with pytest.raises(ValueError, match="legacy_review_scope"):
        store.load("run", "foreign", interrupt)
    with pytest.raises(ValueError, match="legacy_review_scope"):
        store.consume("run", "thread", interrupt, raw + " ", {"action": "approve"})
    with pytest.raises(ValueError, match="legacy_review_scope"):
        store.save("run", "thread", "fixture", "", frame())
    store.consume("run", "thread", interrupt, raw, {"action": "approve"})
    with pytest.raises(ValueError, match="legacy_review_scope"):
        store.consume("run", "thread", interrupt, raw, {"action": "approve"})
    with pytest.raises(ValueError, match="legacy_review_run_already"):
        store.ensure_new("run")
    following = store.save("run", "thread", "fixture", "", frame())
    assert following != interrupt
    with pytest.raises(ValueError, match="legacy_review_scope"):
        store.load("run", "thread", interrupt)
