"""C2c2g5g FIRST original RunStore file/iterator definitions, ALL UNRUN.

Actual disposable streams and original Core/native/factory paths, not real user
data, physical/native/billing or full durable root-child/fallback proof. Independent
fixture bytes/teardown cannot grant original source recovery or a second close.
"""

import asyncio
import json
from pathlib import Path

import pytest

from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.storage import LegacyStorageReadError, RunStore

IDENTITY = 'a' * 32
FRAME = dict(run_id=IDENTITY, sequence=1, timestamp='offline', kind='model_turn', payload={'private': 'unchanged'})
_fixture_open = Path.open


def bytes_oracle(path):
    with _fixture_open(path, 'rb') as stream:
        return stream.read()


def session(store):
    store.write_session(IDENTITY, {'run_id': IDENTITY, 'status': 'completed'})
    return store.root / 'runs' / IDENTITY / 'session.json'


def observe(monkeypatch, target, events, phase, *, opaque=False, close_gate=None):
    original_open = Path.open
    streams = []

    class Stream:
        def __init__(self, raw):
            self.raw = raw
            self.exits = self.closes = 0

        def __enter__(self):
            events.append('enter')
            if phase in {'enter', 'enter_close'}:
                raise OSError('PRIVATE_ENTER')
            self.raw.__enter__()
            if phase == 'enter_unusable':
                return None
            return self

        def __exit__(self, *details):
            self.exits += 1
            events.append('exit attempted')
            if close_gate is not None:
                entered, release = close_gate
                entered.set()
                assert release.wait(3)
            if phase in {'close', 'read_close', 'write_close'}:
                raise OSError('PRIVATE_EXIT')
            result = self.raw.__exit__(*details)
            if phase == 'closed_then_throw':
                raise FileNotFoundError('PRIVATE_ALREADY_CLOSED')
            events.append('exit returned')
            return result

        def close(self):
            self.closes += 1
            events.append('setup close')
            if phase == 'enter_close':
                raise OSError('PRIVATE_SETUP_CLOSE')
            self.raw.close()

        def read(self, *args):
            events.append('read')
            if phase == 'read_missing':
                raise FileNotFoundError('PRIVATE_ENTERED_MISSING')
            if phase in {'read', 'read_close'}:
                raise OSError('PRIVATE_READ')
            if phase == 'unusable':
                return None
            return self.raw.read(*args)

        def write(self, value):
            events.append('write')
            if phase in {'write', 'write_close'}:
                raise OSError('PRIVATE_WRITE')
            if phase == 'short_write':
                self.raw.write(value[:1])
                return 1
            return self.raw.write(value)

        def flush(self):
            events.append('flush')
            if phase == 'flush':
                raise OSError('PRIVATE_FLUSH')
            return self.raw.flush()

        def fileno(self):
            return self.raw.fileno()

    def open(source, *args, **kwargs):
        if source != target:
            return original_open(source, *args, **kwargs)
        events.append(('open', args[0]))
        stream = Stream(original_open(source, *args, **kwargs))
        streams.append(stream)
        if opaque:
            raise OSError('PRIVATE_ALLOCATED_NO_RETURN')
        return stream

    monkeypatch.setattr(Path, 'open', open)
    return streams


@pytest.mark.parametrize('phase', ['read_missing', 'read', 'unusable', 'enter', 'enter_unusable'])
def test_original_runstore_read_fault_not_missing_owner_before_known_close_no_new_source(tmp_path, monkeypatch, phase):
    events = []
    store = RunStore(tmp_path, failure=lambda: events.append('owner fault'))
    path = session(store)
    streams = observe(monkeypatch, path, events, phase)
    with pytest.raises(LegacyStorageReadError, match='^legacy_storage_evidence_unavailable$') as error:
        store.read_session(IDENTITY)
    assert error.value.source is store and store.failed and store.cleanup_uncertain
    assert not store.resource_cleanup_uncertain and streams[0].raw.closed
    assert events.index('owner fault') < events.index('setup close' if phase == 'enter' else 'exit attempted')
    for operation in (lambda: store.read_session(IDENTITY), store.list_sessions,
                      lambda: store.append_event(IDENTITY, FRAME), lambda: store.write_session(IDENTITY, {})):
        with pytest.raises(LegacyStorageReadError):
            operation()
    assert len(streams) == 1 and not path.with_suffix('.json.tmp').exists()


@pytest.mark.parametrize('phase', ['close', 'closed_then_throw', 'read_close', 'enter_close'])
def test_original_runstore_failed_exit_or_setup_close_retains_exact_source_frame_no_second_close(tmp_path, monkeypatch, phase):
    fault = ProviderReceiptFault()
    store = RunStore(tmp_path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    path = session(store)
    streams = observe(monkeypatch, path, [], phase)
    try:
        with pytest.raises(LegacyStorageReadError) as error:
            store.read_session(IDENTITY)
        frame = next(iter(store._unresolved_sources.values()))
        assert error.value.source is store and frame.resource is streams[0] and frame.open_attempted
        assert frame.close_attempted and not frame.close_returned and store.resource_cleanup_uncertain
        assert fault.cleanup_uncertain and next(iter(fault._cleanup_sources.values())) is store
        with pytest.raises(LegacyStorageReadError):
            store._close_original(frame)
        with pytest.raises(LegacyStorageReadError):
            store.check_resource_cleanup()
        with pytest.raises(LegacyStorageReadError):
            store.read_session(IDENTITY)
        assert streams[0].closes == (phase == 'enter_close') and streams[0].exits == (phase != 'enter_close')
        assert len(streams) == 1
    finally:
        streams[0].raw.close()


@pytest.mark.parametrize(('kind', 'phase'), [
    ('event', 'write'), ('event', 'short_write'), ('event', 'flush'), ('event', 'fsync'), ('event', 'close'),
    ('trace', 'write'), ('trace', 'short_write'), ('trace', 'close'),
    ('session', 'write'), ('session', 'short_write'), ('session', 'close'), ('session', 'replace'),
])
def test_original_runstore_write_failure_fences_original_owner_and_preserves_source_no_retry(tmp_path, monkeypatch, phase, kind):
    import doppel_agent.storage as module
    events = []
    fault = ProviderReceiptFault()
    def failed():
        events.append('owner fault')
        fault.mark_failed()
    store = RunStore(tmp_path, failure=failed, cleanup_failure=fault.retain_cleanup)
    path = session(store)
    before = bytes_oracle(path)
    target = path.parent / {'event': 'events.jsonl', 'trace': 'trace.jsonl', 'session': 'session.json.tmp'}[kind]
    streams = observe(monkeypatch, target, events, phase)
    # Original trace/session don't gain invented durable=True/fsync branches.
    if phase == 'fsync':
        def fsync(_fd):
            raise OSError('PRIVATE_FSYNC')
        monkeypatch.setattr(module.os, 'fsync', fsync)
    replacements = []
    if phase == 'replace':
        def replace(source, destination):
            replacements.append((source, destination))
            events.append('replace')
            raise OSError('PRIVATE_REPLACE')
        monkeypatch.setattr(Path, 'replace', replace)
    original = {**FRAME, 'kind': 'run_started'} if kind == 'trace' else FRAME
    with pytest.raises(LegacyStorageReadError) as error:
        if kind == 'session':
            store.write_session(IDENTITY, {'run_id': IDENTITY, 'status': 'failed'})
        else:
            store.append_event(IDENTITY, original, durable=True)
    try:
        assert error.value.source is store and store.failed and store.cleanup_uncertain and fault.broken
        assert len(streams) == 1 and streams[0].exits == 1 and bytes_oracle(path) == before
        assert store.resource_cleanup_uncertain is (phase == 'close')
        assert fault.cleanup_uncertain is (phase == 'close')
        if phase != 'replace':
            if phase != 'close':
                assert events.index('owner fault') < events.index('exit attempted')
        else:
            assert events.index('exit returned') < events.index('replace') < events.index('owner fault')
            assert len(replacements) == 1
        with pytest.raises(LegacyStorageReadError):
            store.append_event(IDENTITY, FRAME)
        with pytest.raises(LegacyStorageReadError):
            store.write_session(IDENTITY, {})
        assert len(streams) == 1 and streams[0].exits == 1
    finally:
        for stream in streams:
            stream.raw.close()


def test_original_runstore_opaque_allocated_stream_retained_attempt_not_invented_handle(tmp_path, monkeypatch):
    fault = ProviderReceiptFault()
    store = RunStore(tmp_path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    path = session(store)
    streams = observe(monkeypatch, path, [], 'healthy', opaque=True)
    try:
        with pytest.raises(LegacyStorageReadError):
            store.read_session(IDENTITY)
        frame = next(iter(store._unresolved_sources.values()))
        assert frame.resource is None and frame.open_attempted and not frame.close_returned
        assert next(iter(fault._cleanup_sources.values())) is store and fault.cleanup_uncertain
        with pytest.raises(LegacyStorageReadError):
            store._close_original(frame)
        assert not frame.close_attempted  # Unreturned opaque source has no known handle to close.
        with pytest.raises(LegacyStorageReadError):
            store.read_session(IDENTITY)
        assert len(streams) == 1 and streams[0].exits == streams[0].closes == 0
    finally:
        streams[0].raw.close()


@pytest.mark.parametrize('phase', ['iterate_missing', 'close', 'enter_close', 'opaque'])
def test_original_scandir_resource_retains_original_attempt_and_missing_only_at_open(tmp_path, monkeypatch, phase):
    import doppel_agent.storage as module
    fault = ProviderReceiptFault()
    events = []
    store = RunStore(tmp_path, failure=lambda: events.append('owner fault'), cleanup_failure=fault.retain_cleanup)
    session(store)
    actual_scandir = module.os.scandir
    resources = []
    class Entries:
        def __init__(self, raw):
            self.raw = raw
            self.exits = self.closes = 0
        def __enter__(self):
            if phase == 'enter_close':
                raise OSError('PRIVATE_ITERATOR_ENTER')
            self.raw.__enter__()
            return self
        def __iter__(self):
            if phase == 'iterate_missing':
                raise FileNotFoundError('PRIVATE_ENTERED_ITERATOR_MISSING')
            return iter(self.raw)
        def __exit__(self, *details):
            self.exits += 1
            events.append('exit')
            if phase == 'close':
                raise OSError('PRIVATE_ITERATOR_EXIT')
            return self.raw.__exit__(*details)
        def close(self):
            self.closes += 1
            raise OSError('PRIVATE_ITERATOR_SETUP_CLOSE')
    def scandir(path):
        resource = Entries(actual_scandir(path))
        resources.append(resource)
        if phase == 'opaque':
            raise OSError('PRIVATE_ITERATOR_ALLOCATED_NO_RETURN')
        return resource
    monkeypatch.setattr(module.os, 'scandir', scandir)
    try:
        with pytest.raises(LegacyStorageReadError) as error:
            store.list_sessions()
        assert error.value.source is store and store.failed and store.cleanup_uncertain
        if phase == 'iterate_missing':
            assert not store.resource_cleanup_uncertain and events.index('owner fault') < events.index('exit')
        else:
            frame = next(iter(store._unresolved_sources.values()))
            assert frame.kind == 'directory' and frame.resource is (None if phase == 'opaque' else resources[0])
            assert store.resource_cleanup_uncertain and next(iter(fault._cleanup_sources.values())) is store
            with pytest.raises(LegacyStorageReadError):
                store._close_original(frame)
        with pytest.raises(LegacyStorageReadError):
            store.list_sessions()
        assert len(resources) == 1 and resources[0].exits == (phase in {'close', 'iterate_missing'})
        assert resources[0].closes == (phase == 'enter_close')
    finally:
        for resource in resources:
            resource.raw.close()


def test_original_missing_at_open_and_known_invalid_id_stay_healthy_but_existing_temp_not_overwritten(tmp_path):
    store = RunStore(tmp_path / 'absent')
    assert store.read_session(IDENTITY) is None and store.list_sessions() == [] and not store.failed
    with pytest.raises(ValueError):
        store.write_session('INVALID', {})
    assert not store.failed
    path = session(store)
    temporary = path.parent / 'session.json.tmp'
    temporary.write_bytes(b'ORIGINAL_UNRESOLVED_TEMP')
    before = bytes_oracle(path)
    with pytest.raises(LegacyStorageReadError):
        store.write_session(IDENTITY, {'run_id': IDENTITY, 'status': 'failed'})
    assert bytes_oracle(temporary) == b'ORIGINAL_UNRESOLVED_TEMP' and bytes_oracle(path) == before
    assert store.failed and store.cleanup_uncertain and not store.resource_cleanup_uncertain


def test_original_runstore_payload_and_operation_sequence_preserved_without_fake_redaction(tmp_path):
    store = RunStore(tmp_path)
    store.append_operation(IDENTITY, 'run')
    store.append_event(IDENTITY, FRAME)
    store.append_operation(IDENTITY, 'run', outcome='returned')
    values = store.read_events(IDENTITY)
    assert values[1] == FRAME and values[0]['sequence'] == values[2]['sequence'] == 0
    assert json.loads(bytes_oracle(store.root / 'runs' / IDENTITY / 'events.jsonl').splitlines()[1]) == FRAME


@pytest.mark.parametrize('reviewed', [False, True])
def test_actual_original_legacy_core_file_fault_before_provider_binds_same_root_and_keeps_core(tmp_path, monkeypatch, reviewed):
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime
    calls = []
    class Provider:
        def next_turn(self, *_args):
            calls.append('forbidden provider')
            raise AssertionError('receipt failed before provider')
    async def scenario():
        fault = ProviderReceiptFault()
        runtime = create_runtime('legacy', tmp_path, Provider(), reviewed_legacy=reviewed, provider_receipt_fault=fault)
        streams = observe(monkeypatch, tmp_path / '.doppel-agent' / 'runs' / IDENTITY / 'events.jsonl', [], 'close')
        try:
            with pytest.raises(LegacyStorageReadError):
                await runtime.run(RunRequest('offline', run_id=IDENTITY, thread_id='b' * 32))
            core = next(iter(runtime._unresolved_storage_cores.values()))
            assert core.storage_persistence_failed and core.storage_cleanup_uncertain
            source = next(iter(core._unresolved_storage_sources.values()))
            assert source is core.store and next(iter(fault._cleanup_sources.values())) is source
            assert next(iter(source._unresolved_sources.values())).resource is streams[0]
            assert not calls and not core.task_persistence_failed and not runtime._unresolved_mcp_cores
            with pytest.raises(RuntimeError, match='provider_receipt_unavailable'):
                await runtime.run(RunRequest('no replacement', run_id='c' * 32, thread_id='d' * 32))
            assert len(streams) == 1 and streams[0].exits == 1
        finally:
            for stream in streams:
                stream.raw.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('native', [False, True])
def test_actual_original_manager_runstore_keeps_failed_stream_and_refuses_next_provider(tmp_path, monkeypatch, native):
    from doppel_agent.web.server import JobManager
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, **({'effect_admission': fault.check, 'effect_failure': fault.mark_failed} if native else {}))
    path = session(manager.store)
    streams = observe(monkeypatch, path, [], 'close')
    try:
        with pytest.raises(LegacyStorageReadError):
            manager.recent()
        source = next(iter(manager._unresolved_metadata_sources.values()))
        assert source is manager.store and next(iter(source._unresolved_sources.values())).resource is streams[0]
        assert manager._operation_fault.is_set() and manager._pending_requests == 0
        with pytest.raises(RuntimeError, match='legacy_operation_evidence_unavailable'):
            manager.probe({'provider': 'mock'})
        with pytest.raises(RuntimeError, match='legacy_operation_cleanup_unresolved'):
            manager.close_owned()
        assert streams[0].exits == 1 and source.resource_cleanup_uncertain
    finally:
        manager.pool.shutdown(wait=True)
        for stream in streams:
            stream.raw.close()


def test_actual_service_original_factory_file_fault_keeps_original_close_task_and_owner(tmp_path, monkeypatch):
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.service import RunService
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        # Actual service factory/root, not durable service.create/children/fallback D.
        runtime = await service._runtime({'mode': 'legacy', 'request': {'permissions': {}}, 'profile_snapshot': {}})
        streams = observe(monkeypatch, tmp_path / '.doppel-agent' / 'runs' / IDENTITY / 'events.jsonl', [], 'close')
        try:
            with pytest.raises(LegacyStorageReadError):
                await runtime.run(RunRequest('offline', run_id=IDENTITY, thread_id='b' * 32))
            assert runtime.provider_receipt_fault is service._provider_receipt_fault
            assert service._provider_receipt_fault.cleanup_uncertain
            with pytest.raises(RuntimeError, match='^owner_cleanup_unresolved$'):
                await service.close()
            original_close = service._close_task
            with pytest.raises(RuntimeError, match='^owner_cleanup_unresolved$'):
                await service.close()
            assert service._close_task is original_close and service._owner.held and not service.cleanup_complete
            assert streams[0].exits == 1
        finally:
            for stream in streams:
                stream.raw.close()
            service._owner.release()
    asyncio.run(scenario())


def test_actual_original_core_file_body_fault_precedes_gated_close_and_repeated_cancellation_keeps_worker(tmp_path, monkeypatch):
    import threading
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime
    entered, release = threading.Event(), threading.Event()
    async def scenario():
        fault = ProviderReceiptFault()
        runtime = create_runtime('legacy', tmp_path, MockProvider(), provider_receipt_fault=fault)
        streams = observe(monkeypatch, tmp_path / '.doppel-agent' / 'runs' / IDENTITY / 'events.jsonl',
                          [], 'write_close', close_gate=(entered, release))
        caller = asyncio.create_task(runtime.run(RunRequest('offline', run_id=IDENTITY, thread_id='b' * 32)))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert fault.broken and not fault.cleanup_uncertain  # SAME original close still entered.
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done()
        finally:
            release.set()
        try:
            with pytest.raises(asyncio.CancelledError):
                await caller
            source = next(iter(fault._cleanup_sources.values()))
            assert next(iter(source._unresolved_sources.values())).resource is streams[0]
            assert len(runtime._unresolved_storage_cores) == 1 and streams[0].exits == 1
            assert fault.cleanup_uncertain
        finally:
            for stream in streams:
                stream.raw.close()
    asyncio.run(scenario())


def test_actual_original_native_pool_core_separate_runstore_source_retained_not_manager_store(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import doppel_agent.web.server as module
    fault = ProviderReceiptFault()
    manager = module.JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    monkeypatch.setattr(module, 'uuid4', lambda: SimpleNamespace(hex=IDENTITY))
    streams = observe(monkeypatch, tmp_path / '.doppel-agent' / 'runs' / IDENTITY / 'trace.jsonl', [], 'close')
    try:
        accepted = manager.submit({'prompt': 'offline', 'config': {'provider': 'mock'}})
        manager.pool.shutdown(wait=True)
        core = manager._cores[accepted['run_id']]
        source = next(iter(manager._unresolved_metadata_sources.values()))
        assert source is core.store and source is not manager.store
        assert core.storage_persistence_failed and core.storage_cleanup_uncertain and not core.task_persistence_failed
        assert next(iter(source._unresolved_sources.values())).resource is streams[0]
        assert manager.status(accepted['run_id'])['status'] == 'failed' and fault.broken
        assert streams[0].exits == 1
        with pytest.raises(RuntimeError, match='legacy_operation_cleanup_unresolved'):
            manager.close_owned()
    finally:
        manager.pool.shutdown(wait=True)
        for stream in streams:
            stream.raw.close()


@pytest.mark.parametrize('boundary', ['bytes', 'rows', 'entries', 'utf8', 'nonfinite'])
def test_actual_original_bounds_and_invalid_metadata_known_close_not_resource_failure(tmp_path, monkeypatch, boundary):
    import doppel_agent.storage as module
    store = RunStore(tmp_path)
    path = session(store)
    if boundary == 'bytes':
        monkeypatch.setattr(module, 'MAX_READ_BYTES', 8)
        read = lambda: store.read_session(IDENTITY)
    elif boundary == 'rows':
        monkeypatch.setattr(module, 'MAX_EVENT_ROWS', 2)
        path = path.parent / 'events.jsonl'
        path.write_text(''.join(json.dumps({**FRAME, 'sequence': i}) + '\n' for i in (1, 2, 3)), encoding='utf-8')
        read = lambda: store.read_events(IDENTITY)
    elif boundary == 'entries':
        store.write_session('b' * 32, {'run_id': 'b' * 32, 'status': 'completed'})
        monkeypatch.setattr(module, 'MAX_HISTORY_ENTRIES', 1)
        read = store.list_sessions
    else:
        path.write_bytes(b'\xff' if boundary == 'utf8' else b'{"bad":NaN}')
        read = lambda: store.read_session(IDENTITY)
    before = bytes_oracle(path)
    with pytest.raises(LegacyStorageReadError):
        read()
    assert store.failed and not store.cleanup_uncertain and not store.resource_cleanup_uncertain
    assert store._unresolved_sources == {} and bytes_oracle(path) == before


def test_required_original_missing_events_is_metadata_unknown_not_allocated_handle(tmp_path):
    store = RunStore(tmp_path)
    session(store)
    with pytest.raises(LegacyStorageReadError) as error:
        store.read_events(IDENTITY)
    assert error.value.source is store and store.failed and not store.cleanup_uncertain
    assert not store.resource_cleanup_uncertain and not store._unresolved_sources


@pytest.mark.parametrize('operation', ['event', 'session'])
def test_original_nonfinite_write_refuses_known_publication_keeps_original_handle_close_and_temp(tmp_path, operation):
    fault = ProviderReceiptFault()
    store = RunStore(tmp_path, failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    path = session(store)
    before = bytes_oracle(path)
    with pytest.raises(LegacyStorageReadError):
        if operation == 'event':
            store.append_event(IDENTITY, {**FRAME, 'payload': {'value': float('nan')}})
        else:
            store.write_session(IDENTITY, {'run_id': IDENTITY, 'status': 'failed', 'bad': float('inf')})
    assert fault.broken and store.failed and store.cleanup_uncertain
    assert not store.resource_cleanup_uncertain and not fault.cleanup_uncertain
    assert bytes_oracle(path) == before
    if operation == 'session':
        assert bytes_oracle(path.parent / 'session.json.tmp') == b''
    else:
        assert bytes_oracle(path.parent / 'events.jsonl') == b''
