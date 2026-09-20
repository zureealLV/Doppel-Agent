"""Bridge synchronous legacy providers and native async providers."""

from __future__ import annotations

from typing import Any

from ..concurrency.limits import ResourceLimits
from ..provider import Message, ModelTurn, next_model_turn


class ProviderAdapter:
    def __init__(
        self,
        provider: Any,
        *,
        profile_id: str = "default",
        limits: ResourceLimits | None = None,
    ):
        self.provider = provider
        self.profile_id = profile_id
        self.limits = limits

    async def anext_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        if self.limits is None:
            return await next_model_turn(self.provider, messages, tools)
        async with self.limits.provider(self.profile_id):
            return await next_model_turn(self.provider, messages, tools)
