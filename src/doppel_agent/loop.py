"""Bounded model -> tool -> model loop."""

from __future__ import annotations

from .events import EventBus
from .provider import Message, Provider
from .tools import ToolRegistry
from .context.policy import ContextPolicy


SYSTEM_PROMPT = (
    "You are Doppel Agent, a local coding assistant. Use tools to inspect the workspace "
    "before editing. Never claim a tool succeeded unless its result confirms success. "
    "Stay within the workspace and respect denied permissions."
)


class AgentLoop:
    def __init__(self, provider: Provider, tools: ToolRegistry, bus: EventBus, max_steps: int = 8, context_limit_tokens: int = 32_000):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.provider = provider
        self.tools = tools
        self.bus = bus
        self.max_steps = max_steps
        self.context = ContextPolicy(context_limit_tokens)

    def run(self, prompt: str, history: list[Message] | None = None) -> str:
        history = list(history or [])
        if any(message.role not in ("user", "assistant") or message.tool_calls or message.tool_call_id for message in history):
            raise ValueError("conversation history may contain only plain user/assistant messages")
        messages = [Message("system", SYSTEM_PROMPT), *history, Message("user", prompt)]
        self.bus.emit("run_started", prompt=prompt)
        for step in range(1, self.max_steps + 1):
            messages, waterline = self.context.compact(messages)
            self.bus.emit("context_watermark", step=step, **waterline)
            if waterline["compacted"]:
                self.bus.emit("context_compacted", step=step, **waterline)
            turn = self.provider.next_turn(messages, self.tools.schemas())
            self.bus.emit("model_turn", step=step, tool_call_count=len(turn.tool_calls))
            if turn.usage:
                self.bus.emit("model_usage", step=step, **turn.usage)
            if not turn.tool_calls:
                self.bus.emit("run_completed", answer=turn.content)
                return turn.content
            messages.append(Message("assistant", turn.content, tool_calls=turn.tool_calls))
            for call in turn.tool_calls:
                self.bus.emit("tool_requested", name=call.name, arguments=call.arguments)
                try:
                    output = self.tools.execute(call.name, call.arguments)
                    self.bus.emit("tool_completed", name=call.name, output_bytes=len(output.encode("utf-8")))
                except (OSError, ValueError, PermissionError, UnicodeError, TimeoutError) as exc:
                    output = f"Tool error ({type(exc).__name__}): {exc}"
                    self.bus.emit("tool_failed", name=call.name, error=output)
                messages.append(Message("tool", output, tool_call_id=call.id))
        raise RuntimeError("max_steps_exceeded")
