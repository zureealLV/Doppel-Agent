"""Model boundary and OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
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
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
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


class AsyncProvider(Protocol):
    async def anext_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn: ...


async def next_model_turn(
    provider: Any,
    messages: list[Message],
    tools: list[dict[str, Any]],
) -> ModelTurn:
    """Call a native async provider, or isolate a legacy sync provider."""
    async_method = getattr(provider, "anext_turn", None)
    if async_method is not None:
        return await async_method(messages, tools)
    return await asyncio.to_thread(provider.next_turn, messages, tools)


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
        return ModelTurn(
            content="Offline mock received the request. Configure an API provider for real coding tasks."
        )


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError("remote providers must use HTTPS")
        if not model.strip():
            raise ValueError("model is required")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def next_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        body = {
            "model": self.model,
            "messages": [message.to_api() for message in messages],
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
        except HTTPError as exc:
            raise RuntimeError(f"provider HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"provider connection failed: {exc.reason}") from exc
        if len(raw) > 4 * 1024 * 1024:
            raise RuntimeError("provider response exceeds 4 MiB")
        return parse_model_turn(raw)


def parse_model_turn(raw: bytes | str | dict[str, Any]) -> ModelTurn:
    """Validate the small Chat Completions response surface used by Doppel."""
    try:
        payload = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
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


class ProviderCircuitOpen(RuntimeError):
    """The provider profile is temporarily blocked after repeated failures."""


class AsyncOpenAICompatibleProvider:
    """Long-lived async provider with bounded retry and a small circuit breaker."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        client: Any | None = None,
        max_attempts: int = 3,
        retry_budget_seconds: float = 10,
        circuit_failure_threshold: int = 5,
        circuit_reset_seconds: float = 30,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        random_source: Callable[[], float] = random.random,
    ):
        import httpx

        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url must be an HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            raise ValueError("remote providers must use HTTPS")
        if not model.strip():
            raise ValueError("model is required")
        if max_attempts < 1 or retry_budget_seconds < 0 or circuit_failure_threshold < 1:
            raise ValueError("invalid provider reliability settings")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.max_attempts = max_attempts
        self.retry_budget_seconds = retry_budget_seconds
        self.circuit_failure_threshold = circuit_failure_threshold
        self.circuit_reset_seconds = circuit_reset_seconds
        self._sleep = sleep
        self._random = random_source
        self._failures = 0
        self._open_until = 0.0
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                return None

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        requested = self._retry_after(retry_after)
        if requested is not None:
            return requested
        return min(4.0, 0.25 * (2**attempt)) * (0.75 + self._random() * 0.5)

    async def anext_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        import httpx

        now = time.monotonic()
        if now < self._open_until:
            raise ProviderCircuitOpen("provider circuit is open")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_api() for message in messages],
            "stream": False,
        }
        if tools:
            body.update(tools=tools, tool_choice="auto")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        deadline = time.monotonic() + self.retry_budget_seconds
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = await self._client.post(self.endpoint, json=body, headers=headers)
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    raise httpx.HTTPStatusError(
                        f"retryable provider HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                if len(response.content) > 4 * 1024 * 1024:
                    raise RuntimeError("provider response exceeds 4 MiB")
                result = parse_model_turn(response.content)
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            ) as exc:
                last_error = exc
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable or attempt + 1 >= self.max_attempts:
                    break
                retry_after = (
                    exc.response.headers.get("Retry-After")
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None
                )
                delay = self._backoff(attempt, retry_after)
                if time.monotonic() + delay > deadline:
                    break
                if self._sleep is None:
                    await asyncio.sleep(delay)
                else:
                    await self._sleep(delay)
                continue
            except asyncio.CancelledError:
                raise
            else:
                self._failures = 0
                self._open_until = 0.0
                return result
        self._failures += 1
        if self._failures >= self.circuit_failure_threshold:
            self._open_until = time.monotonic() + self.circuit_reset_seconds
        if isinstance(last_error, httpx.HTTPStatusError):
            raise RuntimeError(f"provider HTTP {last_error.response.status_code}") from last_error
        raise RuntimeError("provider request failed") from last_error

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
