"""S7 generation-pinned child actions. Scripted/gated fixtures, not native QA."""

import asyncio
import threading

import pytest

from doppel_agent.runtime.async_subagents import AsyncSubagentManager, AsyncSubagentStore


def test_store_followup_compares_generation_inside_original_transaction(tmp_path):
    store = AsyncSubagentStore(tmp_path / 'children.sqlite3')
    store.create_bounded('child', 'parent', 'first', 4)
    store.update('child', 'completed', answer='first answer')
    second = store.follow_up('child', 'second', expected_generation=1)
    assert second['generation'] == 2
    store.update('child', 'completed', answer='second answer')
    before = store.get('child')
    with pytest.raises(ValueError, match='subagent_generation_changed'):
        store.follow_up('child', 'stale second', expected_generation=1)
    assert store.get('child') == before
    third = store.follow_up('child', 'third', expected_generation=2)
    assert third['history'] == [
        {'prompt': 'first', 'answer': 'first answer'},
        {'prompt': 'second', 'answer': 'second answer'},
    ]
    assert third['parent_run_id'] == 'parent' and third['generation'] == 3


@pytest.mark.parametrize('generation', [True, False, 0, -1, 1.0, '1'])
def test_store_rejects_non_strict_generation_without_changing_record(tmp_path, generation):
    store = AsyncSubagentStore(tmp_path / 'children.sqlite3')
    store.create_bounded('child', 'parent', 'first', 4)
    store.update('child', 'completed', answer='answer')
    before = store.get('child')
    with pytest.raises(ValueError, match='invalid_subagent_generation'):
        store.follow_up('child', 'new', expected_generation=generation)
    assert store.get('child') == before


def test_stale_repeated_followup_and_cancel_never_target_next_generation(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        requests, events = [], []

        class Sink:
            async def emit(self, kind, **payload):
                events.append((kind, payload))

        async def runner(request):
            requests.append(request)
            if request.generation == 2:
                started.set()
                await release.wait()
            return 'fixture answer'

        manager = AsyncSubagentManager(tmp_path / 'children.sqlite3', runner, sink=Sink())
        try:
            first = await manager.spawn('parent', 'first')
            identifier = first['subagent_id']
            await manager.wait(identifier)
            second = await manager.follow_up(identifier, 'second', expected_generation=1)
            await asyncio.wait_for(started.wait(), 2)
            with pytest.raises(ValueError, match='subagent_generation_changed'):
                await manager.follow_up(identifier, 'stale followup', expected_generation=1)
            with pytest.raises(ValueError, match='subagent_generation_changed'):
                await manager.cancel(identifier, expected_generation=1)
            assert second['generation'] == 2 and manager.scheduler.status(identifier) == 'running'
            assert manager.scheduler.accepted_count == 2
            assert await manager.cancel(identifier, expected_generation=2)
            final = await manager.wait(identifier)
            assert final['status'] == 'cancelled' and final['generation'] == 2
            assert final['parent_run_id'] == 'parent' and final['prompt'] == 'second'
            assert [request.generation for request in requests] == [1, 2]
            assert all(request.permissions == frozenset({'workspace_read'}) and not request.allow_delegate for request in requests)
            terminal = [(kind, payload) for kind, payload in events if kind in {'subagent.completed', 'subagent.cancelled'}]
            assert [(kind, payload['generation']) for kind, payload in terminal] == [
                ('subagent.completed', 1), ('subagent.cancelled', 2),
            ]
        finally:
            release.set()
            await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_generation', [1, 2])
def test_cancel_serializes_with_generation_claim_and_scheduler_admission(tmp_path, monkeypatch, cancel_generation):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        child_release = asyncio.Event()

        async def runner(request):
            if request.generation == 2:
                await child_release.wait()
            return 'answer'

        manager = AsyncSubagentManager(tmp_path / 'children.sqlite3', runner)
        first = await manager.spawn('parent', 'first')
        identifier = first['subagent_id']
        await manager.wait(identifier)
        original = manager.store.follow_up

        def gated(*args, **kwargs):
            result = original(*args, **kwargs)  # generation 2 queued, not yet submitted
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(manager.store, 'follow_up', gated)
        following = asyncio.create_task(manager.follow_up(identifier, 'second', expected_generation=1))
        cancelling = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            cancelling = asyncio.create_task(manager.cancel(identifier, expected_generation=cancel_generation))
            for _ in range(12):
                await asyncio.sleep(0)
            assert not cancelling.done()
            release.set()
            accepted = await following
            assert accepted['generation'] == 2
            if cancel_generation == 1:
                with pytest.raises(ValueError, match='subagent_generation_changed'):
                    await cancelling
                assert manager.scheduler.status(identifier) in {'queued', 'running'}
                assert await manager.cancel(identifier, expected_generation=2)
            else:
                assert await cancelling
            final = await manager.wait(identifier)
            assert final['generation'] == 2 and final['status'] == 'cancelled'
        finally:
            release.set()
            child_release.set()
            await asyncio.gather(following, *([cancelling] if cancelling else []), return_exceptions=True)
            await manager.close()
    asyncio.run(scenario())


def test_reviewed_followup_does_not_wait_for_running_generation_or_block_its_cancel(tmp_path):
    async def scenario():
        started = asyncio.Event()

        async def runner(_request):
            started.set()
            await asyncio.Event().wait()
            return 'unreachable'

        manager = AsyncSubagentManager(tmp_path / 'children.sqlite3', runner)
        try:
            first = await manager.spawn('parent', 'first')
            await asyncio.wait_for(started.wait(), 2)
            with pytest.raises(ValueError, match='follow-up requires a completed subagent'):
                await asyncio.wait_for(manager.follow_up(first['subagent_id'], 'too early', expected_generation=1), 2)
            assert await manager.cancel(first['subagent_id'], expected_generation=1)
            assert (await manager.wait(first['subagent_id']))['status'] == 'cancelled'
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_wait_for_prior_generation_drain_does_not_hold_cancel_lock(tmp_path):
    async def scenario():
        persisted, release = asyncio.Event(), asyncio.Event()
        events = []

        class Sink:
            async def emit(self, kind, **payload):
                events.append((kind, payload))
                if kind == 'subagent.completed':
                    persisted.set()
                    await release.wait()

        async def runner(_request):
            return 'first answer'

        manager = AsyncSubagentManager(tmp_path / 'children.sqlite3', runner, sink=Sink())
        following = None
        try:
            first = await manager.spawn('parent', 'first')
            identifier = first['subagent_id']
            await asyncio.wait_for(persisted.wait(), 2)
            following = asyncio.create_task(manager.follow_up(identifier, 'second', expected_generation=1))
            for _ in range(12):
                await asyncio.sleep(0)
            assert not following.done()
            assert await asyncio.wait_for(manager.cancel(identifier, expected_generation=1), 2)
            release.set()
            with pytest.raises(ValueError, match='follow-up requires a completed subagent'):
                await following
            assert (await manager.get(identifier))['generation'] == 1
            assert manager.scheduler.accepted_count == 1
        finally:
            release.set()
            if following:
                await asyncio.gather(following, return_exceptions=True)
            await manager.close()
    asyncio.run(scenario())


def test_competing_reviewed_followups_capture_old_future_not_new_generation_job(tmp_path, monkeypatch):
    async def scenario():
        completed, release_completion, captured = asyncio.Event(), asyncio.Event(), asyncio.Event()
        child_release = asyncio.Event()
        observed = 0

        class Sink:
            async def emit(self, kind, **payload):
                if kind == 'subagent.completed' and payload['generation'] == 1:
                    completed.set()
                    await release_completion.wait()

        async def runner(request):
            if request.generation == 2:
                await child_release.wait()
            return 'answer'

        manager = AsyncSubagentManager(tmp_path / 'children.sqlite3', runner, sink=Sink())
        following = []
        try:
            first = await manager.spawn('parent', 'first')
            identifier = first['subagent_id']
            await asyncio.wait_for(completed.wait(), 2)
            original = manager.get

            async def observed_get(*args, **kwargs):
                nonlocal observed
                record = await original(*args, **kwargs)
                if record and record['generation'] == 1 and record['status'] == 'completed':
                    observed += 1
                    if observed == 2:
                        captured.set()
                return record

            monkeypatch.setattr(manager, 'get', observed_get)
            following = [asyncio.create_task(manager.follow_up(identifier, prompt, expected_generation=1)) for prompt in ('second A', 'second B')]
            await asyncio.wait_for(captured.wait(), 2)
            release_completion.set()
            results = await asyncio.wait_for(asyncio.gather(*following, return_exceptions=True), 2)
            assert sum(isinstance(result, dict) for result in results) == 1
            errors = [result for result in results if isinstance(result, ValueError)]
            assert len(errors) == 1 and str(errors[0]) == 'subagent_generation_changed'
            assert manager.scheduler.accepted_count == 2
            assert (await manager.get(identifier))['generation'] == 2
            assert await manager.cancel(identifier, expected_generation=2)
            await manager.wait(identifier)
        finally:
            release_completion.set()
            child_release.set()
            await asyncio.gather(*following, return_exceptions=True)
            await manager.close()
    asyncio.run(scenario())


def test_cancelled_followup_admission_drains_without_recursive_child_lock(tmp_path):
    async def scenario():
        admitted, release = asyncio.Event(), asyncio.Event()

        class Sink:
            async def emit(self, kind, **_payload):
                if kind == 'subagent.followed_up':
                    admitted.set()
                    await release.wait()

        async def runner(request):
            if request.generation == 2:
                await asyncio.Event().wait()
            return 'first answer'

        manager = AsyncSubagentManager(tmp_path / 'children.sqlite3', runner, sink=Sink())
        following = None
        try:
            first = await manager.spawn('parent', 'first')
            identifier = first['subagent_id']
            await manager.wait(identifier)
            following = asyncio.create_task(manager.follow_up(identifier, 'second', expected_generation=1))
            await asyncio.wait_for(admitted.wait(), 2)
            following.cancel()
            for _ in range(12):
                await asyncio.sleep(0)
            following.cancel()
            assert not following.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(following, 2)
            final = await manager.wait(identifier)
            assert final['generation'] == 2 and final['status'] == 'cancelled'
            assert await asyncio.wait_for(manager.cancel(identifier, expected_generation=2), 2) is False
        finally:
            release.set()
            if following:
                await asyncio.gather(following, return_exceptions=True)
            await manager.close()
    asyncio.run(scenario())
