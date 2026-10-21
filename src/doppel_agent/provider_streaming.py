"""Context-local token sink and bounded Chat Completions SSE decoding.

The sink receives visible content only, never reasoning or tool arguments. SSE
usage observations are the original JSON event bytes, not an estimated or
reconstructed buffered HTTP response.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .provider_observation import BODY_LIMIT, _finite_float, _reject_constant, _unique_object

StreamCallback = Callable[[str], Awaitable[None] | None]
_callback: ContextVar[StreamCallback | None] = ContextVar('doppel_model_stream', default=None)
_public_output: ContextVar[bool] = ContextVar('doppel_public_model_output', default=True)


@contextmanager
def delegated_model_scope():
    """Child calls keep their receipts but are not root public conversation turns."""
    token = _public_output.set(False)
    try:
        yield
    finally:
        _public_output.reset(token)


def public_model_output() -> bool:
    return _public_output.get()



@contextmanager
def model_stream(callback: StreamCallback):
    """Install a request-local sink; async providers await an asynchronous sink.

    A synchronous provider needs a synchronous sink (it may run in a worker).
    ContextVar propagation via asyncio.to_thread keeps concurrent runs isolated.
    """
    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)


def stream_callback() -> StreamCallback | None:
    return _callback.get()


class StreamLimitError(RuntimeError):
    """Aggregate SSE bytes exceeded the original response limit."""


class SSEModelStream:
    """Strict single-choice SSE assembler with bounded aggregate wire bytes."""

    def __init__(self, *, limit: int = BODY_LIMIT,
                 observe: Callable[[bytes, dict[str, Any]], None] | None = None):
        self.limit, self.observe = limit, observe
        self.size = 0
        self.buffer = b''
        self.pending_lf = False
        self.data: list[bytes] = []
        self.content: list[str] = []
        self.tools: dict[int, dict[str, Any]] = {}
        self.usage: dict[str, Any] | None = None
        self.done = False
        self.finished = False
        self.started = False

    def feed(self, raw: bytes) -> Iterator[str]:
        self.size += len(raw)
        if self.size > self.limit:
            raise StreamLimitError('provider response exceeds 4 MiB')
        if raw and self.pending_lf:
            if raw.startswith(b'\n'):
                raw = raw[1:]
            self.pending_lf = False
        self.buffer += raw
        while True:
            positions = [pos for pos in (self.buffer.find(b'\r'), self.buffer.find(b'\n')) if pos >= 0]
            if not positions:
                return
            pos = min(positions)
            self.pending_lf = self.buffer[pos:] == b'\r'
            width = 2 if self.buffer[pos:pos + 2] == b'\r\n' else 1
            line, self.buffer = self.buffer[:pos], self.buffer[pos + width:]
            if not line:
                if self.data:
                    data, self.data = b'\n'.join(self.data), []
                    yield from self._event(data)
            elif line.startswith(b'data:'):
                value = line[5:]
                self.data.append(value[1:] if value.startswith(b' ') else value)
            # Comments and other SSE fields are ignored, but still size-bound.

    def _event(self, raw: bytes) -> Iterator[str]:
        if self.done:
            raise RuntimeError('invalid provider stream after DONE')
        if raw == b'[DONE]':
            if not self.finished:
                raise RuntimeError('provider stream ended before finish reason')
            self.done = True
            return
        try:
            payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                                 parse_constant=_reject_constant, parse_float=_finite_float)
            if not isinstance(payload, dict) or 'error' in payload:
                raise ValueError()
            choices = payload.get('choices')
            if not isinstance(choices, list) or len(choices) > 1:
                raise ValueError()
            usage = payload.get('usage')
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError()
                self.usage = usage
            if self.observe is not None:
                self.observe(raw, payload)
            if not choices:
                return
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get('index') != 0:
                raise ValueError()
            change = choice.get('delta')
            if not isinstance(change, dict):
                raise ValueError()
            if self.finished and change:
                raise ValueError()
            finish = choice.get('finish_reason')
            if finish is not None:
                if finish not in ('stop', 'tool_calls'):
                    raise RuntimeError('provider stream was truncated or filtered')
                self.finished = True
            text = change.get('content')
            if text is not None:
                if not isinstance(text, str):
                    raise ValueError()
                if text:
                    self.started = True
                    self.content.append(text)
                    yield text
            calls = change.get('tool_calls') or []
            if not isinstance(calls, list):
                raise ValueError()
            for call in calls:
                if not isinstance(call, dict):
                    raise ValueError()
                index = call.get('index')
                if type(index) is not int or not 0 <= index < 128:
                    raise ValueError()
                self.started = True
                current = self.tools.setdefault(index, {'id': '', 'type': 'function',
                                                       'function': {'name': '', 'arguments': ''}})
                function = call.get('function') or {}
                if not isinstance(function, dict):
                    raise ValueError()
                for target, key, value in ((current, 'id', call.get('id')),
                    (current['function'], 'name', function.get('name')),
                    (current['function'], 'arguments', function.get('arguments'))):
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError()
                        target[key] += value
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
            raise RuntimeError('invalid provider stream') from exc

    def finish(self) -> dict[str, Any]:
        if not self.done or self.data or self.buffer.strip():
            raise RuntimeError('provider stream ended without DONE')
        tools = [self.tools[index] for index in sorted(self.tools)]
        ids = [call['id'] for call in tools]
        if any(not value for value in ids) or len(set(ids)) != len(ids) or any(
            not call['function']['name'] for call in tools
        ):
            raise RuntimeError('invalid provider stream tool calls')
        return {'choices': [{'message': {'content': ''.join(self.content), 'tool_calls': tools}}],
                'usage': self.usage}
