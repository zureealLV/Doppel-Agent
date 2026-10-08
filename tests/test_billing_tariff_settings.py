"""B2b5a FIRST definitions, ALL UNRUN; disposable original settings, no DPAPI IO."""

import json

import pytest

from doppel_agent.settings import SettingsStore
from doppel_agent.tasks.work_orders import execution_settings
from doppel_agent.billing_tariff import freeze_tariff
from test_billing_tariff import PROFILE, tariff


def config(**changes):
    return {"provider": "mock", "preset": "mock", "name": "fixture", "model": "fixture-model", "base_url": "",
            "input_price": 0, "output_price": 0, **changes}


def test_explicit_tariff_confirmation_and_fixed_errors_before_original_read_write_or_key_access(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid tariff entered original settings IO or DPAPI")
    monkeypatch.setattr(store, "_normalized", forbidden)
    monkeypatch.setattr("doppel_agent.settings._protect", forbidden)
    for confirmation in (None, False, 1, "yes", [], {}):
        with pytest.raises(ValueError, match="billing_tariff_confirmation_required"):
            store.save_profile(config(billing_tariff=tariff(), billing_tariff_confirmed=confirmation))
    invalid = tariff(currency="PRIVATE_INVALID")
    with pytest.raises(ValueError, match="billing_tariff_invalid"):
        store.save_profile(config(billing_tariff=invalid, billing_tariff_confirmed=True))
    assert not store.path.exists()


def test_original_save_preserves_explicit_tariff_for_same_identity_but_clears_on_model_endpoint_provider_change(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("tariff metadata read touched DPAPI")
    monkeypatch.setattr("doppel_agent.settings._protect", forbidden)
    monkeypatch.setattr("doppel_agent.settings._unprotect", forbidden)
    store = SettingsStore(tmp_path / "settings.json")
    first = store.save_profile(config(billing_tariff=tariff(), billing_tariff_confirmed=True))
    assert first["billing_tariff"]["rates"]["input"] == "1.25"
    saved = store.save_profile(config(name="renamed", input_price=99, output_price=88))
    assert saved["billing_tariff"] == first["billing_tariff"]  # legacy floats do NOT edit frozen rates
    old = freeze_tariff(saved["billing_tariff"], saved, freeze_date="2026-10-06")
    changed = store.save_profile(config(model="new-model"))
    assert changed["billing_tariff"] is None and old["rates"]["input"] == "1.25"
    store.save_profile(config(billing_tariff=tariff(), billing_tariff_confirmed=True))
    assert store.save_profile(config(base_url="http://127.0.0.1/fixture"))["billing_tariff"] is None
    store.save_profile(config(billing_tariff=tariff(), billing_tariff_confirmed=True))
    assert store.save_profile(config(provider="openai"))["billing_tariff"] is None
    assert store.save_profile(config(billing_tariff=None))["billing_tariff"] is None


def test_legacy_zero_or_corrupt_saved_tariff_is_unknown_not_auto_upgraded(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.save_profile(config())["billing_tariff"] is None
    saved = json.loads(store.path.read_text(encoding="utf-8"))
    saved["profiles"][0]["billing_tariff"] = {"currency": "CNY", "rates": {"input": "0"}}
    store.path.write_text(json.dumps(saved), encoding="utf-8")
    assert store.public()["billing_tariff"] is None


def test_work_order_original_frozen_profiles_retain_receipt_without_repricing_and_reject_binding_forgery():
    receipt = freeze_tariff(tariff(), PROFILE, freeze_date="2026-10-06")
    profile = {**PROFILE, "billing_price_receipt": receipt}
    result = execution_settings({"profile_id": "fixture", "profiles": {"fixture": profile}})
    assert result["profiles"]["fixture"]["billing_price_receipt"] == receipt
    result["profiles"]["fixture"]["billing_price_receipt"]["rates"]["input"] = "99"
    assert receipt["rates"]["input"] == "1.25"
    with pytest.raises(ValueError, match="billing_receipt_invalid"):
        execution_settings({"profiles": {"fixture": {**profile, "model": "forged-model"}}})
    old = execution_settings({"profiles": {"fixture": PROFILE}})
    assert "billing_price_receipt" not in old["profiles"]["fixture"]


def test_frozen_priced_work_order_preserves_exact_model_identity_instead_of_trimming_and_rebinding():
    profile = {**PROFILE, "model": " fixture-model "}
    receipt = freeze_tariff(tariff(), profile, freeze_date="2026-10-06")
    result = execution_settings({"profiles": {"fixture": {**profile, "billing_price_receipt": receipt}}})
    assert result["profiles"]["fixture"]["model"] == profile["model"]
    assert result["profiles"]["fixture"]["billing_price_receipt"] == receipt
    assert execution_settings(result) == result  # Replay never silently changes frozen binding.
