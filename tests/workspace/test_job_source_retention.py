"""Remaining C2c2h FIRST original Job/source definitions, ALL UNRUN.

Original _WindowsJob/_start/supervisor/service methods with observed local ctypes
API proxy. No real Win32 handle, Popen, process/group/job accounting, native or
physical proof. Fake method returns never establish real tree drain.
"""

import asyncio
import ctypes
import io
from types import SimpleNamespace

import pytest

import doppel_agent.workspace.process_supervisor as module
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


def observe_api(monkeypatch, events, *, setup='healthy', close='healthy', create='healthy'):
    class Function:
        def __init__(self, name):
            self.name = name
            self.argtypes = self.restype = None
        def __call__(self, *args):
            events.append((self.name, args[0] if args else None))
            if self.name == 'CreateJobObjectW':
                if create == 'opaque':
                    raise OSError('PRIVATE_CREATED_NO_RETURN')
                if create == 'unusable':
                    return 'PRIVATE_UNUSABLE_HANDLE'
                return 0 if create == 'refused' else 987654
            if self.name == 'SetInformationJobObject':
                if setup == 'set_throw':
                    raise OSError('PRIVATE_SET_THROW')
                if setup == 'set_unusable':
                    return 'PRIVATE_UNUSABLE_SETUP'
                return 0 if setup == 'set' else 1
            if self.name == 'AssignProcessToJobObject':
                if setup == 'assign_throw':
                    raise OSError('PRIVATE_ASSIGN_THROW')
                if setup == 'assign_unusable':
                    return 'PRIVATE_UNUSABLE_ASSIGNMENT'
                return 0 if setup == 'assign' else 1
            if self.name == 'CloseHandle':
                if close in {'throw', 'closed_then_throw'}:
                    raise OSError('PRIVATE_CLOSE_UNKNOWN')
                return 0 if close == 'false' else 1
            return 1
    kernel = SimpleNamespace(**{name: Function(name) for name in (
        'CreateJobObjectW', 'SetInformationJobObject', 'AssignProcessToJobObject',
        'TerminateJobObject', 'QueryInformationJobObject', 'CloseHandle')})
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    return kernel


@pytest.mark.parametrize('setup', ['set', 'assign', 'set_throw', 'assign_throw'])
@pytest.mark.parametrize('close', ['false', 'throw', 'closed_then_throw'])
def test_original_job_constructor_failed_setup_close_retains_same_unpublished_handle_attempt_no_second_close(monkeypatch, setup, close):
    events = []
    kernel = observe_api(monkeypatch, events, setup=setup, close=close)
    with pytest.raises(module.ProcessCleanupError, match='^job handle cleanup unproved$') as error:
        module._WindowsJob(12345)
    source = error.value.source
    assert source._kernel32 is kernel and source._handle == 987654 and source.cleanup_uncertain
    assert source._create_attempted and source._create_returned and source._close_attempted and not source._close_returned
    before = list(events)
    for operation in (source.close, source.close_checked, source.terminate, source.terminate_checked, source.wait_empty):
        with pytest.raises(module.ProcessCleanupError):
            operation()
    assert events == before and str(error.value) == 'job handle cleanup unproved'


@pytest.mark.parametrize('setup', ['set', 'assign', 'set_throw', 'assign_throw', 'set_unusable', 'assign_unusable'])
def test_original_job_known_setup_refusal_same_handle_closed_once_not_unknown_cleanup(monkeypatch, setup):
    events = []
    observe_api(monkeypatch, events, setup=setup)
    with pytest.raises(module.ProcessSupervisionError, match='^job setup unavailable$') as error:
        module._WindowsJob(12345)
    source = error.value.source
    assert source._handle is None and source._close_returned and not source.cleanup_uncertain
    source.close()
    source.close_checked()
    assert len([value for value in events if value[0] == 'CloseHandle']) == 1


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('close', ['false', 'throw', 'closed_then_throw'])
def test_original_published_job_close_unknown_keeps_same_identity_no_second_api_or_other_job_operations(monkeypatch, strict, close):
    events = []
    observe_api(monkeypatch, events, close=close)
    source = module._WindowsJob(12345)
    operation = source.close_checked if strict else source.close
    with pytest.raises(module.ProcessCleanupError) as error:
        operation()
    assert error.value.source is source and source._handle == 987654 and source.cleanup_uncertain
    before = list(events)
    for operation in (source.close, source.close_checked, source.terminate, source.terminate_checked, source.wait_empty):
        with pytest.raises(module.ProcessCleanupError):
            operation()
    assert events == before


def test_original_job_opaque_create_attempt_kept_no_invented_handle_or_close(monkeypatch):
    events = []
    kernel = observe_api(monkeypatch, events, create='opaque')
    with pytest.raises(module.ProcessCleanupError, match='^job allocation unproved$') as error:
        module._WindowsJob(12345)
    source = error.value.source
    assert source._kernel32 is kernel and source._handle is None and source._create_attempted
    assert not source._create_returned and source.cleanup_uncertain and not source._close_attempted
    before = list(events)
    with pytest.raises(module.ProcessCleanupError):
        source.close_checked()
    assert events == before and not source._close_attempted


def test_original_job_known_allocation_refusal_not_resource_unknown_or_fake_close(monkeypatch):
    events = []
    observe_api(monkeypatch, events, create='refused')
    with pytest.raises(module.ProcessSupervisionError, match='^job allocation unavailable$') as error:
        module._WindowsJob(12345)
    source = error.value.source
    assert source._handle is None and source._create_returned and not source.cleanup_uncertain
    assert not source._close_attempted and all(value[0] != 'CloseHandle' for value in events)


def observe_process(monkeypatch, events):
    class Process:
        pid = 12345
        _handle = 12345
        returncode = -1
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        def kill(self):
            events.append(('kill original process', self))
        def wait(self):
            events.append(('wait original process', self))
            return -1
    process = Process()
    def popen(*args, **kwargs):
        events.append(('original Popen', process))
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setattr(module.os, 'name', 'nt')
    def forbidden(*args, **kwargs):
        raise AssertionError('new resume/fallback/job forbidden after original job source failure')
    monkeypatch.setattr(module, '_resume_suspended_primary_thread', forbidden)
    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    return process


@pytest.mark.parametrize('strict', [False, True])
def test_original_static_start_keeps_same_unpublished_job_in_original_managed_not_fallback(tmp_path, monkeypatch, strict):
    root = tmp_path.resolve()
    events = []
    observe_api(monkeypatch, events, setup='assign', close='false')
    process = observe_process(monkeypatch, events)
    try:
        with pytest.raises(module.ProcessCleanupError) as error:
            module.ProcessSupervisor._start(['offline fixture'], root, {}, module.subprocess.PIPE,
                                            module.subprocess.PIPE, require_tree_ownership=strict)
        managed = error.value.managed
        source = error.value.source
        assert managed.process is process and managed.job is source
        assert source.cleanup_uncertain and source._handle == 987654
        assert managed.supervision == 'unavailable'  # Unknown setup, not a valid Job/fallback admission.
        assert events.count(('original Popen', process)) == 1
        assert len([value for value in events if value[0] == 'CloseHandle']) == 1
        assert not process.stdout.closed and not process.stderr.closed
    finally:
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('binary', [False, True])
def test_original_supervisor_text_and_binary_keep_exact_startup_job_source_same_owner_and_refuse_new_start(tmp_path, monkeypatch, binary):
    # Capture root before os.name simulation; no native Popen/Job/handle created.
    root = tmp_path.resolve()
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    events = []
    observe_api(monkeypatch, events, setup='set', close='throw')
    process = observe_process(monkeypatch, events)
    # Text original TemporaryFile allocation is actual disposable fixture IO but
    # its Windows platform dispatch is not under test here.
    spools = []
    def spool():
        value = io.BytesIO()
        spools.append(value)
        return value
    monkeypatch.setattr(module.tempfile, 'TemporaryFile', spool)
    async def scenario():
        operation = supervisor.run_binary if binary else supervisor.run
        with pytest.raises(module.ProcessCleanupError) as error:
            await operation(['offline fixture'], cwd=root, run_id='original')
        managed = error.value.managed
        source = error.value.source
        assert supervisor.cleanup_failed and fault.broken and fault.cleanup_uncertain
        assert supervisor._active['original'][0] is managed and managed.process is process and managed.job is source
        assert supervisor._unresolved_sources[id(source)] is source
        assert next(iter(fault._cleanup_sources.values())) is supervisor
        before = list(events)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await operation(['no new start'], cwd=root, run_id='forbidden')
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.cancel_run('original')
        assert events == before and supervisor.active_count == 1
    try:
        asyncio.run(scenario())
    finally:
        process.stdout.close()
        process.stderr.close()
        for stream in spools:
            stream.close()


def test_actual_service_same_supervisor_startup_job_unknown_keeps_same_failed_close_task_and_owner(tmp_path, monkeypatch):
    from doppel_agent.runtime.service import RunService
    async def scenario():
        service = RunService(tmp_path)
        await service.start()
        root = tmp_path.resolve()
        events = []
        with monkeypatch.context() as patch:
            observe_api(patch, events, setup='assign', close='false')
            process = observe_process(patch, events)
            with pytest.raises(module.ProcessCleanupError):
                await service.process_supervisor.run_binary(['offline fixture'], cwd=root, run_id='original')
        try:
            assert service._provider_receipt_fault.broken and service._provider_receipt_fault.cleanup_uncertain
            assert next(iter(service._provider_receipt_fault._cleanup_sources.values())) is service.process_supervisor
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await service.close()
            original_close = service._close_task
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await service.close()
            assert service._close_task is original_close and service._owner.held and not service.cleanup_complete
            assert service.process_supervisor.active_count == 1
        finally:
            process.stdout.close()
            process.stderr.close()
            service._owner.release()  # Explicit disposable teardown, never original source recovery.
    asyncio.run(scenario())


@pytest.mark.parametrize('binary', [False, True])
def test_original_completed_text_or_binary_wait_not_job_handle_close_proof_keeps_source_and_owner(tmp_path, monkeypatch, binary):
    events = []
    observe_api(monkeypatch, events, close='false')
    job = module._WindowsJob(12345)
    process = SimpleNamespace(stdout=io.BytesIO(b'offline'), stderr=io.BytesIO(), returncode=0, wait=lambda: 0)
    managed = module._ManagedProcess(process, 'local_API_proxy_not_native', job)
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    async def scenario():
        operation = supervisor.run_binary if binary else supervisor.run
        with pytest.raises(module.ProcessCleanupError) as error:
            await operation(['offline fixture'], cwd=tmp_path, run_id='original')
        assert error.value.source is job and supervisor._unresolved_sources[id(job)] is job
        assert supervisor._active['original'][0] is managed and supervisor.cleanup_failed
        assert fault.broken and next(iter(fault._cleanup_sources.values())) is supervisor
        before = list(events)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.cancel_run('original')
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert events == before and len([value for value in events if value[0] == 'CloseHandle']) == 1
    try:
        asyncio.run(scenario())
    finally:
        process.stdout.close()
        process.stderr.close()


def test_actual_original_job_healthy_close_returns_once_and_keeps_idempotent_known_api_boundary(monkeypatch):
    events = []
    observe_api(monkeypatch, events)
    source = module._WindowsJob(12345)
    source.close()
    before = list(events)
    source.close_checked()
    source.close()
    assert events == before and source._handle is None and source._close_returned and not source.cleanup_uncertain


def test_actual_original_create_unusable_return_is_not_known_job_identity_or_close_authority(monkeypatch):
    events = []
    observe_api(monkeypatch, events, create='unusable')
    with pytest.raises(module.ProcessCleanupError, match='^job allocation unproved$') as error:
        module._WindowsJob(12345)
    source = error.value.source
    assert source._create_returned and source._handle == 'PRIVATE_UNUSABLE_HANDLE' and source.cleanup_uncertain
    before = list(events)
    with pytest.raises(module.ProcessCleanupError):
        source.close_checked()
    assert events == before and not source._close_attempted


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('phase', ['false', 'throw', 'none', 'unusable'])
def test_original_job_termination_ack_failure_keeps_original_attempt_and_disallows_other_api(monkeypatch, strict, phase):
    events, calls = [], []
    kernel = observe_api(monkeypatch, events)
    def terminate(handle, code):
        calls.append((handle, code))
        if phase == 'throw':
            raise OSError('PRIVATE_TERMINATE_ACK_UNKNOWN')
        return {'false': 0, 'none': None, 'unusable': 'PRIVATE_ACK'}.get(phase)
    kernel.TerminateJobObject = terminate
    source = module._WindowsJob(12345)
    operation = source.terminate_checked if strict else source.terminate
    with pytest.raises(module.ProcessCleanupError, match='^job termination unproved$') as error:
        operation()
    frame = source._termination_source
    assert error.value.source is source and source.cleanup_uncertain and source._handle == 987654
    assert frame.handle == source._handle and frame.attempted and frame.returned is (phase != 'throw')
    assert frame.finished and not frame.acknowledged and frame.done.is_set()
    before = list(events)
    for action in (source.terminate, source.terminate_checked, source.wait_empty, source.close, source.close_checked):
        with pytest.raises(module.ProcessCleanupError):
            action()
    assert calls == [(987654, 1)] and events == before


def test_original_job_known_termination_ack_is_shared_once_not_accounting_zero(monkeypatch):
    events = []
    observe_api(monkeypatch, events)
    source = module._WindowsJob(12345)
    source.terminate()
    frame = source._termination_source
    source.terminate_checked()
    source.terminate()
    assert frame.acknowledged and frame.returned and frame.finished
    assert source._accounting_source is None and source._handle == 987654 and not source.cleanup_uncertain
    assert [name for name, _ in events].count('TerminateJobObject') == 1
    source.close_checked()  # Known proxy closure only, NOT OS/tree evidence.


@pytest.mark.parametrize('phase', ['false', 'throw', 'unusable', 'short', 'timeout', 'sleep_error'])
def test_original_job_accounting_failure_keeps_exact_buffers_and_blocks_a_second_query_or_close(monkeypatch, phase):
    events, queries = [], []
    kernel = observe_api(monkeypatch, events)
    clock = [0.0]
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    def query(handle, kind, value, size, written):
        queries.append((handle, value._obj, written._obj))
        if phase == 'throw':
            raise OSError('PRIVATE_ACCOUNTING_QUERY_UNKNOWN')
        written._obj.value = size - 1 if phase == 'short' else size
        value._obj.ActiveProcesses = 1 if phase in {'timeout', 'sleep_error'} else 0
        if phase == 'timeout':
            clock[0] = 2.0
        return {'false': 0, 'unusable': 'PRIVATE_QUERY_ACK'}.get(phase, 1)
    def pause(_delay):
        raise OSError('PRIVATE_ACCOUNTING_SLEEP_UNKNOWN')
    if phase == 'sleep_error':
        monkeypatch.setattr(module, 'sleep', pause)
    kernel.QueryInformationJobObject = query
    source = module._WindowsJob(12345)
    with pytest.raises(module.ProcessCleanupError) as error:
        source.wait_empty(timeout_seconds=1)
    frame = source._accounting_source
    original = frame.queries[0]
    assert error.value.source is source and source.cleanup_uncertain and source._handle == 987654
    assert frame.attempted and frame.finished and not frame.acknowledged
    assert original.value is queries[0][1] and original.written is queries[0][2]
    assert original.attempted and original.returned is (phase != 'throw')
    before = list(events)
    for action in (source.wait_empty, source.terminate, source.terminate_checked, source.close_checked):
        with pytest.raises(module.ProcessCleanupError):
            action()
    assert len(queries) == 1 and events == before and 'PRIVATE_' not in str(error.value)


def test_original_job_accounting_keeps_nonzero_then_exact_zero_receipts_in_same_attempt(monkeypatch):
    events, calls = [], []
    kernel = observe_api(monkeypatch, events)
    def query(handle, kind, value, size, written):
        calls.append((handle, value._obj, written._obj))
        written._obj.value = size
        value._obj.ActiveProcesses = 1 if len(calls) == 1 else 0
        return 1
    kernel.QueryInformationJobObject = query
    monkeypatch.setattr(module, 'sleep', lambda _: None)
    source = module._WindowsJob(12345)
    source.wait_empty()
    frame = source._accounting_source
    assert frame.acknowledged and frame.returned and frame.finished and len(frame.queries) == 2
    assert [entry.value.ActiveProcesses for entry in frame.queries] == [1, 0]
    source.wait_empty()  # Same original acknowledged observation, not another query/physical test.
    assert len(calls) == 2 and source._accounting_source is frame and not source.cleanup_uncertain
    source.close_checked()


def test_original_job_close_cannot_overtake_entered_termination_and_late_ack_cannot_reset_fault(monkeypatch):
    events = []
    kernel = observe_api(monkeypatch, events)
    source = None
    def terminate(handle, code):
        assert source._termination_source.attempted and not source._termination_source.finished
        with pytest.raises(module.ProcessCleanupError):
            source.close_checked()
        return 1  # Late known proxy ACK, not an excuse to clear the concurrent original fault.
    kernel.TerminateJobObject = terminate
    source = module._WindowsJob(12345)
    with pytest.raises(module.ProcessCleanupError):
        source.terminate_checked()
    assert source.cleanup_uncertain and source._handle == 987654 and not source._close_attempted
    assert source._termination_source.finished and not source._termination_source.acknowledged
    assert all(name != 'CloseHandle' for name, _ in events)


@pytest.mark.parametrize('operation', ['terminate_checked', 'wait_empty'])
def test_original_job_entered_close_fences_other_api_and_late_close_ack_keeps_fault_handle(monkeypatch, operation):
    events = []
    kernel = observe_api(monkeypatch, events)
    source = None
    def close(handle):
        events.append(('original entered close', handle))
        assert source._close_attempted and not source._close_returned
        with pytest.raises(module.ProcessCleanupError):
            getattr(source, operation)()
        return 1  # Original close ACK must not erase independently faulted handle/source.
    kernel.CloseHandle = close
    source = module._WindowsJob(12345)
    with pytest.raises(module.ProcessCleanupError):
        source.close_checked()
    assert source.cleanup_uncertain and source._handle == 987654 and not source._close_returned
    assert source._close_response == 1
    assert source._termination_source is None and source._accounting_source is None
    before = list(events)
    for action in (source.close_checked, source.terminate_checked, source.wait_empty):
        with pytest.raises(module.ProcessCleanupError):
            action()
    assert events == before
