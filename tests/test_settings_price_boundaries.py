"""S8 B2b1 profile guard definitions FIRST, UNRUN; no price/billing receipt."""

import pytest

from doppel_agent.reporting.run_report import build_report
from doppel_agent.settings import SettingsStore


def config(**values):
    return {"provider": "mock", "preset": "mock", "name": "offline fixture",
            "model": "mock", "base_url": "", "input_price": 1, "output_price": 2, **values}


@pytest.mark.parametrize("field", ["input_price", "output_price"])
@pytest.mark.parametrize("value", [True, False, -1, float("nan"), float("inf"), float("-inf"),
    "NaN", "Infinity", "-Infinity", "1e309", 10**400, "not a number", [], {}])
def test_profile_refuses_invalid_prices_before_original_state_write(tmp_path, monkeypatch, field, value):
    store = SettingsStore(tmp_path / "settings.json")
    store.save_profile(config())
    original = store.path.read_bytes()

    def no_write(_data):
        raise AssertionError("invalid prices must not write even the original settings")

    monkeypatch.setattr(store, "_write", no_write)
    with pytest.raises(ValueError):
        store.save_profile(config(**{field: value}))
    assert store.path.read_bytes() == original


def test_missing_price_defaults_and_legacy_numeric_strings_are_not_price_receipts(tmp_path, monkeypatch):
    def no_key(*_args, **_kwargs):
        raise AssertionError("offline price validation must not access DPAPI or keys")

    monkeypatch.setattr("doppel_agent.settings._protect", no_key)
    monkeypatch.setattr("doppel_agent.settings._unprotect", no_key)
    store = SettingsStore(tmp_path / "settings.json")
    default = config()
    default.pop("input_price")
    default.pop("output_price")
    first = store.save_profile(default)
    assert first["input_price"] == first["output_price"] == 0
    result = store.save_profile(config(input_price="1.25", output_price=2.5))
    assert result["input_price"] == 1.25 and result["output_price"] == 2.5
    assert "currency" not in result and "price_snapshot" not in result


def test_mutable_or_zero_profile_prices_cannot_price_a_historical_report():
    # Numeric settings attached to source must not become frozen historical rates.
    rows = [{"seq": 1, "type": "graph.model_finished", "payload": {
        "usage": {"input_tokens": 0, "output_tokens": 0}}}]
    for price in (0, 1, 123):
        source = {"status": "completed", "mode": "graph", "lease_active": False,
                  "profile_snapshot": {"input_price": price, "output_price": price,
                      "currency": "CNY", "date": "2026-10-05", "arbitrary": "PRIVATE_PRICE_TEXT"}}
        report = build_report("a" * 32, source, rows, total=1, high_water=1,
                              omitted_usage_rows=0, truncated=False)
        assert report["usage"]["observed"]["input_tokens"] == 0
        assert report["cost"] == {"state": "unknown", "amount": None, "currency": None,
            "price_snapshot": None, "reason": "no_frozen_billing_price_receipt",
            "account_cap_guaranteed": False}
        assert report["usage"]["billing_complete"] is False
