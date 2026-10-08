"""Total service-segment deadlines and lifetime ownership, never paid providers."""
import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider, ModelTurn
from doppel_agent.runtime.base import RuntimeResult
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize('operation',['create','resume'])
@pytest.mark.parametrize('phase',['setup','workspace_lock'])
def test_execution_deadline_includes_setup_and_workspace_wait(tmp_path,monkeypatch,operation,phase):
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider())
        await service.start()
        release=asyncio.Event()
        calls=[]
        class Runtime:
            async def run(self,request,sink):
                calls.append('runtime')
                return RuntimeResult(request.run_id,request.thread_id,'completed','fixture','graph')
            resume=run
        async def setup(record):
            if phase=='setup':
                await release.wait()
            return Runtime()
        monkeypatch.setattr(service,'_runtime',setup)
        writer=service.workspace_locks.write(tmp_path)
        if phase=='workspace_lock':
            await writer.__aenter__()
        try:
            request={'mode':'graph','prompt':'deadline','permissions':{},'deadline_seconds':0.05}
            if operation=='create':
                record,_=await service.create(request)
            else:
                record,_=service.runs.create('resume','thread','graph',request,None)
                service.runs.update('resume','interrupted',metadata={'interrupts':[{'id':'decision','value':{}}]})
                await service.resume('resume','decision',{'action':'approve'})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(service.scheduler.wait(record['run_id']),2)
            result=await service.get(record['run_id'])
            assert result['status']=='failed', result
            assert result['error'].startswith('TimeoutError:')
            assert not calls
            events=await service.list_events(record['run_id'])
            assert sum(e['type']=='run.failed' for e in events)==1
            if result['conversation_id']:
                detail=service.conversations.get(result['conversation_id'])
                assert detail['active_run_id'] is None
                assert [m['role'] for m in detail['messages']]==['user']
        finally:
            release.set()
            if phase=='workspace_lock':
                await writer.__aexit__(None,None,None)
            await service.close()
    asyncio.run(scenario())


def test_expired_queued_segment_never_runs_provider_or_tools(tmp_path,monkeypatch):
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider(),max_active_runs=1)
        await service.start()
        entered,release=asyncio.Event(),asyncio.Event()
        calls=[]
        class Runtime:
            async def run(self,request,sink):
                calls.append(request.prompt)
                if request.prompt=='holder':
                    entered.set()
                    await release.wait()
                return RuntimeResult(request.run_id,request.thread_id,'completed','fixture','graph')
        async def setup(record):
            return Runtime()
        monkeypatch.setattr(service,'_runtime',setup)
        try:
            first,_=await service.create({'mode':'graph','prompt':'holder','permissions':{},'deadline_seconds':10})
            await entered.wait()
            queued,_=await service.create({'mode':'graph','prompt':'expired','permissions':{},'deadline_seconds':0.05})
            await asyncio.sleep(0.1)  # deliberately beyond this requested budget, not a latency SLA
            release.set()
            await service.scheduler.wait(first['run_id'])
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(service.scheduler.wait(queued['run_id']),2)
            assert (await service.get(queued['run_id']))['status']=='failed'
            assert calls==['holder']
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())


def test_legacy_cancel_drains_core_worker_before_scheduler_and_owner_close(tmp_path):
    entered,release=threading.Event(),threading.Event()
    class Provider:
        def next_turn(self,*args):
            entered.set()
            assert release.wait(30)
            return ModelTurn('late Core answer')
    async def scenario():
        service=RunService(tmp_path,provider=Provider())
        await service.start()
        try:
            record,_=await service.create({'mode':'legacy','prompt':'blocked Core','permissions':{}})
            assert await asyncio.to_thread(entered.wait,30)
            assert await service.cancel(record['run_id'])
            # Observe for a finite fixture window while the Core worker is still
            # held. A cancelled wrapper must not report its worker as drained.
            await asyncio.sleep(0.1)
            assert not service.scheduler._jobs[record['run_id']].future.done()
            closing=asyncio.create_task(service.close())
            await asyncio.sleep(0)
            other=RunService(tmp_path,provider=MockProvider())
            try:
                with pytest.raises(RuntimeError,match='already owned'):
                    await other.start()
            finally:
                await other.close()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await service.scheduler.wait(record['run_id'])
            await closing
            assert (await service.get(record['run_id']))['status']=='cancelled'
            assert [m['role'] for m in service.conversations.get(record['conversation_id'])['messages']]==['user']
        finally:
            release.set()
            await service.close()
    asyncio.run(scenario())


def test_child_execution_has_parent_configured_budget(tmp_path,monkeypatch):
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider())
        await service.start()
        request={'mode':'graph','prompt':'parent','permissions':{'delegate':True},'deadline_seconds':0.05}
        service.runs.create('parent','parent-thread','graph',request,None)
        service.runs.update('parent','completed')
        class Runtime:
            async def run(self,*args):
                await asyncio.Event().wait()
        async def setup(record):
            return Runtime()
        monkeypatch.setattr(service,'_runtime',setup)
        try:
            record=await service.spawn_subagent('parent','bounded child')
            child=await asyncio.wait_for(service.subagents.wait(record['subagent_id']),2)
            assert child['status']=='failed'
            assert child['error'].startswith('TimeoutError:')
        finally:
            await service.close()
    asyncio.run(scenario())


def test_subagent_failure_stores_class_not_raw_secret_exception(tmp_path):
    from doppel_agent.runtime.async_subagents import AsyncSubagentManager
    async def scenario():
        events=[]
        class Sink:
            async def emit(self,kind,**payload):
                events.append((kind,payload))
        async def runner(request):
            raise ValueError('DO_NOT_STORE_TRANSPORT_AUTH_SENTINEL')
        manager=AsyncSubagentManager(tmp_path/'children.sqlite3',runner,sink=Sink())
        try:
            record=await manager.spawn('parent','inspect')
            result=await manager.wait(record['subagent_id'])
            assert result['status']=='failed'
            assert result['error'].startswith('ValueError:')
            assert 'DO_NOT_STORE' not in str(result)
            assert 'DO_NOT_STORE' not in str(events)
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_real_graph_api_deadline_has_no_assistant_success_projection(tmp_path):
    import time
    class Provider:
        async def anext_turn(self,*args):
            await asyncio.Event().wait()
    app=create_app(tmp_path,provider=Provider())
    with TestClient(app,base_url='http://127.0.0.1') as client:
        record=client.post('/api/v1/runs',json={'mode':'graph','prompt':'deadline','deadline_seconds':1}).json()
        stop=time.monotonic()+30
        while time.monotonic()<stop:
            result=client.get('/api/v1/runs/'+record['run_id']).json()
            if result['status'] in {'completed','failed','cancelled'}:
                break
            time.sleep(0.01)
        assert result['status']=='failed'
        assert result['error'].startswith('TimeoutError:')
        detail=client.get('/api/v1/conversations/'+record['conversation_id']).json()
        assert [m['role'] for m in detail['messages']]==['user']
