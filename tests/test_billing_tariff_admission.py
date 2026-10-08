"""Original B2b5a snapshot-path definitions, ALL UNRUN; no real service IO."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

from doppel_agent.billing_tariff import freeze_tariff
from doppel_agent.runtime.service import RunService
from test_billing_tariff import PROFILE, tariff


class SnapshotClock:
    @staticmethod
    def now(zone):
        assert zone is UTC
        return datetime(2026, 10, 6, 12, tzinfo=UTC)


def test_original_public_admission_freezes_private_declaration_without_legacy_prices_or_reference(monkeypatch):
    monkeypatch.setattr("doppel_agent.runtime.service.datetime", SnapshotClock)
    profile = {**PROFILE, "billing_tariff": tariff(), "input_price": 999,
               "output_price": 888, "api_key": "PRIVATE_NOT_READ", "name": "PRIVATE_LABEL"}
    snapshot = RunService._freeze_public_profile(profile)
    assert set(snapshot) == {*PROFILE, "billing_price_receipt"}
    assert snapshot["billing_price_receipt"] == freeze_tariff(tariff(), PROFILE, freeze_date="2026-10-06")
    profile["billing_tariff"]["rates"]["input"] = "99"
    assert snapshot["billing_price_receipt"]["rates"]["input"] == "1.25"
    assert RunService._freeze_public_profile({**PROFILE, "input_price": 0, "output_price": 0})["billing_price_receipt"] is None
    future = {**PROFILE, "billing_tariff": tariff(effective_date="2026-10-07")}
    assert RunService._freeze_public_profile(future)["billing_price_receipt"] is None


def test_original_work_order_carries_saved_price_identity_even_after_current_profile_edit(monkeypatch):
    monkeypatch.setattr("doppel_agent.runtime.service.datetime", SnapshotClock)
    old = RunService._freeze_public_profile({**PROFILE, "billing_tariff": tariff()})
    edited = {**PROFILE, "model": "edited-model", "billing_tariff": tariff(currency="USD")}
    reads = []

    def public():
        reads.append(True)
        return {"active_profile_id": "fixture", "profiles": [deepcopy(edited)]}

    owner = SimpleNamespace(settings=SimpleNamespace(public=public), provider_override=None,
                            _freeze_public_profile=RunService._freeze_public_profile)
    carried = RunService._freeze_work_order_scope(owner, {"profile_id": "fixture"}, [], existing={"fixture": old})
    assert carried["profiles"]["fixture"] == old
    fresh = RunService._freeze_work_order_scope(owner, {"profile_id": "fixture"}, [])
    assert fresh["profiles"]["fixture"]["model"] == "edited-model"
    assert fresh["profiles"]["fixture"]["billing_price_receipt"]["currency"] == "USD"
    assert fresh["profiles"]["fixture"]["billing_price_receipt"]["binding_sha256"] != old["billing_price_receipt"]["binding_sha256"]
    assert len(reads) == 2  # Existing original public metadata read, not key/price lookup on replay.
    carried["profiles"]["fixture"]["billing_price_receipt"]["rates"]["input"] = "77"
    assert old["billing_price_receipt"]["rates"]["input"] == "1.25"


def test_original_override_and_historical_work_order_do_not_synthesize_tariffs(monkeypatch):
    monkeypatch.setattr("doppel_agent.runtime.service.datetime", SnapshotClock)

    def forbidden():
        raise AssertionError("override touched real settings")

    owner = SimpleNamespace(settings=SimpleNamespace(public=forbidden),
                            provider_override=SimpleNamespace(model="scripted"),
                            _freeze_public_profile=RunService._freeze_public_profile)
    override = RunService._freeze_work_order_scope(owner, {}, [])
    assert "billing_price_receipt" not in override["profiles"]["scripted"]
    historical = RunService._freeze_work_order_scope(owner, {"profile_id": "fixture"}, [], existing={"fixture": PROFILE})
    assert historical["profiles"]["fixture"] == PROFILE
    assert "billing_price_receipt" not in historical["profiles"]["fixture"]
