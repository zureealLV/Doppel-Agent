"""B2b3b definitions FIRST, ALL UNRUN; numeric framing, not a tariff/invoice."""

import json

import pytest

from doppel_agent.provider_observation import canonical_usage_details, empty_usage_details, response_details_projection


def project(extra):
    raw = json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5, **extra},
                      "private": "PRIVATE_REASONING_KEY_PATH"}).encode()
    return response_details_projection(raw)


def test_explicit_cache_partition_and_reasoning_aliases_observed_once_not_added_to_total():
    state, usage, details = project({"prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 6,
        "input_tokens_details": {"cached_tokens": 4, "private": "PRIVATE_KEY"},
        "prompt_tokens_details": {"cached_tokens": 4},
        "output_tokens_details": {"reasoning_tokens": 2}, "completion_tokens_details": {"reasoning_tokens": 2}})
    assert state == "bounded_body" and usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert details == {"version": 1, "cached_input_tokens": 4, "uncached_input_tokens": 6,
        "reasoning_output_tokens": 2, "cache_state": "known", "reasoning_state": "known"}
    assert "PRIVATE_" not in json.dumps(details)


def test_missing_cache_miss_or_reasoning_is_not_derived_zero_or_input_minus_hit():
    _, _, details = project({"prompt_cache_hit_tokens": 4})
    assert details == {**empty_usage_details(), "cached_input_tokens": 4, "cache_state": "partial"}
    assert details["uncached_input_tokens"] is None and details["reasoning_output_tokens"] is None
    assert project({})[2] == empty_usage_details()


def test_explicit_zero_is_known_including_zero_input_and_output_bases():
    raw = b'{"usage":{"input_tokens":0,"output_tokens":0,"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":0,"completion_tokens_details":{"reasoning_tokens":0}}}'
    _, usage, details = response_details_projection(raw)
    assert usage["total_tokens"] == 0
    assert details == {"version": 1, "cached_input_tokens": 0, "uncached_input_tokens": 0,
        "reasoning_output_tokens": 0, "cache_state": "known", "reasoning_state": "known"}


@pytest.mark.parametrize("extra", [
    {"prompt_cache_hit_tokens": True}, {"prompt_cache_hit_tokens": -1}, {"prompt_cache_hit_tokens": 11},
    {"prompt_cache_hit_tokens": "4"}, {"prompt_cache_hit_tokens": 4.0},
    {"prompt_cache_hit_tokens": 4, "input_tokens_details": {"cached_tokens": 3}},
    {"prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 5},
    {"prompt_cache_hit_tokens": 4, "prompt_tokens_details": None},
    {"prompt_cache_miss_tokens": 11}, {"input_tokens_details": "PRIVATE_INVALID_CONTAINER"},
])
def test_bad_cache_partition_never_becomes_eligible_split_tariff(extra):
    state, usage, details = project(extra)
    assert state == "bounded_body" and usage["total_tokens"] == 15  # Base pair remains independently known.
    assert details["cache_state"] == "invalid" and details["cached_input_tokens"] is None
    assert details["uncached_input_tokens"] is None and "PRIVATE_" not in json.dumps(details)


@pytest.mark.parametrize("extra", [{"completion_tokens_details": {"reasoning_tokens": True}},
    {"output_tokens_details": {"reasoning_tokens": 6}}, {"completion_tokens_details": "PRIVATE_CONTAINER"},
    {"output_tokens_details": {"reasoning_tokens": 1}, "completion_tokens_details": {"reasoning_tokens": 2}}])
def test_bad_reasoning_details_do_not_erase_base_pair_or_guess_additive_charge(extra):
    _, usage, details = project(extra)
    assert usage["total_tokens"] == 15 and details["reasoning_state"] == "invalid"
    assert details["reasoning_output_tokens"] is None and details["cache_state"] == "unknown"


@pytest.mark.parametrize("raw", [
    b'{"usage":{"total_tokens":10,"prompt_cache_hit_tokens":2}}',
    b'{"usage":{"input_tokens":10,"output_tokens":5,"prompt_cache_hit_tokens":2,"prompt_cache_hit_tokens":3}}',
    b'{"usage":{"input_tokens":10,"output_tokens":5,"prompt_cache_hit_tokens":2},"private":Infinity}',
])
def test_details_without_trusted_base_or_unambiguous_bounded_frame_are_all_unknown(raw):
    _, usage, details = response_details_projection(raw)
    assert usage is None and details == empty_usage_details()


def test_original_request_projection_reallowlists_numeric_details_not_arbitrary_extra_payload():
    value = {"version": 1, "cached_input_tokens": 4, "uncached_input_tokens": 6,
             "reasoning_output_tokens": 2, "cache_state": "known", "reasoning_state": "known",
             "private": "PRIVATE_DETAILS_KEY_PATH"}
    assert canonical_usage_details(value, (10, 5)) == {key: value[key] for key in empty_usage_details()}
    assert canonical_usage_details(value, None) == empty_usage_details()
    value["cached_input_tokens"] = True
    actual = canonical_usage_details(value, (10, 5))
    assert actual["cache_state"] == "invalid" and actual["cached_input_tokens"] is None
    assert actual["uncached_input_tokens"] is None and actual["reasoning_output_tokens"] == 2
    value["cache_state"] = ["PRIVATE_ENUM"]
    value["reasoning_state"] = {"private": "PRIVATE_ENUM"}
    actual = canonical_usage_details(value, (10, 5))
    assert actual["cache_state"] == actual["reasoning_state"] == "invalid"
    assert "PRIVATE_" not in json.dumps(actual)
