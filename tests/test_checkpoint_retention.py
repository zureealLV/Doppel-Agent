import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from doppel_agent.persistence.retention import CheckpointRetention
from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService
from doppel_agent.persistence.checkpoints import sqlite_checkpointer


async def seed_checkpoints(root, threads, filename="checkpoints.sqlite3"):
    path = root / '.doppel-agent' / filename
    async with sqlite_checkpointer(path) as saver:
        await saver.setup()
    with sqlite3.connect(path) as db:
        for thread in threads:
            for ns in ('', 'child-ns'):
                for i in range(6):
                    cid = f'{i:04}'
                    db.execute('INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id) VALUES(?,?,?)', (thread, ns, cid))
                    db.execute('INSERT INTO writes(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel) VALUES(?,?,?,?,0,?)', (thread, ns, cid, 'task', 'fixture'))
    return path


def counts(path, table):
    with sqlite3.connect(path) as db:
        return dict(db.execute(f'SELECT thread_id,COUNT(*) FROM {table} GROUP BY thread_id'))


@pytest.mark.parametrize("filename", ["checkpoints.sqlite3", "deep-checkpoints.sqlite3", "focused-fallback.sqlite3"])
def test_retention_protects_live_unknown_and_current_native_threads(tmp_path, filename):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        request = {'mode': 'graph', 'prompt': 'x', 'permissions': {}}
        for thread, status in [('pending','interrupted'),('active','running'),('leased','completed'),('obsolete','completed'),('recent','completed')]:
            service.runs.create(thread, thread, 'graph', request, None)
            service.runs.update(thread,status)
        conv = service.conversations.create('graph')
        current = conv['thread_id']
        service.runs.create('native-run',current,'graph',{**request,'conversation_id':conv['id']},None,native=True)
        service.runs.update('native-run','completed')
        service.runs.release_conversation_turn('native-run')
        with sqlite3.connect(service.runs.database) as db:
            db.execute("UPDATE runtime_runs SET updated_at=? WHERE run_id!='recent'", (old,))
            db.execute("UPDATE runtime_runs SET lease_active=1 WHERE run_id='leased'")
        path = await seed_checkpoints(tmp_path,['pending','active','leased','obsolete','recent',current,'unregistered'], filename)
        policy = CheckpointRetention(keep_per_namespace=2, obsolete_days=30)
        try:
            ledger_before = service.mcp_ledger.database.read_bytes()
            preview = policy.apply(service.state_root, dry_run=True)
            assert counts(path,'checkpoints')['obsolete'] == 12
            actual = policy.apply(service.state_root)
            assert preview == actual
            assert counts(path,'checkpoints') == {'pending':12,'active':12,'leased':12,'recent':4,current:4,'unregistered':12}
            assert counts(path,'writes') == counts(path,'checkpoints')
            assert service.runs.get('obsolete') is not None  # runtime audit/idempotency is retained
            service.conversations.delete(conv['id'])
            deleted = policy.apply(service.state_root)
            assert deleted['threads_deleted'] == 1
            assert current not in counts(path,'checkpoints')
            assert service.runs.get('native-run') is not None
            assert service.mcp_ledger.database.read_bytes() == ledger_before
        finally:
            await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('kwargs', [{'keep_per_namespace':0},{'obsolete_days':0},{'keep_per_namespace':True}])
def test_invalid_retention_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        CheckpointRetention(**kwargs)


def test_empty_workspace_does_not_create_checkpoint_files(tmp_path):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        assert CheckpointRetention().apply(service.state_root) == {'checkpoints_deleted':0,'writes_deleted':0,'threads_deleted':0}
        await service.close()
        assert not list(service.state_root.glob('*checkpoints.sqlite3'))
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_real_latest_snapshot_survives_automatic_compaction_and_restart(tmp_path, mode):
    from doppel_agent.provider import ModelTurn
    class Provider:
        def next_turn(self,messages,tools):
            users = [m.content for m in messages if m.role=='user']
            return ModelTurn('|'.join(users))
        async def anext_turn(self,messages,tools):
            return self.next_turn(messages,tools)
    async def scenario():
        service = RunService(tmp_path,provider=Provider())
        service.checkpoint_retention = CheckpointRetention(keep_per_namespace=2)
        await service.start()
        conv = service.conversations.create(mode)
        try:
            for prompt in ('one','two','three'):
                record,_=await service.create({'mode':mode,'prompt':prompt,'permissions':{},'conversation_id':conv['id']})
                await service.scheduler.wait(record['run_id'])
            assert (await service.get(record['run_id']))['answer']=='one|two|three'
            assert counts(service.state_root/('checkpoints.sqlite3' if mode=='graph' else 'deep-checkpoints.sqlite3'),'checkpoints')[conv['thread_id']] == 2
        finally:
            await service.close()
        replacement = RunService(tmp_path,provider=Provider())
        await replacement.start()
        try:
            assert replacement._started, (replacement._provider_startup_recovery,
                replacement._legacy_startup_recovery, replacement._tool_startup_recovery)
            record,_=await replacement.create({'mode':mode,'prompt':'four','permissions':{},'conversation_id':conv['id']})
            await replacement.scheduler.wait(record['run_id'])
            assert (await replacement.get(record['run_id']))['answer']=='one|two|three|four'
        finally:
            await replacement.close()
    asyncio.run(scenario())


def test_compaction_waiting_on_checkpoint_writer_does_not_block_durable_events(tmp_path, monkeypatch):
    import threading
    from contextlib import contextmanager
    import doppel_agent.persistence.retention as module
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider())
        await service.start()
        request={'mode':'graph','prompt':'terminal','permissions':{}}
        service.runs.create('terminal','thread','graph',request,None)
        service.runs.update('terminal','completed')
        path=await seed_checkpoints(tmp_path,['thread'])
        checkpoint_writer=sqlite3.connect(path)
        checkpoint_writer.execute('BEGIN IMMEDIATE')
        entered=threading.Event()
        original=module.sqlite_connection
        @contextmanager
        def connection(database):
            with original(database) as db:
                # Deliberately hold checkpoint write contention longer than the
                # unchanged runtime event-store busy timeout. Not a latency SLA.
                db.execute('PRAGMA busy_timeout=15000')
                def trace(sql):
                    if ('DELETE' in sql or (database.name=='checkpoints.sqlite3' and sql=='BEGIN IMMEDIATE')):
                        entered.set()
                db.set_trace_callback(trace)
                yield db
        monkeypatch.setattr(module,'sqlite_connection',connection)
        maintenance=asyncio.create_task(asyncio.to_thread(CheckpointRetention(keep_per_namespace=2).apply,service.state_root))
        try:
            assert await asyncio.to_thread(entered.wait,30)
            # Must commit while the other connection still holds the checkpoint
            # write lock; don't increase/waive the event-store timeout to pass.
            event=await asyncio.to_thread(service.events.append,'terminal','thread','run.cancel_requested',{})
            assert event['type']=='run.cancel_requested'
        finally:
            checkpoint_writer.rollback()
            checkpoint_writer.close()
            await maintenance
            await service.close()
    asyncio.run(scenario())


def test_real_child_followup_compacts_checkpoints_after_each_generation(tmp_path):
    async def scenario():
        service=RunService(tmp_path,provider=MockProvider())
        service.checkpoint_retention=CheckpointRetention(keep_per_namespace=2)
        await service.start()
        service.runs.create('parent','parent-thread','graph',{'prompt':'parent','mode':'graph','permissions':{'delegate':True}},None)
        service.runs.update('parent','completed')
        try:
            child=await service.spawn_subagent('parent','first')
            cid=child['subagent_id']
            assert (await service.subagents.wait(cid))['status']=='completed'
            for prompt in ('second','third'):
                await service.follow_up_subagent('parent',cid,prompt)
                assert (await service.subagents.wait(cid))['status']=='completed'
            assert counts(service.state_root/'checkpoints.sqlite3','checkpoints')[cid]==2
            assert (await service.subagents.get(cid))['generation']==3
        finally:
            await service.close()
    asyncio.run(scenario())
