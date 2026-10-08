import asyncio
import os
import signal
import subprocess
import sys
import threading

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService


def test_second_owner_cannot_recover_live_records(tmp_path):
    async def scenario():
        first = RunService(tmp_path, provider=MockProvider())
        second = RunService(tmp_path, provider=MockProvider())
        await first.start()
        request = {"prompt": "live", "mode": "graph", "permissions": {}}
        first.runs.create("live", "thread", "graph", request, None)
        first.runs.update("live", "running")
        try:
            with pytest.raises(RuntimeError, match="already owned"):
                await second.start()
            assert first.runs.get("live")["status"] == "running"
            # A service that never acquired ownership must not mutate via public APIs.
            with pytest.raises(RuntimeError, match="already owned|closed"):
                await second.cancel("live")
        finally:
            await second.close()
            await first.close()
        third = RunService(tmp_path, provider=MockProvider())
        await third.start()
        assert third.runs.get("live")["status"] == "failed"
        await third.close()
        with pytest.raises(RuntimeError, match="closed"):
            await third.create(request)
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['cancel_request', 'failure_finalization'])
def test_repeated_cancel_drains_terminal_mutation_before_owner_handoff(tmp_path, monkeypatch, phase):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        entered, release = threading.Event(), threading.Event()
        request = {'mode': 'graph', 'prompt': 'fixture', 'permissions': {}}
        original_update = service.runs.update

        if phase == 'cancel_request':
            service.runs.create('fixture', 'thread', 'graph', request, None)
            service.runs.update('fixture', 'interrupted')
            original = service.runs.request_cancel

            def request_cancel(*args):
                entered.set()
                assert release.wait(30)
                return original(*args)

            monkeypatch.setattr(service.runs, 'request_cancel', request_cancel)
            task = asyncio.create_task(service.cancel('fixture'))
            run_id = 'fixture'
        else:
            async def runtime(record):
                raise ValueError('fixture setup failure')

            def update(*args, **kwargs):
                if args[1] == 'failed':
                    entered.set()
                    assert release.wait(30)
                return original_update(*args, **kwargs)

            monkeypatch.setattr(service, '_runtime', runtime)
            monkeypatch.setattr(service.runs, 'update', update)
            record, _ = await service.create(request)
            run_id = record['run_id']
            assert await asyncio.to_thread(entered.wait, 30)
            task = service.scheduler._running[run_id]
        closing = None
        other = RunService(tmp_path, provider=MockProvider())
        try:
            assert await asyncio.to_thread(entered.wait, 30)
            task.cancel()
            await asyncio.sleep(.03)
            task.cancel()
            await asyncio.sleep(.03)
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(.03)
            assert not task.done(), 'owned SQLite mutation was abandoned'
            assert not closing.done(), 'workspace owner released during SQLite mutation'
            with pytest.raises(RuntimeError, match='already owned'):
                await other.start()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if closing is not None:
                await closing
            else:
                await service.close()
            await other.start()
            assert (await other.get(run_id))['status'] == ('cancelled' if phase == 'cancel_request' else 'failed')
            await other.close()
    asyncio.run(scenario())


def test_lock_is_cross_process_and_released_after_abrupt_exit(tmp_path):
    from doppel_agent.persistence.ownership import WorkspaceOwner
    path = tmp_path / "owner.lock"
    code = "from pathlib import Path; from doppel_agent.persistence.ownership import WorkspaceOwner; import sys; owner=WorkspaceOwner(Path(sys.argv[1])); owner.acquire(); print(__import__('os').getpid(), flush=True); sys.stdin.read()"
    child = subprocess.Popen([sys.executable, "-u", "-c", code, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        owned_pid = int(child.stdout.readline().strip())
        owner = WorkspaceOwner(path)
        with pytest.raises(RuntimeError, match="already owned"):
            owner.acquire()
        os.kill(owned_pid, signal.SIGTERM)
        child.wait(timeout=15)
        owner.acquire()
        owner.release()
    finally:
        if child.poll() is None:
            os.kill(owned_pid, signal.SIGTERM)
        child.communicate(timeout=15)


def test_startup_cancellation_drains_recovery_before_releasing_owner(tmp_path, monkeypatch):
    async def scenario():
        first = RunService(tmp_path, provider=MockProvider())
        entered, release = threading.Event(), threading.Event()
        def recovery():
            entered.set()
            assert release.wait(30)
        monkeypatch.setattr(first.runs, "recover_incomplete", recovery)
        task = asyncio.create_task(first.start())
        assert await asyncio.to_thread(entered.wait, 30)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        second = RunService(tmp_path, provider=MockProvider())
        try:
            with pytest.raises(RuntimeError, match="already owned"):
                await second.start()
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await first.close()
            await second.close()
        third = RunService(tmp_path, provider=MockProvider())
        await third.start()
        await third.close()
    asyncio.run(scenario())


def test_cancelled_close_holds_owner_until_resources_finish(tmp_path, monkeypatch):
    async def scenario():
        first = RunService(tmp_path, provider=MockProvider())
        await first.start()
        entered, release = asyncio.Event(), asyncio.Event()
        original = first.process_supervisor.close
        async def close():
            entered.set()
            await release.wait()
            await original()
        monkeypatch.setattr(first.process_supervisor, "close", close)
        task = asyncio.create_task(first.close())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        other = RunService(tmp_path, provider=MockProvider())
        try:
            with pytest.raises(RuntimeError, match="already owned"):
                await other.start()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await first.close()
        finally:
            release.set()
            await other.close()
        replacement = RunService(tmp_path, provider=MockProvider())
        await asyncio.gather(replacement.start(), replacement.start())
        await asyncio.gather(replacement.close(), replacement.close())
    asyncio.run(scenario())


def test_startup_error_releases_owner_after_cleanup(tmp_path, monkeypatch):
    async def scenario():
        first=RunService(tmp_path,provider=MockProvider())
        def recover():
            raise ValueError('fixture recovery failure')
        monkeypatch.setattr(first.runs,'recover_incomplete',recover)
        with pytest.raises(ValueError,match='fixture recovery failure'):
            await first.start()
        await first.close()
        other=RunService(tmp_path,provider=MockProvider())
        await other.start()
        await other.close()
    asyncio.run(scenario())


def test_close_waits_for_inflight_acceptance_before_owner_handoff(tmp_path, monkeypatch):
    async def scenario():
        first=RunService(tmp_path,provider=MockProvider())
        await first.start()
        entered,release=threading.Event(),threading.Event()
        original=first.runs.create
        def create(*args,**kwargs):
            entered.set()
            assert release.wait(30)
            return original(*args,**kwargs)
        monkeypatch.setattr(first.runs,'create',create)
        request={'mode':'graph','prompt':'acceptance','permissions':{}}
        accepting=asyncio.create_task(first.create(request))
        assert await asyncio.to_thread(entered.wait,30)
        closing=asyncio.create_task(first.close())
        await asyncio.sleep(0)
        other=RunService(tmp_path,provider=MockProvider())
        try:
            with pytest.raises(RuntimeError,match='already owned'):
                await other.start()
            with pytest.raises(RuntimeError,match='closed'):
                await first.create(request)
            assert not closing.done()
            release.set()
            record,_=await accepting
            await closing
            await other.start()
            assert (await other.get(record['run_id']))['status'] in {'completed','cancelled','failed'}
        finally:
            release.set()
            await asyncio.gather(accepting,closing,return_exceptions=True)
            await other.close()
    asyncio.run(scenario())
