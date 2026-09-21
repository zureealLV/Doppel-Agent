"""Nodes for the focused LangGraph runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.types import interrupt

from ..concurrency.limits import ResourceLimits
from ..context.policy import ContextPolicy
from ..loop import SYSTEM_PROMPT
from ..persistence.tool_ledger import ToolExecutionLedger
from ..provider import Message, ModelTurn, Provider, ToolCall, next_model_turn
from ..tools import ToolRegistry
from .state import DoppelState, SerializedMessage

FINAL_TURN_PROMPT = (
    "Tool access is now closed. Do not emit tool calls. "
    "Using only the evidence already collected, write the final answer now."
)

SENSITIVE_CAPABILITIES = {"workspace_write", "command_execute", "mcp_execute"}


def serialize_message(message: Message) -> SerializedMessage:
    data: SerializedMessage = {"role": message.role, "content": message.content}
    if message.tool_call_id:
        data["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        data["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments} for call in message.tool_calls
        ]
    return data


def deserialize_message(data: SerializedMessage) -> Message:
    calls = tuple(
        ToolCall(call["id"], call["name"], call["arguments"]) for call in data.get("tool_calls", [])
    )
    return Message(
        data["role"],
        data.get("content", ""),
        tool_call_id=data.get("tool_call_id"),
        tool_calls=calls,
    )


class FocusedGraphNodes:
    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        *,
        context_limit_tokens: int = 32_000,
        ledger: ToolExecutionLedger | None = None,
        resource_limits: ResourceLimits | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.context = ContextPolicy(context_limit_tokens)
        self.ledger = ledger
        self.resource_limits = resource_limits

    async def reason(self, state: DoppelState) -> dict[str, Any]:
        messages = [deserialize_message(item) for item in state["messages"]]
        step = state.get("step_count", 1)
        max_steps = state.get("max_steps", 8)
        final_turn = step >= max_steps
        if final_turn:
            messages.append(Message("user", FINAL_TURN_PROMPT))
        messages, _ = self.context.compact(messages)
        schemas = [] if final_turn else self.tools.schemas()
        turn: ModelTurn = await next_model_turn(self.provider, messages, schemas)
        if final_turn and turn.tool_calls:
            return {
                "messages": [serialize_message(item) for item in messages],
                "status": "failed",
                "answer": "RuntimeError: model requested tools after the final synthesis turn",
                "error": {"code": "tool_call_after_budget", "step": step},
            }
        messages.append(Message("assistant", turn.content, tool_calls=turn.tool_calls))
        update: dict[str, Any] = {"messages": [serialize_message(item) for item in messages]}
        if not turn.tool_calls:
            update.update(status="completed", answer=turn.content, error=None)
        return update

    def requires_approval(self, state: DoppelState) -> bool:
        messages = [deserialize_message(item) for item in state["messages"]]
        assistant = messages[-1]
        for call in assistant.tool_calls:
            try:
                capability = self.tools.capability(call.name)
            except ValueError:
                continue
            if capability in SENSITIVE_CAPABILITIES:
                return True
        return False

    async def request_approval(self, state: DoppelState) -> dict[str, Any]:
        messages = [deserialize_message(item) for item in state["messages"]]
        assistant = messages[-1]
        requested = []
        for call in assistant.tool_calls:
            try:
                capability = self.tools.capability(call.name)
            except ValueError:
                continue
            if capability in SENSITIVE_CAPABILITIES:
                requested.append(
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "capability": capability,
                    }
                )
        decision = interrupt({"kind": "tool_approval", "tool_calls": requested})
        if not isinstance(decision, dict) or decision.get("action") not in {
            "approve",
            "reject",
            "edit",
        }:
            raise ValueError("approval decision must be approve, reject, or edit")
        action = decision["action"]
        if action == "reject":
            for call in assistant.tool_calls:
                messages.append(
                    Message(
                        "tool",
                        "Tool error (PermissionError): rejected by user",
                        tool_call_id=call.id,
                    )
                )
            return {
                "messages": [serialize_message(item) for item in messages],
                "approval": {
                    "action": "reject",
                    "tool_call_ids": [call.id for call in assistant.tool_calls],
                },
                "step_count": state.get("step_count", 1) + 1,
            }
        if action == "edit":
            edited = decision.get("tool_calls")
            if not isinstance(edited, list):
                raise ValueError("edited approval requires tool_calls")
            original = {call.id: call for call in assistant.tool_calls}
            replacement: list[ToolCall] = []
            for item in edited:
                if not isinstance(item, dict) or item.get("id") not in original:
                    raise ValueError("edited tool call id is invalid")
                source = original[item["id"]]
                if item.get("name", source.name) != source.name or not isinstance(
                    item.get("arguments"), dict
                ):
                    raise ValueError("edited tool call name or arguments are invalid")
                replacement.append(ToolCall(source.id, source.name, item["arguments"]))
            if {call.id for call in replacement} != set(original):
                raise ValueError("edited approval must include every tool call")
            messages[-1] = Message("assistant", assistant.content, tool_calls=tuple(replacement))
        return {
            "messages": [serialize_message(item) for item in messages],
            "approval": {
                "action": action,
                "tool_call_ids": [call.id for call in messages[-1].tool_calls],
            },
        }

    async def execute_tools(self, state: DoppelState) -> dict[str, Any]:
        messages = [deserialize_message(item) for item in state["messages"]]
        assistant = messages[-1]
        if assistant.role != "assistant" or not assistant.tool_calls:
            return {
                "status": "failed",
                "answer": "RuntimeError: tool node entered without pending tool calls",
                "error": {"code": "invalid_tool_state"},
            }
        for call in assistant.tool_calls:
            try:
                if self.tools.capability(call.name) == "command_execute" and self.resource_limits:
                    async with self.resource_limits.command():
                        output = await self._execute_one(state["run_id"], call)
                else:
                    output = await self._execute_one(state["run_id"], call)
            except (
                OSError,
                ValueError,
                PermissionError,
                UnicodeError,
                TimeoutError,
            ) as exc:
                output = f"Tool error ({type(exc).__name__}): {exc}"
            messages.append(Message("tool", output, tool_call_id=call.id))
        return {
            "messages": [serialize_message(item) for item in messages],
            "step_count": state.get("step_count", 1) + 1,
        }

    async def _execute_one(self, run_id: str, call: ToolCall) -> str:
        if self.ledger:
            if self.tools.is_async(call.name):
                output, _ = await self.ledger.aexecute_once(
                    run_id,
                    call.id,
                    call.name,
                    call.arguments,
                    lambda: self.tools.aexecute(
                        call.name,
                        call.arguments,
                        run_id=run_id,
                        tool_call_id=call.id,
                    ),
                )
                return output
            output, _ = await asyncio.to_thread(
                self.ledger.execute_once,
                run_id,
                call.id,
                call.name,
                call.arguments,
                lambda name=call.name, arguments=call.arguments: self.tools.execute(name, arguments),
            )
            return output
        return await self.tools.aexecute(
            call.name,
            call.arguments,
            run_id=run_id,
            tool_call_id=call.id,
        )


def initial_messages(prompt: str, history: tuple[Message, ...]) -> list[SerializedMessage]:
    messages = [Message("system", SYSTEM_PROMPT), *history, Message("user", prompt)]
    return [serialize_message(message) for message in messages]
