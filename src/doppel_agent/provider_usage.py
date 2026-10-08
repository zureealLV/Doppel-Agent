"""Pure actual-counter validation and separate internal post-response guard.

Neither observed provider counters nor a content/tool heuristic prove complete
billing, network-attempt coverage, currency/price provenance or an account cap.
No provider IO, state store, report reader, global meter or price lookup here.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any


MAX_SAFE = 9007199254740991
GUARD_KIND = "internal_post_response_guard_not_billing"


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE


def parse_usage(value: Any) -> tuple[int, int] | None:
    """Both counter groups required; duplicated aliases agree, zero is real zero.

    Extra original usage details are neither charged twice nor erased. This
    validates the input/output pair only; cache/reasoning billing needs its own
    frozen provider-specific receipt contract, never inferred from this pair.
    """
    if not isinstance(value, dict):
        return None
    counters = []
    for aliases in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        values = [value[key] for key in aliases if key in value]
        if not values or any(not _integer(item) for item in values) or len(set(values)) != 1:
            return None
        counters.append(values[0])
    total = sum(counters)
    if total > MAX_SAFE or "total_tokens" in value and (
        not _integer(value["total_tokens"]) or value["total_tokens"] != total
    ):
        return None
    return counters[0], counters[1]


class TokenUsageMeter:
    """One facade instance's exact guard and separately observed usage subtotal.

    Unknown usage estimates only charge the local guard. Python integers remain
    exact internally; unsafe cumulative numbers are exported as null + overflow,
    not rounded/clamped. Returned projections do not alias mutable meter state.
    Rebuilding a model creates a new meter, NOT a durable run/account budget.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._guard = 0
        self._estimated = 0
        self._known = 0
        self._unknown = 0
        self._inputs = 0
        self._outputs = 0

    def __deepcopy__(self, memo: dict[int, Any]) -> TokenUsageMeter:
        # Chat facade/runnable copies can copy private attributes. Preserve the
        # numeric snapshot but never share/copy a native lock or mutable budget.
        copied = TokenUsageMeter()
        memo[id(self)] = copied
        with self._lock:
            for name in ("_guard", "_estimated", "_known", "_unknown", "_inputs", "_outputs"):
                setattr(copied, name, getattr(self, name))
        return copied

    def exhausted(self, budget: int | None) -> bool:
        """Existing post-response > boundary; never a pre-request billing cap."""
        if budget is None:
            return False
        if not _integer(budget) or budget < 1:
            raise ValueError("usage_guard_budget_invalid")
        with self._lock:
            return self._guard > budget

    def record(self, usage: Any, estimate: Callable[[], int]) -> dict[str, Any]:
        counters = parse_usage(usage)
        # Reserve no fabricated charge if the estimator fails. In particular,
        # valid explicit zero must never evaluate the fallback estimator.
        charge = sum(counters) if counters is not None else estimate()
        if not _integer(charge) or counters is None and charge < 1:
            raise ValueError("usage_guard_estimate_invalid")
        with self._lock:
            self._guard += charge
            if counters is None:
                self._unknown += 1
                self._estimated += charge
            else:
                self._known += 1
                self._inputs += counters[0]
                self._outputs += counters[1]
            return self._project(counters)

    def _project(self, counters: tuple[int, int] | None) -> dict[str, Any]:
        # Called only while the original meter lock is held. The returned
        # metadata describes exactly this charge's cumulative prefix, not a mix
        # of another sync worker's guard and this worker's usage counters.
        overflow = self._inputs + self._outputs > MAX_SAFE
        state = ("unknown" if not self._known else "partial" if self._unknown or overflow
                 else "known_for_observed_responses")
        return {
            "guard_kind": GUARD_KIND,
            "current_guard_source": "provider_counters" if counters is not None else "content_tool_heuristic",
            "current_usage": (None if counters is None else {
                "input_tokens": counters[0], "output_tokens": counters[1], "total_tokens": sum(counters),
            }),
            "guard_tokens": self._guard if self._guard <= MAX_SAFE else None,
            "guard_overflow": self._guard > MAX_SAFE,
            "estimated_guard_tokens": self._estimated if self._estimated <= MAX_SAFE else None,
            "estimated_guard_overflow": self._estimated > MAX_SAFE,
            "observed": {
                "state": state,
                "known_usage_turns": self._known,
                "unknown_usage_turns": self._unknown,
                "input_tokens": self._inputs if self._known and not overflow else None,
                "output_tokens": self._outputs if self._known and not overflow else None,
                "subtotal_overflow": overflow,
            },
            "billing_complete": False,
            "account_cap_guaranteed": False,
        }
