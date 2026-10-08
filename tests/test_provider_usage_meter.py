"""S8 B2b1 definitions FIRST, UNRUN until S9; not provider billing proof."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from doppel_agent.provider_usage import MAX_SAFE, TokenUsageMeter, parse_usage


@pytest.mark.parametrize("usage", [
    None, {}, [], True, {"total_tokens": 10}, {"input_tokens": 1},
    {"input_tokens": True, "output_tokens": 1},
    {"input_tokens": 1, "output_tokens": False},
    {"input_tokens": -1, "output_tokens": 2},
    {"input_tokens": 1.0, "output_tokens": 2},
    {"input_tokens": "1", "output_tokens": 2},
    {"input_tokens": MAX_SAFE + 1, "output_tokens": 0},
    {"input_tokens": MAX_SAFE, "output_tokens": 1},
    {"input_tokens": 1, "prompt_tokens": 2, "output_tokens": 3},
    {"input_tokens": 1, "output_tokens": 2, "completion_tokens": 3},
    {"input_tokens": 1, "output_tokens": 2, "total_tokens": 4},
    {"input_tokens": 1, "output_tokens": 2, "total_tokens": True},
    {"input_tokens": 1, "output_tokens": 2, "total_tokens": float("nan")},
])
def test_strict_actual_counter_contract_rejects_unproved_usage(usage):
    assert parse_usage(usage) is None


def test_duplicate_aliases_charge_once_and_preserve_explicit_zero():
    meter = TokenUsageMeter()

    def no_estimate():
        raise AssertionError("valid counters must not consult an estimator")

    first = meter.record({"input_tokens": 7, "prompt_tokens": 7,
                          "output_tokens": 3, "completion_tokens": 3,
                          "total_tokens": 10}, no_estimate)
    assert first["guard_tokens"] == 10
    assert first["current_guard_source"] == "provider_counters"
    assert first["current_usage"] == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    zero = meter.record({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, no_estimate)
    assert zero["guard_tokens"] == 10
    assert zero["estimated_guard_tokens"] == 0
    assert zero["current_usage"]["total_tokens"] == 0
    assert zero["observed"]["known_usage_turns"] == 2
    assert zero["observed"]["input_tokens"] == 7
    assert zero["observed"]["output_tokens"] == 3
    assert zero["observed"]["state"] == "known_for_observed_responses"
    assert meter.exhausted(10) is False  # Existing post-response > boundary, not >=.
    assert meter.exhausted(9) is True


@pytest.mark.parametrize("usage", [None, {}, {"total_tokens": 200},
    {"input_tokens": True, "output_tokens": -4},
    {"input_tokens": 1, "prompt_tokens": 100, "output_tokens": 2}])
def test_unknown_usage_estimate_is_guard_only_never_actual(usage):
    calls = []
    meter = TokenUsageMeter()
    result = meter.record(usage, lambda: calls.append("estimate") or 11)
    assert calls == ["estimate"]
    assert result["current_usage"] is None
    assert result["current_guard_source"] == "content_tool_heuristic"
    assert result["guard_tokens"] == result["estimated_guard_tokens"] == 11
    assert result["observed"] == {"state": "unknown", "known_usage_turns": 0,
        "unknown_usage_turns": 1, "input_tokens": None, "output_tokens": None,
        "subtotal_overflow": False}
    assert result["guard_kind"] == "internal_post_response_guard_not_billing"
    assert result["billing_complete"] is False
    assert result["account_cap_guaranteed"] is False
    assert meter.exhausted(None) is False and meter.exhausted(10) is True


def test_known_subtotal_does_not_absorb_unknown_guard_estimates():
    meter = TokenUsageMeter()
    first = meter.record({"input_tokens": 4, "output_tokens": 2}, lambda: 99)
    second = meter.record(None, lambda: 10)
    assert first["observed"]["state"] == "known_for_observed_responses"
    assert second["observed"] == {"state": "partial", "known_usage_turns": 1,
        "unknown_usage_turns": 1, "input_tokens": 4, "output_tokens": 2,
        "subtotal_overflow": False}
    assert second["guard_tokens"] == 16 and second["estimated_guard_tokens"] == 10
    # Returned metadata isn't the mutable meter; a late caller cannot retarget it.
    second["observed"]["input_tokens"] = 999
    third = meter.record({"input_tokens": 0, "output_tokens": 0}, lambda: 99)
    assert third["observed"]["input_tokens"] == 4
    assert first["observed"]["known_usage_turns"] == 1


def test_overflow_is_unknown_in_metadata_but_exact_guard_stays_exhausted():
    meter = TokenUsageMeter()
    meter.record({"input_tokens": MAX_SAFE, "output_tokens": 0}, lambda: 1)
    result = meter.record({"input_tokens": 1, "output_tokens": 0}, lambda: 1)
    assert result["current_usage"]["input_tokens"] == 1
    assert result["guard_tokens"] is None and result["guard_overflow"] is True
    assert result["observed"]["state"] == "partial"
    assert result["observed"]["input_tokens"] is None
    assert result["observed"]["output_tokens"] is None
    assert result["observed"]["subtotal_overflow"] is True
    assert meter.exhausted(MAX_SAFE) is True
    assert result["estimated_guard_tokens"] == 0


def test_estimator_overflow_is_not_clamped_into_fake_actual_usage():
    meter = TokenUsageMeter()
    meter.record(None, lambda: MAX_SAFE)
    result = meter.record(None, lambda: 1)
    assert result["estimated_guard_overflow"] is True
    assert result["estimated_guard_tokens"] is None
    assert result["guard_overflow"] is True and result["guard_tokens"] is None
    assert result["observed"]["subtotal_overflow"] is False
    assert result["observed"]["input_tokens"] is None
    assert meter.exhausted(MAX_SAFE) is True


@pytest.mark.parametrize("estimate", [True, False, -1, 0, 1.0, "2", MAX_SAFE + 1])
def test_invalid_estimate_refuses_without_mutating_original_meter(estimate):
    meter = TokenUsageMeter()
    with pytest.raises(ValueError, match="usage_guard_estimate_invalid"):
        meter.record(None, lambda: estimate)
    after = meter.record({"input_tokens": 0, "output_tokens": 0}, lambda: 1)
    assert after["guard_tokens"] == 0 and after["observed"]["unknown_usage_turns"] == 0


def test_estimator_failure_does_not_invent_a_sealed_charge():
    meter = TokenUsageMeter()

    def unavailable():
        raise RuntimeError("fixture estimator unavailable")

    with pytest.raises(RuntimeError):
        meter.record(None, unavailable)
    after = meter.record({"input_tokens": 0, "output_tokens": 0}, lambda: 1)
    assert after["observed"]["unknown_usage_turns"] == 0


@pytest.mark.parametrize("budget", [True, False, 0, -1, 1.0, "1", MAX_SAFE + 1])
def test_budget_guard_rejects_invalid_limit_not_a_currency_conversion(budget):
    with pytest.raises(ValueError, match="usage_guard_budget_invalid"):
        TokenUsageMeter().exhausted(budget)


def test_instances_never_share_usage_or_fallback_billing_state():
    left, right = TokenUsageMeter(), TokenUsageMeter()
    left.record({"input_tokens": 3, "output_tokens": 2}, lambda: 1)
    result = right.record({"input_tokens": 0, "output_tokens": 0}, lambda: 1)
    assert result["guard_tokens"] == 0 and result["observed"]["input_tokens"] == 0
    assert result["billing_complete"] is False


def test_shared_facade_worker_receipts_each_project_one_atomic_meter_prefix():
    # Future disposable worker fixture, not run during construction.
    meter = TokenUsageMeter()

    def finish(_index):
        return meter.record({"input_tokens": 1, "output_tokens": 0}, lambda: 99)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(finish, range(64)))
    assert sorted(item["guard_tokens"] for item in results) == list(range(1, 65))
    assert all(item["guard_tokens"] == item["observed"]["input_tokens"]
               == item["observed"]["known_usage_turns"] for item in results)
    assert all(item["estimated_guard_tokens"] == 0 for item in results)


def test_facade_deepcopy_preserves_numeric_history_without_sharing_meter_lock_state():
    original = TokenUsageMeter()
    original.record({"input_tokens": 3, "output_tokens": 2}, lambda: 1)
    copied = deepcopy(original)
    after = copied.record({"input_tokens": 1, "output_tokens": 0}, lambda: 1)
    assert after["guard_tokens"] == 6
    still_original = original.record({"input_tokens": 0, "output_tokens": 0}, lambda: 1)
    assert still_original["guard_tokens"] == 5 and still_original["observed"]["input_tokens"] == 3
