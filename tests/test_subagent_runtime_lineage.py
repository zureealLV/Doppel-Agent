"""B2b lineage/drain definitions, UNRUN. Scripted runtime, not actual Graph/native QA."""

import asyncio
import threading
from dataclasses import FrozenInstanceError

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.base import RuntimeResult
from doppel_agent.runtime.service import ChildRuntimeSink, RunService


def test_child_runtime_envelope_does_not_allow_payload_to_override_original_lineage():
    async def scenario():
        seen = []
        class ParentSink:
            async def emit(self, kind, **payload):
                seen.append((kind, payload))
        sink = ChildRuntimeSink(ParentSink(), 'parent', 'child', 7)
        with pytest.raises(FrozenInstanceError):
            sink.generation = 999
        await sink.emit('run.completed', parent_run_id='spoof', subagent_id='spoof', generation=999, answer='text')
        assert seen == [('subagent.runtime', {
            'parent_run_id': 'parent', 'subagent_id': 'child', 'generation': 7,
            'runtime_kind': 'run.completed', 'runtime_payload': {
                'parent_run_id': 'spoof', 'subagent_id': 'spoof', 'generation': 999, 'answer': 'text',
            },
        })]
    asyncio.run(scenario())


def test_child_runtime_sink_joins_entire_original_notification_when_repeatedly_cancelled():
    """A gated sink callback definition, not an OS/socket cleanup proof."""
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        class ParentSink:
            async def emit(self, kind, **payload):
                seen.append('appended')
                entered.set()
                await release.wait()
                seen.append('notified')
        sink = ChildRuntimeSink(ParentSink(), 'parent', 'child', 1)
        operation = asyncio.create_task(sink.emit('tool.finished'))
        try:
            await entered.wait()
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
            assert not operation.done() and seen == ['appended']
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert seen == ['appended', 'notified']
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
    asyncio.run(scenario())


def test_original_manager_two_generations_route_tool_events_to_original_parent(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        parent = 'a' * 32
        service.runs.create(parent, 'parent-thread', 'deep', {'prompt': 'parent', 'permissions': {'delegate': True}}, None)
        service.runs.update(parent, 'completed', answer='parent answer')
        histories = []
        class Runtime:
            async def run(self, request, sink):
                histories.append(request.history)
                for kind in ('runtime.started', 'tool.started', 'tool.finished', 'runtime.finished', 'run.completed'):
                    await sink.emit(kind, run_id=request.run_id, tool_call_id='scripted-call', generation=999)
                return RuntimeResult(request.run_id, request.thread_id, 'completed', 'child answer', 'graph')
        async def setup(record):
            assert record['mode'] == 'graph'
            assert not any(record['request']['permissions'].values())
            return Runtime()
        monkeypatch.setattr(service, '_runtime', setup)
        try:
            first = await service.spawn_subagent(parent, 'inspect')
            cid = first['subagent_id']
            await service.subagents.wait(cid)
            await service.follow_up_subagent(parent, cid, 'follow up', expected_generation=1)
            await service.subagents.wait(cid)
            events = await service.list_events(parent)
            runtime = [event for event in events if event['type'] == 'subagent.runtime']
            assert len(runtime) == 10
            assert [event['payload']['generation'] for event in runtime] == [1] * 5 + [2] * 5
            assert all(event['run_id'] == parent and event['thread_id'] == 'parent-thread' for event in runtime)
            assert all(event['payload']['parent_run_id'] == parent and event['payload']['subagent_id'] == cid for event in runtime)
            assert all(event['payload']['runtime_payload']['generation'] == 999 for event in runtime)
            assert not any(event['type'] == 'run.completed' for event in events)
            assert service.runs.get(cid) is None  # no forged independently registered run
            assert service.runs.get(parent)['answer'] == 'parent answer'
            assert histories[0] == () and [item.content for item in histories[1]] == ['inspect', 'child answer']
        finally:
            await service.close()
    asyncio.run(scenario())


def test_cancelled_original_child_event_append_is_joined_before_owner_release(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        parent = 'a' * 32
        service.runs.create(parent, 'thread', 'graph', {'prompt': 'parent', 'permissions': {'delegate': True}}, None)
        entered, release = threading.Event(), threading.Event()
        original = service.events.append
        def gated(run, thread, kind, payload):
            if kind == 'subagent.runtime':
                entered.set()
                assert release.wait(5)
            return original(run, thread, kind, payload)
        monkeypatch.setattr(service.events, 'append', gated)
        class Runtime:
            async def run(self, request, sink):
                await sink.emit('tool.finished', tool_call_id='scripted-call')
                return RuntimeResult(request.run_id, request.thread_id, 'completed', 'answer', 'graph')
        async def setup(_record):
            return Runtime()
        monkeypatch.setattr(service, '_runtime', setup)
        closing = None
        try:
            accepted = await service.spawn_subagent(parent, 'inspect')
            cid = accepted['subagent_id']
            assert await asyncio.to_thread(entered.wait, 2)
            assert await service.cancel_subagent(parent, cid, expected_generation=1)
            service.subagents.scheduler._running[cid].cancel()  # repeated cancellation definition
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert not closing.done() and service._owner.held
            release.set()
            await closing
            events = service.events.list(parent)
            assert sum(event['type'] == 'subagent.runtime' for event in events) == 1
            assert sum(event['type'] == 'subagent.cancelled' for event in events) == 1
            assert not service._owner.held
        finally:
            release.set()
            if closing:
                await asyncio.gather(closing, return_exceptions=True)
            await service.close()
    asyncio.run(scenario())
