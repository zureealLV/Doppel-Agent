"""Cancellable model -> tool -> model loop with optional explicit turn budget."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Callable

from .events import EventBus
from .provider import Message, Provider, ToolCall
from .tools import ToolRegistry
from .context.policy import ContextPolicy


PUBLIC_PROGRESS_PROMPT = (
    "Use the same language as the user. Before the first tool call, give a brief public acknowledgement "
    "and concrete next action. After meaningful findings or a change of plan, provide a concise public "
    "progress update, then continue using tools as needed. Do not narrate every tool call. These updates "
    "are user-facing plans and evidence summaries, not private chain-of-thought. Do not expose private "
    "reasoning. Finish with a separate evidence-grounded summary and any remaining gaps. "
)

SYSTEM_PROMPT = PUBLIC_PROGRESS_PROMPT + (
    "You are Doppel Agent, a local coding assistant. Use tools to inspect the workspace "
    "before editing. For code review, start with workspace_map, narrow candidates with "
    "search_text, then inspect only relevant line ranges; avoid reading whole files without need. "
    "Report findings with severity, file and line evidence. Never claim a tool succeeded unless "
    "its result confirms success. Stay within the workspace and respect denied permissions."
)

REVIEW_SYSTEM_PROMPT = PUBLIC_PROGRESS_PROMPT + (
    "You are Doppel Agent in focused, read-only code-review mode. Start with exactly one "
    "workspace_map call, use search_text to locate a concrete risk, then read_file_range only "
    "for the smallest relevant ranges. Do not inventory directories repeatedly. Stop inspecting "
    "when the requested scope has been checked or no further useful inspection remains, and report supported findings, each including severity, "
    "file, line, reproduction reasoning and a minimal fix. If evidence does not support a high-priority "
    "finding, say so. Never invent evidence."
)

FINAL_TURN_PROMPT = (
    "The configured inspection step budget has been reached normally; this turn is reserved "
    "for synthesis. This is not a tool permission or connection failure. Do not emit tool calls "
    "or tool-call markup. Answer the original user request with a substantive final response "
    "using only the evidence already collected. Separate supported findings from unchecked "
    "areas; do not invent evidence or refuse the entire task merely because inspection ended. "
    "Use the same language as the original user request. If inspection is incomplete, briefly "
    "state the step limit and what remains unchecked, without claiming that tools were revoked "
    "or broken."
)


@dataclass(frozen=True)
class LoopContinuation:
    """The original Core loop's pending call boundary, not a second executor."""

    step: int
    messages: tuple[Message, ...]
    pending_calls: tuple[ToolCall, ...]


class LoopInterrupted(Exception):
    def __init__(self, interrupt_id: str, value: dict):
        super().__init__("tool approval required")
        self.interrupt_id, self.value = interrupt_id, value


class AgentLoop:
    def __init__(self, provider: Provider, tools: ToolRegistry, bus: EventBus, max_steps: int | None = None, context_limit_tokens: int = 32_000, system_prompt: str = SYSTEM_PROMPT):
        if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
            raise ValueError("max_steps must be positive")
        self.provider = provider
        self.tools = tools
        self.bus = bus
        self.max_steps = max_steps
        self.context = ContextPolicy(context_limit_tokens)
        self.system_prompt = system_prompt

    def run(self, prompt: str, history: list[Message] | None = None, *,
            continuation: LoopContinuation | None = None,
            tool_executor: Callable[[ToolRegistry, ToolCall, LoopContinuation], str] | None = None,
            check_cancelled: Callable[[], None] | None = None) -> str:
        history = list(history or [])
        if any(message.role not in ("user", "assistant") or message.tool_calls or message.tool_call_id for message in history):
            raise ValueError("conversation history may contain only plain user/assistant messages")
        if continuation is not None:
            if (continuation.step < 1 or (self.max_steps is not None and continuation.step > self.max_steps)
                    or not continuation.pending_calls):
                raise ValueError("invalid loop continuation")
            messages = list(continuation.messages)
            start_step, pending = continuation.step, continuation.pending_calls
            self.bus.emit("run_continued", step=start_step)
        else:
            messages = [Message("system", self.system_prompt), *history, Message("user", prompt)]
            start_step, pending = 1, ()
            self.bus.emit("run_started", prompt=prompt)
        for step in count(start_step):
            if check_cancelled is not None:
                check_cancelled()
            if pending:
                calls, pending = pending, ()
            else:
                messages, waterline = self.context.compact(messages)
                self.bus.emit("context_watermark", step=step, **waterline)
                if waterline["compacted"]:
                    self.bus.emit("context_compacted", step=step, **waterline)
                # Reserve the final model turn for synthesis.
                schemas = [] if step == self.max_steps else self.tools.schemas()
                if step == self.max_steps:
                    self.bus.emit("run.budget_exhausted", step=step, max_steps=self.max_steps, reason="step_limit")
                # Runtime guidance is not a new user goal or conversation history.
                turn_messages = ([*messages, Message("system", FINAL_TURN_PROMPT)]
                                 if step == self.max_steps else messages)
                turn = self.provider.next_turn(turn_messages, schemas)
                self.bus.emit("model_turn", step=step, tool_call_count=len(turn.tool_calls))
                if turn.usage:
                    self.bus.emit("model_usage", step=step, **turn.usage)
                if check_cancelled is not None:
                    check_cancelled()
                if not turn.tool_calls:
                    self.bus.emit("run_completed", answer=turn.content)
                    return turn.content
                if step == self.max_steps:
                    # An uncooperative provider cannot spend the synthesis
                    # turn on more tools, even when it ignores empty schemas.
                    raise RuntimeError("max_steps_exceeded")
                messages.append(Message("assistant", turn.content, tool_calls=turn.tool_calls))
                calls = turn.tool_calls
            for index, call in enumerate(calls):
                if check_cancelled is not None:
                    check_cancelled()
                self.bus.emit("tool_requested", name=call.name, arguments=call.arguments)
                try:
                    output = (tool_executor(self.tools, call, LoopContinuation(step, tuple(messages), calls[index:]))
                              if tool_executor is not None else self.tools.execute(call.name, call.arguments))
                    self.bus.emit("tool_completed", name=call.name, output_bytes=len(output.encode("utf-8")))
                except (OSError, ValueError, PermissionError, UnicodeError, TimeoutError) as exc:
                    output = f"Tool error ({type(exc).__name__}): {exc}"
                    self.bus.emit("tool_failed", name=call.name, error=output)
                messages.append(Message("tool", output, tool_call_id=call.id))
        raise RuntimeError("max_steps_exceeded")
