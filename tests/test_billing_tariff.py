"""B2b5a definitions FIRST, ALL UNRUN. Declared prices, NOT current official rates."""

from copy import deepcopy
from fractions import Fraction
import json

import pytest

from doppel_agent.billing_tariff import (
    canonical_tariff,
    freeze_tariff,
    validate_price_receipt,
    estimate_observation,
)
from doppel_agent.provider_observation import empty_usage_details
from doppel_agent.provider_usage import MAX_SAFE


PROFILE = {"id": "fixture", "provider": "mock", "model": "fixture-model", "base_url": ""}


def tariff(**changes):
    return {
        "version": 1,
        "currency": "CNY",
        "effective_date": "2026-10-01",
        "source_kind": "offline_fixture",
        "source_reference": "PRIVATE_DECLARED_PRICE_REFERENCE_NOT_OFFICIAL",
        "unit_tokens": 1_000_000,
        "billing_basis": "input_output_inclusive",
        "reasoning_basis": "included_in_output",
        "rates": {
            "input": "1.25000000",
            "cached_input": None,
            "uncached_input": None,
            "output": "2.50000000",
        },
        **changes,
    }


def frozen(config=None, profile=None):
    return freeze_tariff(
        tariff() if config is None else config,
        PROFILE if profile is None else profile,
        freeze_date="2026-10-06",
    )


def usage(inputs=7, outputs=3):
    return {"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs}


def test_canonical_config_is_decimal_only_and_frozen_receipt_redacts_reference_identity_and_credentials():
    config = tariff()
    profile = {**PROFILE, "api_key": "PRIVATE_KEY", "name": "PRIVATE_LABEL"}
    canonical = canonical_tariff(config)
    assert canonical["rates"]["input"] == "1.25" and canonical["rates"]["output"] == "2.5"
    receipt = frozen(config, profile)
    assert validate_price_receipt(receipt, profile=PROFILE) == receipt
    assert (
        receipt["source_verified"]
        is receipt["billing_complete"]
        is receipt["account_cap_guaranteed"]
        is False
    )
    assert (
        len(receipt["tariff_sha256"]) == len(receipt["binding_sha256"]) == len(receipt["source_sha256"]) == 64
    )
    assert "PRIVATE_" not in json.dumps(receipt) and "source_reference" not in receipt
    config["rates"]["input"] = "99"
    assert receipt["rates"]["input"] == "1.25"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.update(version=True),
        lambda v: v.update(currency="ZZZ"),
        lambda v: v.update(effective_date="2026-02-30"),
        lambda v: v.update(source_kind=[]),
        lambda v: v.update(source_reference=""),
        lambda v: v.update(source_reference="x" * 2049),
        lambda v: v.update(unit_tokens=True),
        lambda v: v.update(reasoning_basis="charged_again"),
        lambda v: v.update(billing_basis={}),
        lambda v: v.update(private="PRIVATE_KEY"),
        lambda v: v["rates"].update(input=0.1),
        lambda v: v["rates"].update(input=True),
        lambda v: v["rates"].update(input="1e-8"),
        lambda v: v["rates"].update(input="NaN"),
        lambda v: v["rates"].update(input="-1"),
        lambda v: v["rates"].update(input="0.000000001"),
        lambda v: v["rates"].update(input="1000000000"),
        lambda v: v["rates"].update(input=None),
        lambda v: v["rates"].update(cached_input="1"),
        lambda v: v["rates"].update(private="PRIVATE_RATE"),
    ],
)
def test_incomplete_malformed_ambiguous_rate_contract_refused_without_echo(mutate):
    value = tariff()
    mutate(value)
    with pytest.raises(ValueError, match="^billing_tariff_invalid$"):
        canonical_tariff(value)


def test_absent_or_future_tariff_is_unknown_not_legacy_price_zero_and_official_reference_not_verified():
    assert freeze_tariff(None, PROFILE, freeze_date="2026-10-06") is None
    assert freeze_tariff(tariff(effective_date="2026-10-07"), PROFILE, freeze_date="2026-10-06") is None
    receipt = frozen(tariff(source_kind="official_reference"))
    assert receipt["source_verified"] is False
    result = estimate_observation(None, usage(0, 0))
    assert result["state"] == "unknown" and result["amount"] is None and result["currency"] is None


def test_original_identity_binding_and_receipt_fingerprint_are_revalidated_not_current_profile_prices():
    receipt = frozen()
    with pytest.raises(ValueError, match="^billing_receipt_invalid$"):
        validate_price_receipt(receipt, profile={**PROFILE, "model": "another-model"})
    for mutate in (
        lambda v: v["rates"].update(input="99"),
        lambda v: v.update(source_verified=True),
        lambda v: v.update(account_cap_guaranteed=True),
        lambda v: v.update(source_reference="PRIVATE"),
        lambda v: v.update(frozen_date="2026-09-30"),
        lambda v: v.update(version=True),
    ):
        forged = deepcopy(receipt)
        mutate(forged)
        with pytest.raises(ValueError, match="^billing_receipt_invalid$"):
            validate_price_receipt(forged)
    copy = validate_price_receipt(receipt)
    copy["rates"]["input"] = "0"
    assert receipt["rates"]["input"] == "1.25"


def test_exact_observed_unit_estimate_matches_independent_fraction_oracle_no_binary_float_or_rounding():
    receipt = frozen()
    result = estimate_observation(receipt, usage())
    assert result["state"] == "known_for_declared_observation"
    assert Fraction(result["amount"]) == (7 * Fraction("1.25") + 3 * Fraction("2.5")) / 1_000_000
    assert result["amount"] == "0.00001625" and result["currency"] == "CNY"
    assert result["billing_complete"] is False and result["account_cap_guaranteed"] is False
    assert result["basis"] == "declared_tariff_observed_usage_not_invoice"
    assert estimate_observation(receipt, usage(0, 0))["amount"] == "0"
    tiny = tariff(rates={"input": "0.00000001", "cached_input": None, "uncached_input": None, "output": "0"})
    assert estimate_observation(frozen(tiny), usage(1, 0))["amount"] == "0.00000000000001"


def test_cache_partition_requires_both_actual_counters_no_input_minus_hit_and_no_reasoning_double_charge():
    receipt = frozen(
        tariff(
            billing_basis="cache_partition_output_inclusive",
            rates={"input": None, "cached_input": "0.5", "uncached_input": "2", "output": "3"},
        )
    )
    details = {
        **empty_usage_details(),
        "cache_state": "known",
        "cached_input_tokens": 2,
        "uncached_input_tokens": 5,
        "reasoning_state": "known",
        "reasoning_output_tokens": 2,
    }
    result = estimate_observation(receipt, usage(), details)
    assert (
        Fraction(result["amount"])
        == Fraction(2, 1_000_000) * Fraction("0.5") + Fraction(5, 1_000_000) * 2 + Fraction(3, 1_000_000) * 3
    )
    for change in (
        {"cache_state": "partial", "uncached_input_tokens": None},
        {"uncached_input_tokens": 6},
        {"cache_state": "invalid", "cached_input_tokens": None, "uncached_input_tokens": None},
    ):
        unknown = estimate_observation(receipt, usage(), {**details, **change})
        assert unknown["amount"] is None and unknown["reason"] == "cache_partition_unknown"


def test_unsafe_usage_and_missing_usage_never_price_known_zero_while_large_exact_amount_is_string():
    receipt = frozen()
    for pair in (None, {}, {"input_tokens": True, "output_tokens": 0}, usage(MAX_SAFE + 1, 0)):
        assert estimate_observation(receipt, pair)["amount"] is None
    result = estimate_observation(receipt, usage(MAX_SAFE, 0))
    assert (
        type(result["amount"]) is str
        and Fraction(result["amount"]) == MAX_SAFE * Fraction("1.25") / 1_000_000
    )
    usd = estimate_observation(frozen(tariff(currency="USD")), usage())
    assert usd["currency"] == "USD" and usd["amount"] == "0.00001625"  # NO FX or CNY budget inference
