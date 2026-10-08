"""Pure declared tariff contracts, frozen identity and EXACT observation estimates.

No settings, keys, price lookup, IO, FX, provider/admission/recovery authority.
Source kind/reference is a USER DECLARATION, never official-source verification.
Observed-unit estimates are NOT invoices, complete billing or account caps.
"""

import hashlib
import json
import re
from datetime import date

from .provider_observation import canonical_usage_details
from .provider_usage import parse_usage


UNIT_TOKENS = 1_000_000
RATE_SCALE = 100_000_000
CURRENCIES = frozenset({"CNY", "USD", "EUR", "GBP", "JPY"})
SOURCE_KINDS = frozenset({"user_declared", "offline_fixture", "official_reference"})
BASES = frozenset({"input_output_inclusive", "cache_partition_output_inclusive"})
CONFIG_KEYS = frozenset({"version", "currency", "effective_date", "source_kind", "source_reference",
                         "unit_tokens", "billing_basis", "reasoning_basis", "rates"})
RECEIPT_KEYS = frozenset({"version", "currency", "effective_date", "frozen_date", "source_kind", "source_sha256",
                          "binding_sha256", "unit_tokens", "billing_basis", "reasoning_basis", "rates", "tariff_sha256",
                          "source_verified", "billing_complete", "account_cap_guaranteed"})
RATE_KEYS = frozenset({"input", "cached_input", "uncached_input", "output"})


def _enum(value, values):
    return type(value) is str and value in values


def _date(value):
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("billing_tariff_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("billing_tariff_invalid") from None
    if parsed.isoformat() != value:
        raise ValueError("billing_tariff_invalid")
    return value


def _rate(value):
    # Decimal strings ONLY. Bound coefficient width/scale, no exponent/sign/
    # binary floats, even integer floats/bools. Explicit confirmed zero allowed.
    if type(value) is not str or re.fullmatch(r"(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,8})?", value) is None:
        raise ValueError("billing_tariff_invalid")
    return value.rstrip("0").rstrip(".") if "." in value else value


def _rates(value, basis):
    if type(value) is not dict or set(value) != RATE_KEYS:
        raise ValueError("billing_tariff_invalid")
    required = {"output", "input"} if basis == "input_output_inclusive" else {"output", "cached_input", "uncached_input"}
    result = {}
    for key in sorted(RATE_KEYS):
        if key in required:
            result[key] = _rate(value[key])
        else:
            if value[key] is not None:
                raise ValueError("billing_tariff_invalid")
            result[key] = None
    return result


def _common(value):
    if (type(value.get("version")) is not int or value["version"] != 1
            or not _enum(value.get("currency"), CURRENCIES) or not _enum(value.get("source_kind"), SOURCE_KINDS)
            or type(value.get("unit_tokens")) is not int or value["unit_tokens"] != UNIT_TOKENS
            or not _enum(value.get("billing_basis"), BASES) or value.get("reasoning_basis") != "included_in_output"):
        raise ValueError("billing_tariff_invalid")
    return {"version": 1, "currency": value["currency"], "effective_date": _date(value.get("effective_date")),
            "source_kind": value["source_kind"], "unit_tokens": UNIT_TOKENS,
            "billing_basis": value["billing_basis"], "reasoning_basis": "included_in_output",
            "rates": _rates(value.get("rates"), value["billing_basis"])}


def canonical_tariff(value):
    """Private editable settings config; never export its free source reference.

    This validates a declaration, not whether it applies to a real invoice. Missing
    legacy float prices/currency/provenance can't manufacture this contract.
    """
    if type(value) is not dict or set(value) != CONFIG_KEYS:
        raise ValueError("billing_tariff_invalid")
    result = _common(value)
    reference = value["source_reference"]
    if type(reference) is not str or not reference.strip() or any(ord(char) < 32 for char in reference):
        raise ValueError("billing_tariff_invalid")
    try:
        encoded = reference.encode("utf-8")
    except UnicodeError:
        raise ValueError("billing_tariff_invalid") from None
    if len(encoded) > 2048:
        raise ValueError("billing_tariff_invalid")
    result["source_reference"] = reference.strip()
    return result


def _digest(domain, value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("billing_tariff_invalid") from None
    return hashlib.sha256(b"doppel-billing-v1\0" + domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _binding(profile):
    if type(profile) is not dict or not _enum(profile.get("provider"), {"openai", "mock", "explicit_override"}):
        raise ValueError("billing_tariff_invalid")
    model, endpoint = profile.get("model"), profile.get("base_url", "")
    if (type(model) is not str or not model or len(model) > 400 or type(endpoint) is not str
            or len(endpoint) > 4096 or "\x00" in model or "\x00" in endpoint):
        raise ValueError("billing_tariff_invalid")
    # Identity frozen, credentials/name/id/legacy float rates deliberately absent.
    return _digest("model_binding", {"provider": profile["provider"], "model": model, "base_url": endpoint})


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def freeze_tariff(value, profile, *, freeze_date):
    """Explicit owner snapshot date, not ambient clock/latest settings lookup.

    Future-effective declarations remain unavailable at this snapshot. A digest
    correlates private provenance/identity, not anonymous or tamper-proof authority.
    No raw reference/provider/model/URL/key appears in the numeric receipt.
    """
    frozen_date = _date(freeze_date)
    if value is None:
        return None
    canonical = canonical_tariff(value)
    if canonical["effective_date"] > frozen_date:
        return None
    reference = canonical.pop("source_reference")
    result = {**canonical, "frozen_date": frozen_date, "source_sha256": _digest("source_reference", reference),
              "binding_sha256": _binding(profile), "source_verified": False,
              "billing_complete": False, "account_cap_guaranteed": False}
    return {**result, "tariff_sha256": _digest("frozen_tariff", result)}


def validate_price_receipt(value, *, profile=None):
    """Exact detached historical frame; optional original binding, no repricing."""
    try:
        if type(value) is not dict or set(value) != RECEIPT_KEYS:
            raise ValueError
        result = _common(value)
        result["frozen_date"] = _date(value["frozen_date"])
        if result["effective_date"] > result["frozen_date"]:
            raise ValueError
        if (any(value[key] is not False for key in ("source_verified", "billing_complete", "account_cap_guaranteed"))
                or any(not _hash(value[key]) for key in ("source_sha256", "binding_sha256", "tariff_sha256"))):
            raise ValueError
        # Receipt must already be canonical. Do not silently change/hash legacy
        # rate spelling; explicit version/shape, then original fingerprint only.
        if result["rates"] != value["rates"]:
            raise ValueError
        result.update(source_sha256=value["source_sha256"], binding_sha256=value["binding_sha256"],
                      source_verified=False, billing_complete=False, account_cap_guaranteed=False)
        if _digest("frozen_tariff", result) != value["tariff_sha256"] or profile is not None and _binding(profile) != result["binding_sha256"]:
            raise ValueError
        result["tariff_sha256"] = value["tariff_sha256"]
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("billing_receipt_invalid") from None


def _coefficient(value):
    whole, _, fraction = value.partition(".")
    return int(whole) * RATE_SCALE + int(fraction.ljust(8, "0"))


def _amount(numerator):
    # Exact denominator1e14: 8 decimal places in rate, per1e6 tokens. Integer-only
    # arithmetic, no Decimal ambient precision, JS rounding or currency FX.
    denominator = RATE_SCALE * UNIT_TOKENS
    whole, remainder = divmod(numerator, denominator)
    return str(whole) if not remainder else f"{whole}.{remainder:014d}".rstrip("0")


def estimate_observation(receipt, usage, details=None):
    """Declared estimate for ONE independent observation, never grouping policy.

    Original report linking must decide which unit is eligible and supply its
    original frozen receipt. This primitive cannot price callback duplicates,
    prove physical requests, authenticate provider billing or grant retries.
    Inclusive basis explicitly includes cache in input and reasoning in output.
    Partition basis requires BOTH actual supplied cache counters; never derive
    input-minus-hit or add reasoning as another output charge.
    """
    result = {"version": 1, "state": "unknown", "amount": None, "currency": None, "tariff_sha256": None,
              "reason": "no_frozen_tariff", "basis": "declared_tariff_observed_usage_not_invoice",
              "billing_complete": False, "account_cap_guaranteed": False}
    if receipt is None:
        return result
    frozen = validate_price_receipt(receipt)
    result.update(currency=frozen["currency"], tariff_sha256=frozen["tariff_sha256"], reason="usage_unknown")
    pair = parse_usage(usage) if type(usage) is dict else None
    if pair is None:
        return result
    rates = frozen["rates"]
    output = pair[1] * _coefficient(rates["output"])
    if frozen["billing_basis"] == "input_output_inclusive":
        numerator = pair[0] * _coefficient(rates["input"]) + output
    else:
        observed = canonical_usage_details(details, pair)
        if observed["cache_state"] != "known":
            result["reason"] = "cache_partition_unknown"
            return result
        numerator = (observed["cached_input_tokens"] * _coefficient(rates["cached_input"])
                     + observed["uncached_input_tokens"] * _coefficient(rates["uncached_input"]) + output)
    result.update(state="known_for_declared_observation", amount=_amount(numerator), reason="declared_observation")
    return result
