"""Child SQLite admission/finalization must drain before Local Mode owner handoff."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.async_subagents import AsyncSubagentStore
from doppel_agent.runtime.base import RuntimeResult
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize('phase',['running','completed'])
@pytest.mark.parametrize('cancel_count',[1,2])
def test_child_cancel_drains_owned_status_io_before_owner_release(tmp_path,monkeypatch,phase,cancel_count):
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider())
        await service.start()
        service.runs.create('parent','parent-thread','graph',{'mode':'graph','prompt':'parent','permissions':{'delegate':True}},None)
        service.runs.update('parent','completed')
        entered,release=threading.Event(),threading.Event()
        original=service.subagents.store.update
        def update(*args,**kwargs):
            if args[1]==phase:
                entered.set()
                assert release.wait(30)
            return original(*args,**kwargs)
        monkeypatch.setattr(service.subagents.store,'update',update)
        class Runtime:
            async def run(self,request,sink):
                return RuntimeResult(request.run_id,request.thread_id,'completed','child fixture','graph')
        async def setup(record):
            return Runtime()
        monkeypatch.setattr(service,'_runtime',setup)
        closing=None
        try:
            child=await service.spawn_subagent('parent','inspect')
            cid=child['subagent_id']
            assert await asyncio.to_thread(entered.wait,30)
            assert await service.cancel_subagent('parent',cid)
            if cancel_count==2:
                await asyncio.sleep(0)
                service.subagents.scheduler._running[cid].cancel()
            await asyncio.sleep(0.1)
            assert not service.subagents.scheduler._jobs[cid].future.done()
            closing=asyncio.create_task(service.close())
            await asyncio.sleep(0)
            other=RunService(tmp_path,provider=MockProvider())
            try:
                with pytest.raises(RuntimeError,match='already owned'):
                    await other.start()
            finally:
                await other.close()
            release.set()
            final=await service.subagents.wait(cid)
            assert final['status']=='cancelled' and final['answer']==''
            await closing
            events=await service.list_events('parent')
            assert sum(e['type']=='subagent.cancelled' for e in events)==1
        finally:
            release.set()
            if closing:
                await asyncio.gather(closing,return_exceptions=True)
            await service.close()
    asyncio.run(scenario())


def test_cancelled_child_admission_drains_committed_record(tmp_path,monkeypatch):
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider())
        await service.start()
        service.runs.create('parent','thread','graph',{'prompt':'parent','permissions':{'delegate':True}},None)
        service.runs.update('parent','completed')
        entered,release=threading.Event(),threading.Event()
        original=service.subagents.store.create_bounded
        def create(*args,**kwargs):
            result=original(*args,**kwargs)
            entered.set()
            assert release.wait(30)
            return result
        monkeypatch.setattr(service.subagents.store,'create_bounded',create)
        task=asyncio.create_task(service.spawn_subagent('parent','inspect'))
        try:
            assert await asyncio.to_thread(entered.wait,30)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            children=await service.subagents.list_for_parent('parent')
            assert len(children)==1
            assert children[0]['status']=='cancelled'
            assert service.subagents.scheduler.active_count==0
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)
            await service.close()
    asyncio.run(scenario())


def test_cancelled_child_parent_scope_read_drains_before_owner_release(tmp_path, monkeypatch):
    """Fixture-only scope-read cancellation, not actual SQLite/native drain proof."""
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        service.runs.create('parent', 'thread', 'graph', {'prompt': 'parent', 'permissions': {'delegate': True}}, None)
        service.runs.update('parent', 'completed')
        entered, release = threading.Event(), threading.Event()
        original = service.runs.get

        def gated(identifier):
            if identifier == 'parent':
                entered.set()
                assert release.wait(5)
            return original(identifier)

        monkeypatch.setattr(service.runs, 'get', gated)
        operation = asyncio.create_task(service.spawn_subagent('parent', 'inspect'))
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            operation.cancel()
            for _ in range(12):
                await asyncio.sleep(0)
            operation.cancel()
            closing = asyncio.create_task(service.close())
            for _ in range(12):
                await asyncio.sleep(0)
            assert not operation.done() and not closing.done()
            assert service._owner.held and not service.cleanup_complete
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            await closing
            assert service.subagents.store.list_for_parent('parent') == []
            assert not service._owner.held and service.cleanup_complete
        finally:
            release.set()
            await asyncio.gather(operation, *([closing] if closing else []), return_exceptions=True)
            await service.close()
    asyncio.run(scenario())


def test_concurrent_child_followup_has_one_atomic_generation_claim(tmp_path,monkeypatch):
    store=AsyncSubagentStore(tmp_path/'children.sqlite3')
    store.create_bounded('child','parent','first',4)
    store.update('child','completed',answer='completed first')
    original=store.get
    stale=threading.Barrier(2)
    def get(cid):
        row=original(cid)
        if row['status']=='completed':
            stale.wait(timeout=30)
        return row
    monkeypatch.setattr(store,'get',get)
    def follow(prompt):
        try:
            return store.follow_up('child',prompt)
        except ValueError as exc:
            return exc
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(follow,prompt) for prompt in ('second A','second B')]
        results=[f.result(timeout=30) for f in futures]
    assert sum(isinstance(r,dict) for r in results)==1
    assert sum(isinstance(r,ValueError) for r in results)==1
    row=original('child')
    assert row['generation']==2
    assert row['history']==[{'prompt':'first','answer':'completed first'}]
