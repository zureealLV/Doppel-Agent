"""Held-owner metadata worker definitions; UNRUN, no native drain certificate."""

import asyncio
import threading

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService


def test_review_read_never_starts_a_released_owner(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        async def forbidden():
            raise AssertionError('read attempted start')
        monkeypatch.setattr(service, 'start', forbidden)
        with pytest.raises(RuntimeError, match='verification_query_owner_unavailable'):
            await service.subagent_review('parent')
        await service.close()
    asyncio.run(scenario())


def test_cancelled_snapshot_worker_is_joined_before_owner_release(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        service.runs.create('parent', 'thread', 'graph', {'prompt': 'parent', 'permissions': {'delegate': True}}, None)
        entered, release = threading.Event(), threading.Event()
        original = service.subagents.store.review_snapshot
        def gated(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args, **kwargs)
        monkeypatch.setattr(service.subagents.store, 'review_snapshot', gated)
        reading = asyncio.create_task(service.subagent_review('parent'))
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            reading.cancel()
            await asyncio.sleep(0)
            reading.cancel()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert not reading.done() and not closing.done()
            assert service._owner.held
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await reading
            await closing
            assert not service._owner.held
        finally:
            release.set()
            await asyncio.gather(reading, *([closing] if closing else []), return_exceptions=True)
            await service.close()
    asyncio.run(scenario())
