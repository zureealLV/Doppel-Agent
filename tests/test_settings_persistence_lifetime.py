"""Remaining C FIRST original settings/approval definitions, ALL UNRUN.

Disposable metadata and offline callbacks only; no real DPAPI/key/native proof.
"""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from doppel_agent.settings import SettingsStore, SettingsPersistenceError
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.runtime.service import RunService
from doppel_agent.web.server import JobManager


CONFIG = dict(provider="mock", preset="mock", name="offline", model="offline", base_url="")


@pytest.mark.parametrize(
    "raw",
    [
        "broken PRIVATE",
        "[]",
        '{"profiles": []}',
        '{"profiles": [{"id":"default"}], "active_profile_id":"missing"}',
        '{"profiles": [{"id":"default", "input_price":"PRIVATE"}]}',
        '{"profiles": [{"id":"default", "input_price":NaN}]}',
        '{"profiles": [{"id":"default"}], "profiles": []}',
        '{"profiles": [{"id":"same"}, {"id":"same"}]}',
    ],
)
def test_corrupt_original_settings_never_become_defaults_or_overwritten(tmp_path, raw):
    path = tmp_path / "provider-settings.json"
    path.write_text(raw, encoding="utf-8")
    calls = []
    store = SettingsStore(path, failure=lambda: calls.append("original fault"))
    for operation in (
        store.public,
        lambda: store.save_profile(CONFIG),
        lambda: store.delete_profile("default"),
    ):
        with pytest.raises(
            SettingsPersistenceError, match="^provider_settings_persistence_unavailable$"
        ) as failure:
            operation()
        assert "PRIVATE" not in str(failure.value) and path.read_text(encoding="utf-8") == raw
    assert calls and not store.cleanup_uncertain


def test_missing_original_settings_still_has_offline_default_without_io_or_fault(tmp_path):
    path = tmp_path / "missing.json"
    calls = []
    store = SettingsStore(path, failure=lambda: calls.append("fault"))
    assert store.public()["id"] == "default" and calls == [] and not path.exists()


def test_original_settings_fsync_failure_keeps_previous_file_and_fixed_unknown(tmp_path, monkeypatch):
    import doppel_agent.settings as module

    path = tmp_path / "provider-settings.json"
    store = SettingsStore(path)
    store.save_profile(CONFIG)
    before = path.read_bytes()

    def unavailable(fd):
        raise ValueError("PRIVATE_CLOSED_HANDLE_OR_KEY")

    monkeypatch.setattr(module.os, "fsync", unavailable)
    with pytest.raises(SettingsPersistenceError) as failure:
        store.save_profile(dict(CONFIG, name="changed"))
    assert str(failure.value) == "provider_settings_persistence_unavailable"
    assert store.cleanup_uncertain and path.read_bytes() == before


def test_original_temporary_source_is_not_truncated_by_recreated_save(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save_profile(CONFIG)
    temporary = store.path.with_suffix(".tmp")
    temporary.write_text("PRIVATE_UNSETTLED", encoding="utf-8")
    before = store.path.read_bytes()
    recreated = SettingsStore(store.path)
    with pytest.raises(SettingsPersistenceError):
        recreated.save_profile(dict(CONFIG, name="replacement"))
    assert store.path.read_bytes() == before
    assert temporary.read_text(encoding="utf-8") == "PRIVATE_UNSETTLED"
    assert recreated.cleanup_uncertain


def test_recreated_service_keeps_unsettled_original_settings_temp_as_metadata_quarantine(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        temporary = service.settings.path.with_suffix(".tmp")
        temporary.write_text("PRIVATE_UNSETTLED", encoding="utf-8")
        try:
            await service.start()
            assert service._metadata_ready and not service._started and service._owner.held
            assert service._provider_receipt_fault.broken and service._close_task is None
            assert temporary.read_text(encoding="utf-8") == "PRIVATE_UNSETTLED"
        finally:
            await service.close()
        assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_same_original_store_serializes_entire_read_modify_write_not_only_replace(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    entered, release, extra_read = threading.Event(), threading.Event(), threading.Event()
    original_read, original_write = store._normalized, store._write
    reads = []

    def read():
        reads.append("original read")
        if len(reads) > 1:
            extra_read.set()
        return original_read()

    def write(data):
        if not entered.is_set():
            entered.set()
            release.wait()
        return original_write(data)

    monkeypatch.setattr(store, "_normalized", read)
    monkeypatch.setattr(store, "_write", write)
    with ThreadPoolExecutor(max_workers=2) as fixture:
        first = fixture.submit(store.save_profile, CONFIG, profile_id="first offline fixture")
        second = None
        try:
            assert entered.wait(2)
            second = fixture.submit(
                store.save_profile, dict(CONFIG, name="second"), profile_id="second offline fixture"
            )
            assert not extra_read.wait(0.05)  # Observation, not replacement/deadline.
        finally:
            release.set()
        first.result(timeout=3)
        assert second is not None
        second.result(timeout=3)
    assert len(store.public()["profiles"]) == 3


def test_native_original_settings_failure_marks_same_service_fault_and_retains_cleanup_owner(
    tmp_path, monkeypatch
):
    import doppel_agent.settings as module

    async def scenario():
        service = RunService(tmp_path)
        await service.start()

        def unavailable(fd):
            raise OSError("PRIVATE_FSYNC_FAILURE")

        monkeypatch.setattr(module.os, "fsync", unavailable)
        with pytest.raises(SettingsPersistenceError):
            service.settings.save_profile(CONFIG)
        assert service._provider_receipt_fault.broken and service.settings.cleanup_uncertain
        with pytest.raises(RuntimeError, match="settings_cleanup_unresolved"):
            await service.close()
        assert not service.cleanup_complete and service._owner.held
        # Disposable failed-close fixture only, NOT production retry/cleanup.
        service._owner.release()

    asyncio.run(scenario())


def test_corrupt_settings_startup_is_metadata_quarantine_not_failed_close_or_model_io(tmp_path):
    async def scenario():
        service = RunService(tmp_path)
        service.settings.path.write_text("PRIVATE_CORRUPT", encoding="utf-8")
        try:
            await service.start()
            assert service._metadata_ready and not service._started and service._owner.held
            assert service._provider_receipt_fault.broken and service._close_task is None
        finally:
            await service.close()
        assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_original_settings_startup_read_retains_owner_until_same_reader_and_close_join(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        entered, release = threading.Event(), threading.Event()
        original_read = service.settings.recovery_public

        def read():
            entered.set()
            release.wait()
            return original_read()

        monkeypatch.setattr(service.settings, "recovery_public", read)
        start = asyncio.create_task(service.start())
        close = None
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            close = asyncio.create_task(service.close())
            await asyncio.sleep(0)
            assert service._owner.held and not close.done() and not service._started
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="RunService is closed"):
            await start
        assert close is not None
        await close
        assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_original_settings_startup_late_fault_cannot_start_pumps(tmp_path, monkeypatch):
    async def scenario():
        service = RunService(tmp_path)
        original_read = service.settings.recovery_public

        def read():
            metadata = original_read()
            service._provider_receipt_fault.mark_failed()
            return metadata

        monkeypatch.setattr(service.settings, "recovery_public", read)
        try:
            await service.start()
            assert service._metadata_ready and not service._started and service._owner.held
            assert service._provider_receipt_fault.broken and service._close_task is None
        finally:
            await service.close()
        assert service.cleanup_complete and not service._owner.held

    asyncio.run(scenario())


def test_native_manager_settings_write_failure_stays_unknown_before_new_key_lookup(tmp_path, monkeypatch):
    import doppel_agent.settings as module

    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)

    def unavailable(fd):
        raise OSError("PRIVATE_FSYNC_FAILURE")

    monkeypatch.setattr(module.os, "fsync", unavailable)
    with pytest.raises(SettingsPersistenceError):
        manager.save_settings({"config": dict(CONFIG)})
    assert fault.broken and manager._pending_requests == 0
    with pytest.raises(RuntimeError, match="legacy_operation_evidence_unavailable"):
        manager.probe({"profile_id": "forbidden lookup"})
    with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
        manager.close_owned()


def test_original_core_rechecks_after_approval_wait_before_write_effect(tmp_path):
    from doppel_agent.core import Core
    from doppel_agent.provider import ModelTurn, ToolCall

    fault = ProviderReceiptFault()
    decisions = []

    class Provider:
        def next_turn(self, messages, tools):
            return ModelTurn(
                "", (ToolCall("fixture", "write_file", {"path": "effect.txt", "content": "offline"}),)
            )

    def approve(*args):
        decisions.append("original reviewed decision")
        fault.mark_failed()
        return True

    result = Core(tmp_path, Provider(), allow_write=True, approver=approve, check_cancelled=fault.check).run(
        "offline"
    )
    assert decisions == ["original reviewed decision"] and result["status"] == "failed"
    assert not (tmp_path / "effect.txt").exists()


def test_original_broker_denial_keeps_path_but_new_allow_refused_under_fault(tmp_path):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    from doppel_agent.web.approvals import ApprovalBroker

    broker = ApprovalBroker()
    broker.pending["fixture"] = {"id": "fixture"}
    manager.brokers["original"] = broker
    fault.mark_failed()
    try:
        with pytest.raises(RuntimeError):
            manager.decide("original", "fixture", True)
        assert broker.decisions == {}
        assert manager.decide("original", "fixture", False)
        assert broker.decisions == {"fixture": False} and manager._pending_requests == 0
    finally:
        manager.close_owned()


def test_delete_keeps_original_request_lease_and_guard_before_original_store(tmp_path, monkeypatch):
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check)
    calls = []

    def delete(identity):
        calls.append((identity, manager._pending_requests))
        return {"fixture": True}

    monkeypatch.setattr(manager.settings, "delete_profile", delete)
    try:
        assert manager.delete_settings("original") == {"fixture": True}
        assert calls == [("original", 1)] and manager._pending_requests == 0
        fault.mark_failed()
        with pytest.raises(RuntimeError):
            manager.delete_settings("original")
        assert calls == [("original", 1)] and manager._pending_requests == 0
    finally:
        manager.close_owned()


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/settings", None),
        ("POST", "/api/settings", {"config": CONFIG}),
        ("POST", "/api/settings/profiles/default/delete", {}),
    ],
)
def test_original_console_settings_routes_fixed_failure_without_raw_defaults(tmp_path, method, path, body):
    from io import BytesIO
    from types import SimpleNamespace
    from doppel_agent.web.server import ConsoleHandler

    manager = JobManager(tmp_path)
    manager.settings.path.write_text("PRIVATE_CORRUPT", encoding="utf-8")
    handler = object.__new__(ConsoleHandler)
    raw = json.dumps(body).encode() if body is not None else b""
    handler.server = SimpleNamespace(manager=manager, server_port=8766)
    handler.path = path
    handler.headers = {
        "Content-Length": str(len(raw)),
        "Content-Type": "application/json",
        "Host": "127.0.0.1:8766",
        "X-Doppel-UI": "1",
    }
    handler.rfile = BytesIO(raw)
    replies = []
    handler._json = lambda status, value: replies.append((status, value))
    try:
        if method == "GET":
            handler.do_GET()
        else:
            handler.do_POST()
        assert replies == [(503, {"error": "provider_settings_persistence_unavailable"})]
        assert manager._pending_requests == 0
        assert manager.settings.path.read_text(encoding="utf-8") == "PRIVATE_CORRUPT"
    finally:
        manager.close_owned()


def test_original_api_exact_settings_exception_no_store_fixed_503_without_lifespan_io(tmp_path):
    import httpx
    from doppel_agent.api.app import create_app

    async def scenario():
        app = create_app(tmp_path)

        @app.get("/fixture-settings-failure")
        async def original_failure():
            raise SettingsPersistenceError()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.get("/fixture-settings-failure")
        assert response.status_code == 503
        assert response.json() == {"error": "provider_settings_persistence_unavailable"}
        assert response.headers["Cache-Control"] == "no-store"
        service = app.state.run_service
        assert not service._started and not service._owner.held
        await service.close()

    asyncio.run(scenario())
