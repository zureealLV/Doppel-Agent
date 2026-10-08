"""C2c2g5g FIRST original settings file lifetime definitions, ALL UNRUN.

Same actual disposable streams through observed APIs, no real settings/DPAPI/key,
native/physical/billing proof. Fixture teardown is never production recovery.
"""

import asyncio
from pathlib import Path

import pytest

from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.settings import SettingsStore, SettingsPersistenceError

CONFIG = dict(provider="mock", preset="mock", name="offline", model="offline", base_url="")


def observe(monkeypatch, target, events, *, phase, opaque=False):
    original_open = Path.open
    streams = []

    class Stream:
        def __init__(self, raw):
            self.raw = raw
            self.exits = 0
            self.closes = 0

        def __enter__(self):
            events.append("original enter")
            if phase in {"enter", "enter_close"}:
                raise ValueError("PRIVATE_ENTER")
            self.raw.__enter__()
            if phase == "enter_unusable":
                return None
            return self

        def __exit__(self, *details):
            self.exits += 1
            events.append("original exit attempted")
            if phase == "close":
                raise OSError("PRIVATE_CLOSE")
            result = self.raw.__exit__(*details)
            if phase == "closed_then_throw":
                raise FileNotFoundError("PRIVATE_CLOSED_THROW")
            events.append("original exit returned")
            return result

        def close(self):
            self.closes += 1
            events.append("original setup close")
            if phase == "enter_close":
                raise OSError("PRIVATE_SETUP_CLOSE")
            self.raw.close()

        def read(self, *args):
            events.append("original read")
            if phase == "read_missing":
                raise FileNotFoundError("PRIVATE_ENTERED_MISSING")
            if phase == "read":
                raise OSError("PRIVATE_READ")
            if phase == "decode":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "PRIVATE_DECODE")
            if phase == "read_unusable":
                return None
            return self.raw.read(*args)

        def write(self, value):
            events.append("original write")
            if phase == "write":
                raise OSError("PRIVATE_WRITE")
            if phase == "short_write":
                self.raw.write(value[:1])
                return 1
            return self.raw.write(value)

        def flush(self):
            events.append("original flush")
            if phase == "flush":
                raise OSError("PRIVATE_FLUSH")
            return self.raw.flush()

        def fileno(self):
            events.append("original fileno")
            return self.raw.fileno()

    def open(path, *args, **kwargs):
        if path != target:
            return original_open(path, *args, **kwargs)
        events.append(("original open", args[0] if args else "r"))
        stream = Stream(original_open(path, *args, **kwargs))
        streams.append(stream)
        if opaque:
            raise OSError("PRIVATE_ALLOCATED_NO_RETURN")
        return stream

    monkeypatch.setattr(Path, "open", open)
    return streams


@pytest.mark.parametrize(
    "phase", ["read_missing", "read", "decode", "read_unusable", "enter", "enter_unusable"]
)
def test_actual_settings_read_failure_is_not_empty_default_and_fault_precedes_known_original_close(
    tmp_path, monkeypatch, phase
):
    path = tmp_path / "offline.json"
    path.write_text("{}", encoding="utf-8")
    events = []
    fault = ProviderReceiptFault()

    def failed():
        events.append("same owner fault")
        fault.mark_failed()

    store = SettingsStore(path, failure=failed, cleanup_failure=fault.retain_cleanup)
    streams = observe(monkeypatch, path, events, phase=phase)
    with pytest.raises(
        SettingsPersistenceError, match="^provider_settings_persistence_unavailable$"
    ) as error:
        store.public()
    assert error.value.source is store and store.failed and fault.broken
    assert not store.resource_cleanup_uncertain and not fault.cleanup_uncertain
    assert store.cleanup_uncertain is (
        phase != "decode"
    )  # Preserve old conservative IO gate, not physical claim.
    assert events.index("same owner fault") < events.index(
        "original setup close" if phase == "enter" else "original exit attempted"
    )
    assert len(streams) == 1 and streams[0].raw.closed
    with pytest.raises(SettingsPersistenceError):
        store.public()
    with pytest.raises(SettingsPersistenceError):
        store.save_profile(CONFIG)
    assert len(streams) == 1 and not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("phase", ["close", "closed_then_throw"])
@pytest.mark.parametrize("direction", ["read", "write"])
def test_actual_settings_failed_exit_keeps_same_stream_frame_owner_and_never_second_close_or_replace(
    tmp_path, monkeypatch, phase, direction
):
    path = tmp_path / "offline.json"
    path.write_text("{}", encoding="utf-8")
    before = path.read_bytes()
    events = []
    fault = ProviderReceiptFault()
    store = SettingsStore(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    target = path if direction == "read" else path.with_suffix(".tmp")
    streams = observe(monkeypatch, target, events, phase=phase)
    try:
        with pytest.raises(SettingsPersistenceError) as error:
            if direction == "read":
                store.public()
            else:
                store.save_profile(CONFIG)
        frame = next(iter(store._unresolved_files.values()))
        assert error.value.source is store and frame.stream is streams[0] and not frame.close_returned
        assert frame.open_attempted and frame.enter_returned and store.resource_cleanup_uncertain
        assert fault.cleanup_uncertain and next(iter(fault._cleanup_sources.values())) is store
        with pytest.raises(SettingsPersistenceError):
            store.check_resource_cleanup()
        with pytest.raises(SettingsPersistenceError):
            store._close_original(frame)
        with pytest.raises(SettingsPersistenceError):
            store.save_profile(CONFIG)
        assert next(iter(store._unresolved_files.values())) is frame and streams[0].exits == 1
        assert streams[0].closes == 0 and len(streams) == 1
        # Independent disposable bytes oracle, not original re-admission/reset.
        with original_fixture_open(path) as raw:
            assert raw.read() == before
    finally:
        for stream in streams:
            stream.raw.close()


# Save only the fixture's independent original API for row/bytes oracles; never
# use it to recover a production original operation or alter its failure flags.
_fixture_open = Path.open


def original_fixture_open(path):
    return _fixture_open(path, "rb")


@pytest.mark.parametrize("phase", ["write", "short_write", "flush", "fsync", "replace"])
def test_actual_original_temp_failure_fences_before_close_and_keeps_previous_source_no_retry(
    tmp_path, monkeypatch, phase
):
    import doppel_agent.settings as module

    path = tmp_path / "offline.json"
    path.write_text("{}", encoding="utf-8")
    before = path.read_bytes()
    events = []
    fault = ProviderReceiptFault()

    def failed():
        events.append("same owner fault")
        fault.mark_failed()

    store = SettingsStore(path, failure=failed, cleanup_failure=fault.retain_cleanup)
    streams = observe(monkeypatch, path.with_suffix(".tmp"), events, phase=phase)
    if phase == "fsync":

        def fsync(_fd):
            raise OSError("PRIVATE_FSYNC")

        monkeypatch.setattr(module.os, "fsync", fsync)
    if phase == "replace":

        def replace(_source, _target):
            events.append("original replace")
            raise OSError("PRIVATE_REPLACE")

        monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(SettingsPersistenceError):
        store.save_profile(CONFIG)
    assert store.failed and store.cleanup_uncertain and not store.resource_cleanup_uncertain and fault.broken
    assert (
        not fault.cleanup_uncertain and len(streams) == 1 and streams[0].exits == 1 and streams[0].raw.closed
    )
    if phase != "replace":
        assert events.index("same owner fault") < events.index("original exit attempted")
    else:
        assert (
            events.index("original exit returned")
            < events.index("original replace")
            < events.index("same owner fault")
        )
    assert path.read_bytes() == before and path.with_suffix(".tmp").exists()
    with pytest.raises(SettingsPersistenceError):
        store.save_profile(CONFIG)
    assert len(streams) == 1 and streams[0].exits == 1 and store._unresolved_files == {}


def test_actual_opaque_settings_open_keeps_same_attempt_without_invented_original_handle_exit(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.json"
    path.write_text("{}", encoding="utf-8")
    events = []
    fault = ProviderReceiptFault()
    store = SettingsStore(path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    streams = observe(monkeypatch, path, events, phase="healthy", opaque=True)
    try:
        with pytest.raises(SettingsPersistenceError):
            store.public()
        frame = next(iter(store._unresolved_files.values()))
        assert (
            frame.stream is None
            and frame.open_attempted
            and not frame.close_returned
            and store.resource_cleanup_uncertain
        )
        assert fault.cleanup_uncertain and next(iter(fault._cleanup_sources.values())) is store
        with pytest.raises(SettingsPersistenceError):
            store._close_original(frame)
        assert not frame.close_attempted  # No invented close for an unreturned opaque handle.
        with pytest.raises(SettingsPersistenceError):
            store.public()
        assert len(streams) == 1 and streams[0].exits == 0 and streams[0].closes == 0
    finally:
        for stream in streams:
            stream.raw.close()  # Only fixture has opaque external original handle.


def test_original_settings_failed_enter_close_retains_same_unentered_handle_not_exit_or_second_close(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.json"
    path.write_text("{}", encoding="utf-8")
    events = []
    store = SettingsStore(path)
    streams = observe(monkeypatch, path, events, phase="enter_close")
    try:
        with pytest.raises(SettingsPersistenceError):
            store.public()
        frame = next(iter(store._unresolved_files.values()))
        assert frame.stream is streams[0] and frame.open_attempted and not frame.enter_returned
        assert frame.close_attempted and not frame.close_returned and store.resource_cleanup_uncertain
        with pytest.raises(SettingsPersistenceError):
            store._close_original(frame)
        with pytest.raises(SettingsPersistenceError):
            store.public()
        assert streams[0].closes == 1 and streams[0].exits == 0 and len(streams) == 1
    finally:
        streams[0].raw.close()


def test_actual_missing_at_open_defaults_and_known_input_validation_keep_healthy_store(tmp_path):
    path = tmp_path / "offline.json"
    events = []
    store = SettingsStore(path, failure=lambda: events.append("fault"))
    assert store.public()["id"] == "default" and not path.exists() and not store.failed
    with pytest.raises(ValueError):
        store.save_profile({"provider": "invalid original input"})
    assert events == [] and not store.cleanup_uncertain and not store.resource_cleanup_uncertain


def test_actual_settings_post_replace_projection_failure_keeps_committed_file_without_replacing_again(
    tmp_path, monkeypatch
):
    path = tmp_path / "offline.json"
    path.write_text("{}", encoding="utf-8")
    originals = []
    original_replace = Path.replace
    original_open = Path.open
    reads = []
    store = SettingsStore(path)

    def replace(source, target):
        originals.append((source, target))
        return original_replace(source, target)

    def open(source, *args, **kwargs):
        if source == path and args[0] == "r":
            reads.append("original read")
            if len(reads) == 2:
                raise OSError("PRIVATE_POST_REPLACE_READ")
        return original_open(source, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "open", open)
    with pytest.raises(SettingsPersistenceError):
        store.save_profile(CONFIG)
    assert len(originals) == 1 and len(reads) == 2 and store.failed and store.resource_cleanup_uncertain
    with original_fixture_open(path) as raw:
        assert b'"offline"' in raw.read()
    with pytest.raises(SettingsPersistenceError):
        store.save_profile(CONFIG)
    assert len(originals) == 1 and len(reads) == 2


@pytest.mark.parametrize("stage", ["startup_read", "save"])
def test_actual_service_keeps_original_settings_stream_and_failed_close_task_with_owner_held(
    tmp_path, monkeypatch, stage
):
    from doppel_agent.runtime.service import RunService

    async def scenario():
        service = RunService(tmp_path)
        path = service.settings.path
        path.write_text("{}", encoding="utf-8")
        if stage == "save":
            await service.start()
        target = path if stage == "startup_read" else path.with_suffix(".tmp")
        streams = observe(monkeypatch, target, [], phase="close")
        try:
            if stage == "startup_read":
                await service.start()
                assert service._metadata_ready and not service._started
            else:
                with pytest.raises(SettingsPersistenceError):
                    service.settings.save_profile(CONFIG)
            fault = service._provider_receipt_fault
            assert (
                fault.broken
                and fault.cleanup_uncertain
                and next(iter(fault._cleanup_sources.values())) is service.settings
            )
            frame = next(iter(service.settings._unresolved_files.values()))
            assert frame.stream is streams[0] and service._owner.held
            with pytest.raises(RuntimeError, match="settings_cleanup_unresolved"):
                await service.close()
            closing = service._close_task
            with pytest.raises(RuntimeError, match="settings_cleanup_unresolved"):
                await service.close()
            assert service._close_task is closing and not service.cleanup_complete and service._owner.held
            assert frame.stream is streams[0] and streams[0].exits == 1
        finally:
            for stream in streams:
                stream.raw.close()
            service._owner.release()  # Explicit disposable teardown, not recovery.

    asyncio.run(scenario())


@pytest.mark.parametrize("native", [False, True])
def test_actual_native_settings_failed_stream_retained_and_standalone_next_provider_refused(
    tmp_path, monkeypatch, native
):
    from doppel_agent.web.server import JobManager

    fault = ProviderReceiptFault()
    manager = JobManager(
        tmp_path, **({"effect_admission": fault.check, "effect_failure": fault.mark_failed} if native else {})
    )
    path = manager.settings.path
    path.write_text("{}", encoding="utf-8")
    streams = observe(monkeypatch, path.with_suffix(".tmp"), [], phase="close")
    try:
        with pytest.raises(SettingsPersistenceError):
            manager.save_settings({"config": dict(CONFIG)})
        assert manager._operation_fault.is_set() and manager._pending_requests == 0
        source = next(iter(manager._unresolved_metadata_sources.values()))
        assert (
            source is manager.settings and next(iter(source._unresolved_files.values())).stream is streams[0]
        )
        with pytest.raises(RuntimeError, match="legacy_operation_evidence_unavailable"):
            manager.probe({"provider": "mock"})
        with pytest.raises(RuntimeError, match="legacy_operation_cleanup_unresolved"):
            manager.close_owned()
        assert streams[0].exits == 1 and source.resource_cleanup_uncertain
    finally:
        manager.pool.shutdown(wait=True)
        for stream in streams:
            stream.raw.close()
