"""Run-scoped retries and owned sync provider workers; all HTTP is MockTransport."""

import asyncio
import json
import threading

import httpx
import pytest

from doppel_agent.provider import (
    AsyncOpenAICompatibleProvider, Message, ModelTurn, ProviderRequestError, next_model_turn,
)
from doppel_agent.runtime.service import RunService
from doppel_agent.runtime.graph import GraphRuntime
from doppel_agent.runtime.deep import DeepAgentRuntime
from doppel_agent.runtime.base import RunRequest


@pytest.mark.parametrize('operation', ['create', 'resume', 'child', 'concurrent'])
def test_retry_allowance_is_shared_across_turns_and_fresh_per_segment(tmp_path, monkeypatch, operation):
    async def scenario():
        calls, delays = {}, []

        async def handler(request):
            key = json.loads(request.content)['messages'][0]['content']
            calls[key] = calls.get(key, 0) + 1
            if calls[key] % 2:
                return httpx.Response(503, headers={'Retry-After': '0.1'}, request=request)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'fixture'}}]}, request=request)

        async def sleep(delay):
            delays.append(delay)
            await asyncio.sleep(0)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider(
            'https://fixture.invalid/v1', 'mock-http', client=client, sleep=sleep,
            circuit_failure_threshold=10,
        )
        service = RunService(tmp_path, provider=provider)
        await service.start()

        class Runtime:
            async def run(self, request, sink):
                for _ in range(3):
                    await provider.anext_turn([Message('user', request.run_id)], [])
                raise AssertionError('aggregate run retry allowance was reset for each turn')

            async def resume(self, command, sink):
                await self.run(command, sink)

        async def runtime(record):
            return Runtime()

        monkeypatch.setattr(service, '_runtime', runtime)
        request = {'mode': 'graph', 'prompt': 'retries', 'permissions': {'delegate': True}}
        try:
            if operation == 'child':
                service.runs.create('parent', 'parent-thread', 'graph', request, None)
                service.runs.update('parent', 'completed')
                record = await service.spawn_subagent('parent', 'child')
                result = await service.subagents.wait(record['subagent_id'])
                assert result['status'] == 'failed'
                assert result['error'].startswith('ProviderRequestError:')
            else:
                if operation == 'resume':
                    service.runs.create('resume', 'resume-thread', 'graph', request, None)
                    service.runs.update('resume', 'interrupted', metadata={'interrupts': [{'id': 'approval', 'value': {}}]})
                    records = [await service.resume('resume', 'approval', {'action': 'approve'})]
                elif operation == 'concurrent':
                    records = [r for r, _ in await asyncio.gather(service.create(request), service.create(request))]
                else:
                    records = [(await service.create(request))[0]]
                for record in records:
                    with pytest.raises(ProviderRequestError):
                        await service.scheduler.wait(record['run_id'])
                    assert (await service.get(record['run_id']))['status'] == 'failed'
            assert sorted(calls.values()) == ([5, 5] if operation == 'concurrent' else [5])
            assert len(delays) == (4 if operation == 'concurrent' else 2)
        finally:
            await service.close()
            await client.aclose()
    asyncio.run(scenario())


def test_cumulative_run_backoff_cannot_reset_each_turn(tmp_path, monkeypatch):
    async def scenario():
        calls, delays = [], []

        async def handler(request):
            calls.append('http')
            if len(calls) % 2:
                return httpx.Response(429, headers={'Retry-After': '6'}, request=request)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'fixture'}}]}, request=request)

        async def sleep(delay):
            delays.append(delay)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'mock-http', client=client, sleep=sleep)
        service = RunService(tmp_path, provider=provider)
        await service.start()

        class Runtime:
            async def run(self, request, sink):
                for _ in range(2):
                    await provider.anext_turn([Message('user', 'fixture')], [])
                raise AssertionError('cumulative retry delay exceeded the segment allowance')

        async def runtime(record):
            return Runtime()

        monkeypatch.setattr(service, '_runtime', runtime)
        try:
            record, _ = await service.create({'mode': 'graph', 'prompt': 'retry', 'permissions': {}})
            with pytest.raises(ProviderRequestError):
                await service.scheduler.wait(record['run_id'])
            assert len(calls) == 3
            assert delays == [6]
        finally:
            await service.close()
            await client.aclose()
    asyncio.run(scenario())


def test_sync_provider_bridge_drains_actual_worker_on_repeated_cancel():
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        completed = []

        class Provider:
            def next_turn(self, *args):
                entered.set()
                assert release.wait(10)
                completed.append(True)
                return ModelTurn('late actual completion')

        task = asyncio.create_task(next_model_turn(Provider(), [Message('user', 'fixture')], []))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(.03)
            task.cancel()
            await asyncio.sleep(.03)
            assert not task.done(), 'actual provider worker was abandoned'
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert completed == [True]
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['graph', 'deep'])
def test_direct_runtime_has_same_aggregate_retry_scope(tmp_path, monkeypatch, mode):
    async def scenario():
        from types import SimpleNamespace
        calls, delays = [], []

        async def handler(request):
            calls.append(True)
            if len(calls) % 2 or json.loads(request.content)['messages'][0]['content'] == 'fallback':
                return httpx.Response(503, headers={'Retry-After': '0.1'}, request=request)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'fixture'}}]}, request=request)

        async def sleep(delay):
            delays.append(delay)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = AsyncOpenAICompatibleProvider('https://fixture.invalid/v1', 'fixture', client=client, sleep=sleep)

        async def invoke(*args, **kwargs):
            for _ in range(3):
                await provider.anext_turn([Message('user', 'fixture')], [])
            raise AssertionError('direct runtime reset the run budget every turn')

        class Graph:
            async def aget_state(self, config):
                return SimpleNamespace(next=[], values={})

            ainvoke = staticmethod(invoke)

        if mode == 'graph':
            runtime = GraphRuntime(tmp_path, provider)
            monkeypatch.setattr(runtime, '_build_graph', lambda *args: Graph())
        else:
            runtime = DeepAgentRuntime(tmp_path, provider)
            monkeypatch.setattr(runtime, '_invoke', invoke)

            class FallbackGraph(Graph):
                @staticmethod
                async def ainvoke(*args, **kwargs):
                    await provider.anext_turn([Message('user', 'fallback')], [])
                    raise AssertionError('fallback fixture unexpectedly succeeded')

            monkeypatch.setattr(runtime._fallback, '_build_graph', lambda *args: FallbackGraph())
        try:
            with pytest.raises(ProviderRequestError):
                await runtime.run(RunRequest('fixture'))
            assert len(calls) == (5 if mode == 'graph' else 6)
            assert delays == [.1, .1]
        finally:
            await client.aclose()
    asyncio.run(scenario())
