"""S8 A original held-worker close join definitions, UNRUN; no native OS proof."""

import asyncio
import threading

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.reporting.queries import RunReportQueries
from doppel_agent.reporting.evidence import ReportEvidenceQueries
from doppel_agent.runtime.service import RunService


@pytest.mark.parametrize('phase', ['events', 'receipts'])
def test_cancelled_report_worker_is_joined_before_original_owner_close(tmp_path, monkeypatch, phase):
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        parent = 'a' * 32
        service.runs.create(parent, 'thread', 'graph', {'prompt': 'fixture'}, None)
        entered, release = threading.Event(), threading.Event()
        reader = RunReportQueries if phase == 'events' else ReportEvidenceQueries
        original = reader.read
        def gated(self, source, **kwargs):
            entered.set()
            assert release.wait(5)
            return original(self, source, **kwargs)
        monkeypatch.setattr(reader, 'read', gated)
        reading = asyncio.create_task(service.run_report(parent))
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            reading.cancel()
            await asyncio.sleep(0)
            reading.cancel()
            closing = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert not reading.done() and not closing.done() and service._owner.held
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await reading
            await closing
            assert not service._owner.held
            with pytest.raises(RuntimeError):
                await service.run_report(parent)
        finally:
            release.set()
            await asyncio.gather(reading, return_exceptions=True)
            await service.close()
    asyncio.run(scenario())
