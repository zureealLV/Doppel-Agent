"""LangChain chat-model facade over Doppel's provider boundary."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, PrivateAttr, StrictInt

from ..provider import Message, ToolCall, next_model_turn
from ..provider_usage import MAX_SAFE, TokenUsageMeter


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _guard_estimate(turn: Any) -> int:
    """Compatibility content/tool heuristic ONLY for the internal local guard.

    Does not include request input, framing, provider tokenizer/cache/reasoning,
    retries or failures. Not actual tokens, billing or a request/account limit.
    """
    return max(1, len(turn.content) // 4) + sum(
        max(1, len(json.dumps(call.arguments, default=str)) // 4) for call in turn.tool_calls
    )


def _to_doppel_message(message: BaseMessage) -> Message:
    role = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
        "tool": "tool",
    }.get(message.type, message.type)
    calls: tuple[ToolCall, ...] = ()
    if isinstance(message, AIMessage):
        calls = tuple(
            ToolCall(str(call.get("id", "")), str(call["name"]), dict(call.get("args") or {}))
            for call in message.tool_calls
        )
    return Message(
        role,
        _content_text(message.content),
        tool_call_id=message.tool_call_id if isinstance(message, ToolMessage) else None,
        tool_calls=calls,
    )


class DoppelChatModel(BaseChatModel):
    """Make any Doppel sync/async provider usable by LangChain agents."""

    provider: Any
    model_label: str = "doppel-provider"
    model_name: str = "doppel-runtime"
    token_budget: StrictInt | None = Field(default=None, gt=0, le=MAX_SAFE)

    _usage_meter: TokenUsageMeter = PrivateAttr(default_factory=TokenUsageMeter)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "doppel-provider-adapter"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_label": self.model_label}

    def _get_ls_params(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ls_provider": "doppel", "ls_model_name": self.model_name}

    @staticmethod
    def _tool_schemas(tools: list[Any] | None) -> list[dict[str, Any]]:
        return [convert_to_openai_tool(tool) for tool in (tools or [])]

    def bind_tools(
        self,
        tools: list[Any],
        *,
        tool_choice: str | dict[str, Any] | bool | None = None,
        **kwargs: Any,
    ):
        formatted = self._tool_schemas(tools)
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(tools=formatted, **kwargs)

    def _result(self, turn: Any) -> ChatResult:
        # Preserve original raw usage (including cache/reasoning details) for
        # compatibility callbacks. Never replace it with a synthetic estimate.
        usage = turn.usage if isinstance(turn.usage, dict) else {}
        meter = self._usage_meter.record(usage, lambda: _guard_estimate(turn))
        budget_exhausted = self._usage_meter.exhausted(self.token_budget)
        message = AIMessage(
            content=(
                "Subagent token budget exhausted; return the evidence collected so far."
                if budget_exhausted
                else turn.content
            ),
            tool_calls=[] if budget_exhausted else [
                {"id": call.id, "name": call.name, "args": call.arguments, "type": "tool_call"}
                for call in turn.tool_calls
            ],
            response_metadata={
                "doppel_usage": usage,
                # Kept as an explicitly labeled compatibility guard field,
                # null on unsafe cumulative totals; not provider billing.
                "doppel_spent_tokens": meter["guard_tokens"],
                "doppel_spent_tokens_kind": meter["guard_kind"],
                "doppel_usage_meter": meter,
                "doppel_token_budget": self.token_budget,
            },
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"token_usage": usage, **(
                {"doppel_provider_call_id": turn.provider_call_id} if turn.provider_call_id is not None else {}
            )},
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        method = getattr(self.provider, "next_turn", None)
        if method is None:
            raise RuntimeError("provider only supports asynchronous model calls")
        turn = method(
            [_to_doppel_message(message) for message in messages],
            list(kwargs.get("tools") or []),
        )
        return self._result(turn)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        turn = await next_model_turn(
            self.provider,
            [_to_doppel_message(message) for message in messages],
            list(kwargs.get("tools") or []),
        )
        return self._result(turn)
