"""LangChain chat-model facade over Doppel's provider boundary."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr

from ..provider import Message, ToolCall, next_model_turn


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


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
    token_budget: int | None = None

    _spent_tokens: int = PrivateAttr(default=0)

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
        usage = turn.usage or {}
        charged = usage.get("total_tokens")
        if not isinstance(charged, int):
            charged = sum(
                value
                for key, value in usage.items()
                if key in {"input_tokens", "output_tokens", "prompt_tokens", "completion_tokens"}
                and isinstance(value, int)
            )
        if not charged:
            charged = max(1, len(turn.content) // 4) + sum(
                max(1, len(json.dumps(call.arguments, default=str)) // 4) for call in turn.tool_calls
            )
        self._spent_tokens += charged
        budget_exhausted = self.token_budget is not None and self._spent_tokens > self.token_budget
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
                "doppel_spent_tokens": self._spent_tokens,
                "doppel_token_budget": self.token_budget,
            },
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"token_usage": usage},
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
