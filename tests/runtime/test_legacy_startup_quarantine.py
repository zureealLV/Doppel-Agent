"""C2c2c FIRST original Legacy read/service startup definitions, ALL UNRUN."""

import asyncio
import threading
from functools import wraps

import pytest

from doppel_agent.provider import MockProvider
from doppel_agent.runtime.provider_recording import ProviderReceiptError
from doppel_agent.runtime.service import RunService
from doppel_agent.storage import RunStore


def _async_test(function):
    """Execute the original async body without installing a pytest plugin."""

    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


@_async_test
@pytest.mark.parametrize("source", ["new_probe_intent", "historical_open_core"])
async def test_original_legacy_unknown_quarantines_before_any_runtime_pump(tmp_path, source):
    service = RunService(tmp_path, provider=MockProvider())
    store = RunStore(service.state_root)
    identity = "b" * 32
    if source == "new_probe_intent":
        store.append_operation(identity, "probe")
    else:
        store.append_event(
            identity, {"run_id": identity, "kind": "run_started", "payload": {"prompt": "PRIVATE"}}
        )
    try:
        await service.start()
        assert service._metadata_ready and not service._started and service._owner.held
        assert service._legacy_startup_recovery.quarantined
        with pytest.raises(ProviderReceiptError):
            service._assert_provider_admission()
        original = service._legacy_startup_recovery
        await service.start()
        assert service._legacy_startup_recovery is original
    finally:
        await service.close()
    recreated = RunService(tmp_path, provider=MockProvider())
    try:
        await recreated.start()
        assert recreated._legacy_startup_recovery.quarantined and not recreated._started
    finally:
        await recreated.close()


@_async_test
async def test_healthy_original_operation_closure_keeps_original_execution_start(tmp_path):
    service = RunService(tmp_path, provider=MockProvider())
    store = RunStore(service.state_root)
    identity = "c" * 32
    store.append_operation(identity, "probe")
    store.append_operation(identity, "probe", outcome="returned")
    try:
        await service.start()
        assert service._started and not service._legacy_startup_recovery.quarantined
        assert service._legacy_startup_recovery.operation_rows == 2
    finally:
        await service.close()


@_async_test
async def test_close_during_original_legacy_read_joins_same_worker_before_late_start(tmp_path, monkeypatch):
    from doppel_agent.persistence.legacy_recovery import LegacyOperationRecoveryQueries

    entered, release = threading.Event(), threading.Event()
    read = LegacyOperationRecoveryQueries.read

    def original_held_read(reader):
        entered.set()
        release.wait()
        return read(reader)

    monkeypatch.setattr(LegacyOperationRecoveryQueries, "read", original_held_read)
    service = RunService(tmp_path, provider=MockProvider())
    close = None
    start = asyncio.create_task(service.start())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        close = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        assert service._owner.held and not service._started and not start.done() and not close.done()
    finally:
        release.set()
        try:
            await start
        except RuntimeError as error:
            assert str(error) == "RunService is closed"
        if close is not None:
            await close
        else:
            await service.close()
    assert service.cleanup_complete and not service._owner.held
