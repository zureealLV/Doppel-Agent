"""S9 fake managed-process text wait drainage; not native Job/tree proof."""

import asyncio
import threading
from types import SimpleNamespace

from doppel_agent.workspace.process_supervisor import ProcessSupervisor


def test_repeated_cancel_keeps_original_wait_worker_and_registration_until_join(tmp_path, monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    terminated, closed = [], []

    def wait():
        started.set()
        assert release.wait(5)
        finished.set()
        return 0

    managed = SimpleNamespace(process=SimpleNamespace(wait=wait, returncode=0), supervision="fixture_not_native")
    managed.terminate_tree = lambda: terminated.append(True)

    def close():
        assert finished.is_set(), "do not close managed resources before original waiter joins"
        closed.append(True)

    managed.close = close
    supervisor = ProcessSupervisor()
    monkeypatch.setattr(supervisor, "_start", lambda *args, **kwargs: managed)

    async def scenario():
        task = asyncio.create_task(supervisor.run(["fixture-not-executed"], cwd=tmp_path, run_id="fixture"))
        try:
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and supervisor.active_count == 1 and not closed
            release.set()
            assert isinstance((await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError)
            assert supervisor.active_count == 0 and terminated and closed
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
