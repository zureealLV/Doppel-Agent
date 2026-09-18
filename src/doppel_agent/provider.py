"""Model boundary and OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def to_api(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}}
                for call in self.tool_calls
            ]
        return result


@dataclass(frozen=True)
class ModelTurn:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] | None = None


class Provider(Protocol):
    def next_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn: ...


class MockProvider:
    """Deterministic offline fixture; never pretends to be an LLM."""

    def next_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        last = messages[-1]
        if last.role == "tool":
            return ModelTurn(content=f"Tool result:\n{last.content}")
        if last.role == "user" and last.content.startswith("read "):
            path = last.content[5:].strip()
            if path:
                return ModelTurn(tool_calls=(ToolCall("mock-1", "read_file", {"path": path}),))
        return ModelTurn(content="Offline mock received the request. Configure an API provider for real coding tasks.")


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("remote providers must use HTTPS")
        if not model.strip():
            raise ValueError("model is required")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def next_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        body = {"model": self.model, "messages": [message.to_api() for message in messages], "stream": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(self.endpoint, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
        except HTTPError as exc:
            raise RuntimeError(f"provider HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"provider connection failed: {exc.reason}") from exc
        if len(raw) > 4 * 1024 * 1024:
            raise RuntimeError("provider response exceeds 4 MiB")
        try:
            payload = json.loads(raw)
            message = payload["choices"][0]["message"]
            calls = []
            for call in message.get("tool_calls") or []:
                function = call["function"]
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be an object")
                calls.append(ToolCall(call["id"], function["name"], arguments))
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                usage = None
            return ModelTurn(message.get("content") or "", tuple(calls), usage)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("invalid provider response") from exc
