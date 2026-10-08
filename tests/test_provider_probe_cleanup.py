"""Half-open ownership is released on backoff cancellation and invalid payloads."""

import asyncio

import httpx
import pytest

from doppel_agent.provider import AsyncOpenAICompatibleProvider, Message, ProviderRequestError


@pytest.mark.parametrize('fault', ['cancel_backoff', 'invalid_response'])
def test_aborted_half_open_probe_does_not_permanently_block_profile(fault):
    async def scenario():
        now, calls = [100.0], []
        sleeping = asyncio.Event()

        async def handler(request):
            calls.append(True)
            if len(calls) == 1 or (len(calls) == 2 and fault == 'cancel_backoff'):
                return httpx.Response(503, headers={'Retry-After': '.1'}, request=request)
            if len(calls) == 2:
                return httpx.Response(200, json={'choices': []}, request=request)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'recovered'}}]}, request=request)

        async def sleep(delay):
            sleeping.set()
            await asyncio.Event().wait()

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider(
            'https://fixture.invalid/v1', 'fixture', client=client,
            max_attempts=1, circuit_failure_threshold=1, circuit_reset_seconds=5,
            clock=lambda: now[0], sleep=sleep,
        )
        try:
            with pytest.raises(ProviderRequestError):
                await provider.anext_turn([Message('user', 'trip')], [])
            now[0] += 6
            if fault == 'cancel_backoff':
                provider.max_attempts = 2
                probe = asyncio.create_task(provider.anext_turn([Message('user', 'probe')], []))
                await asyncio.wait_for(sleeping.wait(), 2)
                probe.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await probe
            else:
                with pytest.raises(RuntimeError, match='invalid provider response'):
                    await provider.anext_turn([Message('user', 'probe')], [])
            assert (await provider.anext_turn([Message('user', 'next probe')], [])).content == 'recovered'
            assert len(calls) == 3
        finally:
            await client.aclose()
    asyncio.run(scenario())
