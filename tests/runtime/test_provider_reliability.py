import asyncio
import unittest

import httpx

from doppel_agent.provider import (
    AsyncOpenAICompatibleProvider,
    Message,
    ProviderCircuitOpen,
)


class AsyncProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_429_and_respects_retry_after(self):
        attempts = 0
        delays = []

        async def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "0.01"}, request=request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
                request=request,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider(
            "https://provider.example/v1",
            "model",
            client=client,
            sleep=lambda delay: self._record_delay(delays, delay),
        )
        result = await provider.anext_turn([Message("user", "hello")], [])
        self.assertEqual(result.content, "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(delays, [0.01])
        await client.aclose()

    async def test_circuit_opens_after_repeated_failures(self):
        async def handler(request):
            return httpx.Response(503, request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider(
            "https://provider.example/v1",
            "model",
            client=client,
            max_attempts=1,
            circuit_failure_threshold=2,
        )
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "provider HTTP 503"):
                await provider.anext_turn([Message("user", "hello")], [])
        with self.assertRaises(ProviderCircuitOpen):
            await provider.anext_turn([Message("user", "hello")], [])
        await client.aclose()

    async def test_cancellation_is_not_retried(self):
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.sleep(30)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider("https://provider.example/v1", "model", client=client)
        task = asyncio.create_task(provider.anext_turn([Message("user", "hello")], []))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await client.aclose()

    @staticmethod
    async def _record_delay(delays, delay):
        delays.append(delay)
