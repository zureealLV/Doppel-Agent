"""Bounded model -> tool -> model loop."""

from __future__ import annotations

from .events import EventBus
from .provider import Message, Provider
from .tools import ToolRegistry
from .context.policy import ContextPolicy


SYSTEM_PROMPT = (
    "You are Doppel Agent, a local coding assistant. Use tools to inspect the workspace "
    "before editing. For code review, start with workspace_map, narrow candidates with "
    "search_text, then inspect only relevant line ranges; avoid reading whole files without need. "
    "Report findings with severity, file and line evidence. Never claim a tool succeeded unless "
    "its result confirms success. Stay within the workspace and respect denied permissions."
)

REVIEW_SYSTEM_PROMPT = (
    "You are Doppel Agent in focused, read-only code-review mode. Start with exactly one "
    "workspace_map call, use search_text to locate a concrete risk, then read_file_range only "
    "for the smallest relevant ranges. Do not inventory directories repeatedly. Stop inspecting "
    "once evidence is sufficient and answer with at most two findings, each including severity, "
    "file, line, reproduction reasoning and a minimal fix. If evidence does not support a high-priority "
    "finding, say so. Never invent evidence."
)


class AgentLoop:
    def __init__(self, provider: Provider, tools: ToolRegistry, bus: EventBus, max_steps: int = 8, context_limit_tokens: int = 32_000, system_prompt: str = SYSTEM_PROMPT):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.provider = provider
        self.tools = tools
        self.bus = bus
        self.max_steps = max_steps
        self.context = ContextPolicy(context_limit_tokens)
        self.system_prompt = system_prompt

    def run(self, prompt: str, history: list[Message] | None = None) -> str:
        history = list(history or [])
        if any(message.role not in ("user", "assistant") or message.tool_calls or message.tool_call_id for message in history):
            raise ValueError("conversation history may contain only plain user/assistant messages")
        messages = [Message("system", self.system_prompt), *history, Message("user", prompt)]
        self.bus.emit("run_started", prompt=prompt)
        for step in range(1, self.max_steps + 1):
            if step == self.max_steps:
                messages.append(Message(
                    "user",
                    "Tool access is now closed. Do not emit tool calls or tool-call markup. "
                    "Using only the evidence already collected, write the final review report now.",
                ))
            messages, waterline = self.context.compact(messages)
            self.bus.emit("context_watermark", step=step, **waterline)
            if waterline["compacted"]:
                self.bus.emit("context_compacted", step=step, **waterline)
            # Reserve the final model turn for synthesis. This prevents a run from
            # ending immediately after a useful last tool result with no answer.
            schemas = [] if step == self.max_steps else self.tools.schemas()
            turn = self.provider.next_turn(messages, schemas)
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
