"""Model boundary and OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from functools import wraps
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .persistence.owned import await_durable
from .provider_observation import BODY_LIMIT, begin_async, begin_sync, finish_async, finish_sync, native_entered


@dataclass
class _RunRetryAllowance:
    retries_left: int = 2
    backoff_seconds_left: float = 10

    def reserve(self, delay: float) -> bool:
        if self.retries_left <= 0 or delay > self.backoff_seconds_left:
            return False
        self.retries_left -= 1
        self.backoff_seconds_left -= delay
        return True


_run_retry_allowance: ContextVar[_RunRetryAllowance | None] = ContextVar(
    'doppel_run_retry_allowance', default=None
)


def bounded_run_retries(*, fresh: bool = False):
    """Share two retries/10s cumulative backoff across one execution segment.

    Runtime fallback inherits the same allowance. Service admission, resume and
    each child generation force a fresh context, never mutable provider-global
    state. Per-call retry windows/attempt limits still apply independently.
    """
    def decorate(method):
        @wraps(method)
        async def wrapped(*args, **kwargs):
            if not fresh and _run_retry_allowance.get() is not None:
                return await method(*args, **kwargs)
            token = _run_retry_allowance.set(_RunRetryAllowance())
            try:
                return await method(*args, **kwargs)
            finally:
                _run_retry_allowance.reset(token)
        return wrapped
    return decorate


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
    # Original runtime recorder identity, NOT a provider request/billing ID,
    # effect/approval authority or payload-controlled parent/child scope.
    provider_call_id: str | None = None


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
    return await await_durable(asyncio.to_thread(provider.next_turn, messages, tools))


class MockProvider:
    """Deterministic offline fixture; never pretends to be an LLM."""

    def next_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        last = messages[-1]
        if last.role == "tool":
            return ModelTurn(content=f"Tool result:\n{last.content}")
        if last.role == "user" and last.content.startswith("read "):
            path = last.content[5:].strip()
            if path:
                available = {tool["function"]["name"] for tool in tools}
                if "read_file" not in available and "read_file_range" in available:
                    return ModelTurn(tool_calls=(ToolCall(
                        "mock-1", "read_file_range", {"path": path, "start_line": 1, "end_line": 200},
                    ),))
                descriptor = next(
                    (tool["function"] for tool in tools if tool["function"]["name"] == "read_file"),
                    {},
                )
                properties = descriptor.get("parameters", {}).get("properties", {})
                path_key = "file_path" if "file_path" in properties else "path"
                return ModelTurn(tool_calls=(ToolCall("mock-1", "read_file", {path_key: path}),))
        return ModelTurn(
            content="Offline mock received the request. Configure an API provider for real coding tasks."
        )


class OpenAICompatibleProvider:
    def __init__(
        self, base_url: str, model: str, api_key: str = "", timeout: float = 60,
        *, temperature: float | None = None,
        thinking: str | None = None, max_tokens: int | None = None,
    ):
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
        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.completion_options = _completion_options(base_url, thinking, max_tokens)

    def next_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        native_entered(self)  # Context-local only; direct unrecorded API unchanged.
        body = {
            "model": self.model,
            "messages": [message.to_api() for message in messages],
            "stream": False,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body.update(self.completion_options)
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
        attempt = begin_sync("urllib_urlopen")
        try:
            try:
                if attempt is not None:
                    attempt.entered = True
                response_error: BaseException | None = None
                with urlopen(request, timeout=self.timeout) as response:
                    try:
                        if attempt is not None:
                            status = getattr(response, "status", None)
                            attempt.http_status = status if type(status) is int and 100 <= status <= 599 else None
                            attempt.response_state = "unavailable"
                        raw = response.read(BODY_LIMIT + 1)
                        if attempt is not None:
                            attempt.observe(raw, getattr(response, "status", None))
                    except BaseException as exc:
                        response_error = exc
                    finally:
                        if attempt is not None:
                            # Distinguish original context close failure even
                            # when body read also failed. No detached cleanup.
                            attempt.failure = "response_cleanup"
                if attempt is not None:
                    attempt.failure = "transport_failure"  # Original close settled.
                if response_error is not None:
                    raise response_error
            except HTTPError as exc:
                kind = "rate_limited" if exc.code == 429 else "http_status"
                cleanup_failed = attempt is not None and attempt.failure == "response_cleanup"
                if attempt is not None:
                    attempt.http_status = exc.code
                    if not cleanup_failed:
                        attempt.failure = kind
                        attempt.response_state = "unavailable"
                # The original HTTPError owns its response. Bound observation,
                # retain numeric usage on HTTP failures, close this SAME handle.
                # Body read failure doesn't erase the original HTTP classification.
                try:
                    if attempt is not None and not cleanup_failed and getattr(exc, "fp", None) is not None:
                        try:
                            attempt.observe(exc.read(BODY_LIMIT + 1), exc.code)
                        except Exception:
                            attempt.response_state = "unavailable"
                finally:
                    try:
                        exc.close()
                    except BaseException:
                        if attempt is not None:
                            attempt.failure = "response_cleanup"
                        raise
                raise ProviderRequestError(
                    f"provider HTTP {exc.code}", kind=kind, attempts=1, status_code=exc.code,
                ) from exc
            except URLError as exc:
                kind = "timeout" if isinstance(exc.reason, TimeoutError) else "connection"
                if attempt is not None and attempt.failure != "response_cleanup":
                    attempt.failure = kind
                raise ProviderRequestError(f"provider {kind} failed", kind=kind, attempts=1) from exc
            except TimeoutError as exc:
                if attempt is not None and attempt.failure != "response_cleanup":
                    attempt.failure = "timeout"
                raise ProviderRequestError("provider timeout", kind="timeout", attempts=1) from exc
            if len(raw) > BODY_LIMIT:
                if attempt is not None:
                    attempt.failure = "response_limit"
                raise RuntimeError("provider response exceeds 4 MiB")
            if attempt is not None:
                attempt.failure = "invalid_response"
            turn = parse_model_turn(raw)
            if attempt is not None:
                attempt.outcome, attempt.failure = "returned", None
            return turn
        except asyncio.CancelledError:
            if attempt is not None:
                attempt.outcome = "cancelled"
                if attempt.failure != "response_cleanup":
                    attempt.failure = "cancelled"
            raise
        finally:
            # Inside the ORIGINAL sync worker, before its result may be discarded
            # by repeated caller cancellation. No second executor or detached IO.
            finish_sync(attempt)


def _completion_options(base_url: str, thinking: str | None, max_tokens: int | None) -> dict[str, Any]:
    """Explicit non-thinking path; enabled thinking needs a separate history contract.

    DeepSeek defaults to thinking, but our three runtimes do not retain its CoT
    across tools. Disable it on the official host rather than silently dropping
    reasoning_content. Other OpenAI-compatible endpoints remain unchanged.
    """
    if thinking is None and urlparse(base_url).hostname == "api.deepseek.com":
        thinking = "disabled"
    if thinking not in (None, "disabled"):
        raise ValueError("only disabled thinking is supported by this history contract")
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens < 1):
        raise ValueError("max_tokens must be a positive integer")
    options: dict[str, Any] = {}
    if thinking is not None:
        options["thinking"] = {"type": thinking}
    if max_tokens is not None:
        options["max_tokens"] = max_tokens
    return options


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


class ProviderRequestError(RuntimeError):
    """Terminal provider failure with a stable machine-readable class."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        attempts: int,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.attempts = attempts
        self.status_code = status_code


class AsyncOpenAICompatibleProvider:
    """Long-lived async provider with bounded retry and a small circuit breaker."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        *,
        temperature: float | None = None,
        thinking: str | None = None,
        max_tokens: int | None = None,
        client: Any | None = None,
        max_attempts: int = 3,
        retry_budget_seconds: float = 10,
        circuit_failure_threshold: int = 5,
        circuit_reset_seconds: float = 30,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        random_source: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
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
        if temperature is not None and not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if max_attempts < 1 or retry_budget_seconds < 0 or circuit_failure_threshold < 1:
            raise ValueError("invalid provider reliability settings")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.completion_options = _completion_options(base_url, thinking, max_tokens)
        self.max_attempts = max_attempts
        self.retry_budget_seconds = retry_budget_seconds
        self.circuit_failure_threshold = circuit_failure_threshold
        self.circuit_reset_seconds = circuit_reset_seconds
        self._sleep = sleep
        self._random = random_source
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._half_open_probe: object | None = None
        self._circuit_lock = asyncio.Lock()
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

    async def _enter_circuit(self) -> object | None:
        """Return this ORIGINAL request's single half-open ownership lease.

        A stale normal completion/cleanup must never decide or release another
        request's probe. This identity isn't an execution/billing permission.
        """
        async with self._circuit_lock:
            if self._half_open_probe is not None:
                raise ProviderCircuitOpen("provider circuit is open")
            if self._failures < self.circuit_failure_threshold:
                return None
            if self._clock() < self._open_until:
                raise ProviderCircuitOpen("provider circuit is open")
            lease = object()
            self._half_open_probe = lease
            return lease

    def _can_decide_circuit(self, lease: object | None) -> bool:
        # Called ONLY while original _circuit_lock is held. Normal requests that
        # predate half-open, and retired probe leases, can't alter a new probe.
        return self._half_open_probe is lease

    async def _record_success(self, lease: object | None = None) -> None:
        async with self._circuit_lock:
            if not self._can_decide_circuit(lease):
                return
            self._failures = 0
            self._open_until = 0.0
            self._half_open_probe = None

    async def _record_failure(self, lease: object | None = None) -> None:
        async with self._circuit_lock:
            if not self._can_decide_circuit(lease):
                return
            self._failures += 1
            if self._failures >= self.circuit_failure_threshold:
                self._open_until = self._clock() + self.circuit_reset_seconds
            self._half_open_probe = None

    async def _release_probe(self, lease: object) -> None:
        async with self._circuit_lock:
            if self._half_open_probe is lease:
                self._half_open_probe = None

    async def _request_turn(self, body: dict[str, Any], headers: dict[str, str]) -> ModelTurn:
        """One ORIGINAL buffered httpx post attempt, before original retry policy.

        Numeric body observation is independent of logical parsing. Join this
        exact returned response close and original event append/notification;
        nothing here estimates usage or knows hidden wire/redirect retries.
        """
        import httpx

        attempt = await begin_async("httpx_post")
        response = None

        def observe_response():
            if attempt is not None:
                status = getattr(response, "status_code", None)
                attempt.http_status = status if type(status) is int and 100 <= status <= 599 else None
                attempt.response_state = "unavailable"
                try:
                    attempt.observe(response.content, status)
                except Exception:
                    # Buffered native response normally exposes bytes. An
                    # opaque/injected unreadable body isn't zero usage.
                    attempt.response_state = "unavailable"

        try:
            try:
                if attempt is not None:
                    attempt.entered = True
                response = await self._client.post(self.endpoint, json=body, headers=headers)
                observe_response()
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    raise httpx.HTTPStatusError(
                        f"retryable provider HTTP {response.status_code}",
                        request=response.request, response=response,
                    )
                response.raise_for_status()
                if len(response.content) > BODY_LIMIT:
                    if attempt is not None:
                        attempt.failure = "response_limit"
                    raise RuntimeError("provider response exceeds 4 MiB")
                if attempt is not None:
                    attempt.failure = "invalid_response"
                result = parse_model_turn(response.content)
                if attempt is not None:
                    attempt.outcome, attempt.failure = "returned", None
            except httpx.HTTPStatusError as exc:
                if response is None:
                    response = exc.response
                    observe_response()
                if attempt is not None:
                    attempt.failure = "rate_limited" if exc.response.status_code == 429 else "http_status"
                raise
            except httpx.TimeoutException:
                if attempt is not None:
                    attempt.failure = "timeout"
                raise
            except httpx.NetworkError:
                if attempt is not None:
                    attempt.failure = "connection"
                raise
            finally:
                if response is not None:
                    async def close_original_response():
                        try:
                            await response.aclose()
                        except BaseException as exc:
                            if attempt is not None:
                                attempt.outcome, attempt.failure = "failed", "response_cleanup"
                            if isinstance(exc, Exception):
                                # Not a retryable timeout/status/network failure
                                # even for unrecorded direct callers. No private
                                # close text or implicit retry-as-recovery.
                                raise RuntimeError("provider_response_cleanup_failed") from None
                            raise
                    await await_durable(close_original_response())
        except asyncio.CancelledError:
            if attempt is not None and attempt.failure != "response_cleanup" and attempt.outcome != "returned":
                attempt.outcome, attempt.failure = "cancelled", "cancelled"
            raise
        finally:
            # The observer commits its scalar prefix INSIDE retained append
            # before cancellation can discard a successful finish notification.
            await finish_async(attempt)
        return result

    async def anext_turn(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelTurn:
        native_entered(self)
        lease = await self._enter_circuit()
        try:
            return await self._anext_turn_owned(messages, tools, lease)
        finally:
            if lease is not None:
                # Covers preflight, request, retry/backoff handler, parse,
                # append/notify, cancellation and hard BaseException. Release
                # ONLY this original lease, never a subsequent probe's slot.
                await await_durable(self._release_probe(lease))

    async def _anext_turn_owned(self, messages: list[Message], tools: list[dict[str, Any]],
                                lease: object | None) -> ModelTurn:
        import httpx

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_api() for message in messages],
            "stream": False,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body.update(self.completion_options)
        if tools:
            body.update(tools=tools, tool_choice="auto")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        deadline = self._clock() + self.retry_budget_seconds
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                result = await self._request_turn(body, headers)
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
                if self._clock() + delay > deadline:
                    break
                allowance = _run_retry_allowance.get()
                if allowance is not None and not allowance.reserve(delay):
                    break
                if self._sleep is None:
                    await asyncio.sleep(delay)
                else:
                    await self._sleep(delay)
                continue
            else:
                await self._record_success(lease)
                return result
        await self._record_failure(lease)
        attempts = attempt + 1
        if isinstance(last_error, httpx.HTTPStatusError):
            status_code = last_error.response.status_code
            raise ProviderRequestError(
                f"provider HTTP {status_code}",
                kind="rate_limited" if status_code == 429 else "http_status",
                attempts=attempts,
                status_code=status_code,
            ) from last_error
        if isinstance(last_error, httpx.TimeoutException):
            raise ProviderRequestError(
                "provider timeout", kind="timeout", attempts=attempts
            ) from last_error
        if isinstance(last_error, httpx.NetworkError):
            raise ProviderRequestError(
                "provider connection failed", kind="connection", attempts=attempts
            ) from last_error
        raise ProviderRequestError(
            "provider request failed", kind="unknown", attempts=attempts
        ) from last_error

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
