"""Runtime selection without leaking implementation details into API layers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..concurrency.limits import ResourceLimits
from ..billing_tariff import validate_price_receipt
from ..provider import Provider
from ..workspace.process_supervisor import ProcessSupervisor
from .base import AgentRuntime
from .deep import DeepAgentRuntime
from .graph import GraphRuntime
from .legacy import LegacyRuntime
from .provider_recording import ProviderReceiptFault


def create_runtime(
    mode: str,
    workspace: Path,
    provider: Provider,
    *,
    state_root: Path | None = None,
    core_options: dict[str, Any] | None = None,
    resource_limits: ResourceLimits | None = None,
    process_supervisor: ProcessSupervisor | None = None,
    reviewed_legacy: bool = False,
    require_verification_review: bool = False,
    provider_receipt_fault: ProviderReceiptFault | None = None,
    billing_price_receipt: dict | None = None,
) -> AgentRuntime:
    # Trusted owner policy, not a provider argument or user-overridable option.
    # Direct factory callers keep their distinct compatibility default.
    if type(require_verification_review) is not bool:
        raise ValueError("verification_review_policy_invalid")
    frozen_price = None if billing_price_receipt is None else validate_price_receipt(billing_price_receipt)
    if mode == "legacy":
        return LegacyRuntime(
            workspace,
            provider,
            state_root=state_root,
            core_options=core_options,
            reviewed=reviewed_legacy,
            require_verification_review=require_verification_review,
            resource_limits=resource_limits,
            process_supervisor=process_supervisor,
            provider_receipt_fault=provider_receipt_fault,
            billing_price_receipt=frozen_price,
        )
    if mode == "graph":
        options = dict(core_options or {})
        return GraphRuntime(
            workspace,
            provider,
            checkpoint_path=(state_root or workspace / ".doppel-agent") / "checkpoints.sqlite3",
            max_steps=int(options.get("max_steps", 8)),
            resource_limits=resource_limits,
            provider_receipt_fault=provider_receipt_fault,
            billing_price_receipt=frozen_price,
        )
    if mode == "deep":
        options = dict(core_options or {})
        return DeepAgentRuntime(
            workspace,
            provider,
            checkpoint_path=(state_root or workspace / ".doppel-agent") / "deep-checkpoints.sqlite3",
            max_steps=int(options.get("max_steps", 12)),
            allow_write=bool(options.get("allow_write", False)),
            allow_command=bool(options.get("allow_command", False)),
            require_verification_review=require_verification_review,
            max_subagents=2 if options.get("allow_delegate", False) else 0,
            resource_limits=resource_limits,
            process_supervisor=process_supervisor,
            provider_receipt_fault=provider_receipt_fault,
            billing_price_receipt=frozen_price,
        )
    raise ValueError(f"unsupported runtime mode: {mode}")
