"""Deferred regression for owned navigation IO; no GUI or paid provider."""

import asyncio
import threading

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.service import RunService


def test_cancelled_selection_worker_drains_before_owner_handoff(tmp_path, monkeypatch):
    async def scenario():
        first = RunService(tmp_path, provider=MockProvider())
        second = RunService(tmp_path, provider=MockProvider())
        await first.start()
        cid = first.conversations.create()["id"]
        entered, release = threading.Event(), threading.Event()
        original = first.conversations.set_selection

        def blocked(*args):
            entered.set()
            assert release.wait(10), "bounded selection worker was not released"
            return original(*args)

        monkeypatch.setattr(first.conversations, "set_selection", blocked)
        task = asyncio.create_task(first.save_workspace_selection(cid))
        close = None
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            close = asyncio.create_task(first.close())
            await asyncio.sleep(0)
            assert not close.done()
            with pytest.raises(RuntimeError, match="already owned"):
                await second.start()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=10)
            if close is not None:
                await asyncio.wait_for(close, timeout=10)
            else:
                await first.close()
            await second.close()
        third = RunService(tmp_path, provider=MockProvider())
        try:
            await third.start()
            assert await third.workspace_selection() == {"saved": True, "conversation_id": cid, "run_id": None}
        finally:
            await third.close()
        with pytest.raises(RuntimeError, match="closed"):
            await first.save_workspace_selection(cid)

    asyncio.run(scenario())
