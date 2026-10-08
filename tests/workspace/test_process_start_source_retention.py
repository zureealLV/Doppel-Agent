"""C2c2h2 FIRST original startup/register/spool definitions, ALL UNRUN.

Original APIs observed by local fixtures, no real Popen/Win32/native/tree proof.
Disposable stream teardown isn't original recovery/drain/retry authority.
"""

import asyncio
import io
import tempfile
import threading
from types import SimpleNamespace

import pytest

import doppel_agent.workspace.process_supervisor as module
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


def process_fixture():
    return SimpleNamespace(stdout=io.BytesIO(b'offline'), stderr=io.BytesIO(), pid=12345, _handle=12345,
                           returncode=0, wait=lambda: 0)


@pytest.mark.parametrize('binary', [False, True])
@pytest.mark.parametrize('phase', ['opaque', 'unusable'])
def test_original_popen_unreturned_or_unusable_receipt_retained_not_never_started_or_second_factory(tmp_path, monkeypatch, binary, phase):
    calls = []
    process = process_fixture()
    spools = [] if binary else observe_spools(monkeypatch, 'healthy', [])
    def popen(*args, **kwargs):
        calls.append('original Popen')
        if phase == 'opaque':
            raise OSError('PRIVATE_ALLOCATED_NO_RETURN')
        return None
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    async def scenario():
        operation = supervisor.run_binary if binary else supervisor.run
        with pytest.raises(module.ProcessCleanupError, match='^process allocation unproved$') as error:
            await operation(['offline fixture'], cwd=tmp_path, run_id='original')
        source = error.value.source
        assert source.spawn_attempted and source.spawn_returned is (phase == 'unusable')
        assert source.process is None and supervisor._unresolved_sources[id(source)] is source
        assert error.value.managed is None and not source.job_attempted
        assert fault.cleanup_uncertain and next(iter(fault._cleanup_sources.values())) is supervisor
        assert supervisor.active_count == 0  # NO known process, not known no process.
        if not binary:
            files = [value for value in supervisor._unresolved_sources.values()
                     if isinstance(value, module._ProcessFileLifetime)]
            assert len(files) == 2 and all(not value.close_attempted for value in files)
            assert all(not value.producer_settled for value in files)
            assert all(not value.raw.closed and not value.exits for value in spools)
        for next_operation in (lambda: operation(['no second'], cwd=tmp_path, run_id='next'), supervisor.close):
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await next_operation()
        assert calls == ['original Popen']
    try:
        asyncio.run(scenario())
    finally:
        for spool in spools:
            spool.raw.close()  # Disposable cleanup ONLY, no factory receipt recovery.
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('strict', [False, True])
def test_original_external_job_factory_exception_keeps_same_startup_process_attempt_not_known_no_job_or_fallback(tmp_path, monkeypatch, strict):
    root = tmp_path.resolve()
    process = process_fixture()
    calls = []
    monkeypatch.setattr(module.os, 'name', 'nt')
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    def job(_handle):
        calls.append('original opaque Job factory')
        raise OSError('PRIVATE_ALLOCATED_JOB_NO_RETURN')
    monkeypatch.setattr(module, '_WindowsJob', job)
    monkeypatch.setattr(module, '_resume_suspended_primary_thread', lambda *_args: pytest.fail('resume forbidden'))
    monkeypatch.setattr(module.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('fallback forbidden'))
    try:
        with pytest.raises(module.ProcessCleanupError, match='^process startup cleanup unproved$') as error:
            module.ProcessSupervisor._start(['offline fixture'], root, {}, module.subprocess.PIPE,
                                            module.subprocess.PIPE, require_tree_ownership=strict)
        source = error.value.source
        assert source.process is process and source.spawn_returned and source.job_attempted and not source.job_returned
        assert error.value.managed.process is process and error.value.managed.startup is source
        assert source.job is None and source.cleanup_uncertain and not source.resume_attempted
        assert calls == ['original opaque Job factory']
        assert not process.stdout.closed and not process.stderr.closed
    finally:
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('binary', [False, True])
@pytest.mark.parametrize('phase', ['before_insert', 'after_insert'])
def test_original_registration_failure_retains_exact_managed_attempt_and_owner_never_second_register(tmp_path, monkeypatch, binary, phase):
    process = process_fixture()
    spools = [] if binary else observe_spools(monkeypatch, 'healthy', [])
    managed = module._ManagedProcess(process, 'local fixture not native', None)
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    original_register = supervisor._register
    calls = []
    def register(run_id, value):
        calls.append((run_id, value))
        if phase == 'after_insert':
            original_register(run_id, value)
        raise OSError('PRIVATE_REGISTER_REPLY_LOST')
    monkeypatch.setattr(supervisor, '_register', register)
    async def scenario():
        operation = supervisor.run_binary if binary else supervisor.run
        with pytest.raises(module.ProcessCleanupError, match='^process registration unproved$') as error:
            await operation(['offline fixture'], cwd=tmp_path, run_id='original')
        source = error.value.source
        assert source.managed is managed and source.attempted and not source.returned
        assert supervisor._unresolved_sources[id(source)] is source and fault.cleanup_uncertain
        assert supervisor.active_count == (phase == 'after_insert')
        if not binary:
            files = [value for value in supervisor._unresolved_sources.values()
                     if isinstance(value, module._ProcessFileLifetime)]
            assert len(files) == 2 and all(value.managed is managed for value in files)
            assert all(not value.close_attempted and not value.producer_settled for value in files)
            assert all(not value.raw.closed and not value.exits for value in spools)
        with pytest.raises(module.ProcessCleanupError):
            supervisor._admit_original('original', managed)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert calls == [('original', managed)]
    try:
        asyncio.run(scenario())
    finally:
        for spool in spools:
            spool.raw.close()  # Disposable cleanup ONLY, no original registration recovery.
        process.stdout.close()
        process.stderr.close()


def observe_spools(monkeypatch, phase, events, *, target_index=0):
    original = tempfile.TemporaryFile
    streams = []
    class Stream:
        def __init__(self, raw, index):
            self.raw, self.index = raw, index
            self.exits = self.closes = 0
        def __enter__(self):
            events.append(('enter', self.index))
            if self.index == target_index and phase in {'enter', 'enter_close'}:
                raise OSError('PRIVATE_ENTER')
            self.raw.__enter__()
            if self.index == target_index and phase == 'enter_none':
                return None
            return self
        def __exit__(self, *details):
            self.exits += 1
            events.append(('exit attempted', self.index))
            if self.index == target_index and phase == 'exit':
                raise OSError('PRIVATE_EXIT')
            result = self.raw.__exit__(*details)
            if self.index == target_index and phase == 'closed_then_throw':
                raise OSError('PRIVATE_CLOSED_THROW')
            events.append(('exit returned', self.index))
            return result
        def close(self):
            self.closes += 1
            if self.index == target_index and phase == 'enter_close':
                raise OSError('PRIVATE_SETUP_CLOSE')
            self.raw.close()
        def seek(self, *args):
            events.append(('seek', self.index))
            return self.raw.seek(*args)
        def read(self, *args):
            events.append(('read', self.index))
            return self.raw.read(*args)
    def factory():
        value = Stream(original(), len(streams))
        streams.append(value)
        if value.index == target_index and phase == 'opaque':
            raise OSError('PRIVATE_ALLOCATED_SPOOL_NO_RETURN')
        return value
    monkeypatch.setattr(module.tempfile, 'TemporaryFile', factory)
    return streams


@pytest.mark.parametrize('phase', ['opaque', 'enter_close', 'exit', 'closed_then_throw'])
@pytest.mark.parametrize('target_index', [0, 1])
def test_original_text_spool_failure_keeps_same_file_frame_owner_and_no_second_exit(tmp_path, monkeypatch, phase, target_index):
    events = []
    streams = observe_spools(monkeypatch, phase, events, target_index=target_index)
    process = process_fixture()
    managed = module._ManagedProcess(process, 'local fixture not native', None)
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    starts = []
    def start(*args, **kwargs):
        starts.append('original start')
        return managed
    monkeypatch.setattr(supervisor, '_start', start)
    async def scenario():
        with pytest.raises(module.ProcessCleanupError) as error:
            await supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original')
        source = error.value.source
        assert supervisor._unresolved_sources[id(source)] is source and source.factory_attempted
        assert source.resource is (None if phase == 'opaque' else streams[target_index])
        assert source.cleanup_uncertain and not source.close_returned and fault.cleanup_uncertain
        if starts:
            assert supervisor._active['original'][0] is managed  # Spool close BEFORE unregister gate.
        before = list(events)
        with pytest.raises(module.ProcessCleanupError):
            module._close_original_process_file(source)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert events == before and len(starts) == (phase in {'exit', 'closed_then_throw'})
    try:
        asyncio.run(scenario())
    finally:
        for stream in streams:
            stream.raw.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('phase', ['enter', 'enter_none'])
def test_original_spool_known_setup_failure_same_close_is_refused_before_popen_not_resource_unknown(tmp_path, monkeypatch, phase):
    events = []
    streams = observe_spools(monkeypatch, phase, events)
    supervisor = module.ProcessSupervisor()
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Popen forbidden'))
    async def scenario():
        with pytest.raises(module.ProcessSupervisionError, match='^process spool unavailable$'):
            await supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original')
        assert not supervisor.cleanup_failed and not supervisor._unresolved_sources
        assert streams[0].raw.closed and streams[0].closes == (phase == 'enter')
        assert streams[0].exits == (phase == 'enter_none')
        await supervisor.close()
    asyncio.run(scenario())


def test_original_text_registration_drainer_returns_only_after_both_original_spool_exits(tmp_path, monkeypatch):
    events = []
    streams = observe_spools(monkeypatch, 'healthy', events)
    process = process_fixture()
    managed = module._ManagedProcess(process, 'local fixture not native', None)
    supervisor = module.ProcessSupervisor()
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    original_unregister = supervisor._unregister
    def unregister(run_id, value):
        assert all(stream.raw.closed and stream.exits == 1 for stream in streams)
        events.append('original unregister')
        return original_unregister(run_id, value)
    monkeypatch.setattr(supervisor, '_unregister', unregister)
    async def scenario():
        result = await supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original')
        assert result.exit_code == 0 and supervisor.active_count == 0 and not supervisor.cleanup_failed
        assert events[-1] == 'original unregister'
        await supervisor.close()
    try:
        asyncio.run(scenario())
    finally:
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('phase', ['wait_error', 'wait_none', 'wait_bool', 'returncode_none', 'mismatch'])
def test_original_text_failed_or_unusable_wait_keeps_same_worker_process_and_spools_not_known_drain(tmp_path, monkeypatch, phase):
    events = []
    spools = observe_spools(monkeypatch, 'healthy', events)
    process = process_fixture()
    def wait():
        events.append('original wait')
        if phase == 'wait_error':
            raise OSError('PRIVATE_WAIT_FAULT')
        if phase == 'wait_none':
            return None
        if phase == 'wait_bool':
            return True
        return 1 if phase == 'mismatch' else 0
    process.wait = wait
    if phase == 'returncode_none':
        process.returncode = None
    class Job:
        def close(self):
            pytest.fail('original Job close before known wait forbidden')
    managed = module._ManagedProcess(process, 'local fixture not native', Job())
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    async def scenario():
        with pytest.raises(module.ProcessCleanupError, match='^process wait cleanup unproved$') as error:
            await supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original')
        waiter = error.value.source
        assert waiter.done() and error.value.managed is managed
        assert supervisor._unresolved_sources[id(waiter)] is waiter
        assert supervisor._unresolved_sources[id(managed)] is managed and supervisor.active_count == 1
        assert not supervisor._drainers[id(managed)].is_set() and fault.cleanup_uncertain
        assert not any(isinstance(value, tuple) and value[0] in {'seek', 'read'} for value in events)
        assert all(not value.raw.closed and value.exits == 0 for value in spools)
        files = [value for value in supervisor._unresolved_sources.values()
                 if isinstance(value, module._ProcessFileLifetime)]
        assert len(files) == 2 and all(not value.close_attempted and not value.producer_settled for value in files)
        for frame in files:
            with pytest.raises(module.ProcessCleanupError, match='producer cleanup unproved'):
                module._close_original_process_file(frame)
        assert all(not value.raw.closed and value.exits == 0 for value in spools)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert events.count('original wait') == 1
    try:
        asyncio.run(scenario())
    finally:
        for spool in spools:
            spool.raw.close()  # Fixture-only teardown, source remains unknown.
        process.stdout.close()
        process.stderr.close()


def test_original_text_termination_fault_pending_same_waiter_does_not_close_spools_or_unregister(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    spools = observe_spools(monkeypatch, 'healthy', [])
    process = process_fixture()
    def wait():
        entered.set()
        if not release.wait(5):
            raise RuntimeError('fixture release not observed')
        return 0
    process.wait = wait
    class Job:
        def terminate(self):
            raise OSError('PRIVATE_TERMINATE_FAULT')
        def close(self):
            pytest.fail('original Job close with pending producer forbidden')
    managed = module._ManagedProcess(process, 'local fixture not native', Job())
    supervisor = module.ProcessSupervisor()
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    async def scenario():
        task = asyncio.create_task(supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original', timeout_seconds=5))
        waiter = None
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    if task.done():
                        await task
                        pytest.fail('fixture ended before original wait entry')
                    await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(module.ProcessCleanupError, match='^process wait cleanup unproved$') as error:
                await task
            waiter = error.value.source
            assert not waiter.done() and supervisor._unresolved_sources[id(waiter)] is waiter
            assert supervisor._active['original'][0] is managed and not supervisor._drainers[id(managed)].is_set()
            assert all(not spool.raw.closed and not spool.exits for spool in spools)
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await supervisor.close()
        finally:
            release.set()  # Join SAME original fixture worker, NOT replacement/drain authority.
            if waiter is not None:
                await asyncio.shield(waiter)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert supervisor.cleanup_failed and supervisor.active_count == 1
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        for spool in spools:
            spool.raw.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize('phase', ['popen', 'registration', 'wait_error', 'spool_exit'])
def test_actual_service_same_supervisor_original_start_register_spool_unknown_keeps_failed_close_and_owner(tmp_path, monkeypatch, phase):
    from doppel_agent.runtime.service import RunService
    async def scenario():
        service = RunService(tmp_path)
        await service.start()
        process = process_fixture()
        if phase == 'wait_error':
            def wait():
                raise OSError('PRIVATE_SERVICE_WAIT_FAULT')
            process.wait = wait
        spools = []
        try:
            with monkeypatch.context() as patch:
                spools = observe_spools(patch, 'exit' if phase == 'spool_exit' else 'healthy', [])
                if phase == 'popen':
                    def popen(*args, **kwargs):
                        raise OSError('PRIVATE_OPAQUE_POPEN')
                    patch.setattr(module.subprocess, 'Popen', popen)
                else:
                    managed = module._ManagedProcess(process, 'local fixture not native', None)
                    patch.setattr(service.process_supervisor, '_start', lambda *args, **kwargs: managed)
                    if phase == 'registration':
                        def register(*args):
                            raise OSError('PRIVATE_REGISTER_UNRETURNED')
                        patch.setattr(service.process_supervisor, '_register', register)
                with pytest.raises(module.ProcessCleanupError) as error:
                    await service.process_supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original')
            assert service.process_supervisor._unresolved_sources[id(error.value.source)] is error.value.source
            assert service._provider_receipt_fault.broken and service._provider_receipt_fault.cleanup_uncertain
            assert next(iter(service._provider_receipt_fault._cleanup_sources.values())) is service.process_supervisor
            assert service.process_supervisor.active_count == (phase in {'wait_error', 'spool_exit'})
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await service.close()
            original_close = service._close_task
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await service.close()
            assert service._close_task is original_close and service._owner.held and not service.cleanup_complete
        finally:
            for spool in spools:
                spool.raw.close()  # Fixture-only, NOT original resource recovery.
            process.stdout.close()
            process.stderr.close()
            service._owner.release()  # Disposable teardown only, no service reset.
    asyncio.run(scenario())


def test_original_text_repeated_caller_cancel_joins_same_wait_before_spool_exit_and_unregister(tmp_path, monkeypatch):
    entered, release, terminated = threading.Event(), threading.Event(), threading.Event()
    events = []
    spools = observe_spools(monkeypatch, 'healthy', events)
    process = process_fixture()
    def wait():
        entered.set()
        if not release.wait(5):
            raise RuntimeError('fixture release not observed')
        events.append('original wait returned')
        return 0
    process.wait = wait
    class Job:
        def terminate(self):
            events.append('original terminate')
            terminated.set()
        def close(self):
            assert 'original wait returned' in events
            assert all(not spool.raw.closed for spool in spools)
            events.append('original Job close')
    managed = module._ManagedProcess(process, 'local fixture not native', Job())
    supervisor = module.ProcessSupervisor()
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    async def scenario():
        task = asyncio.create_task(supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original', timeout_seconds=5))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    if task.done():
                        await task
                        pytest.fail('fixture ended before original wait entry')
                    await asyncio.sleep(0)
            task.cancel()
            async with asyncio.timeout(2):
                while not terminated.is_set():
                    await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and supervisor.active_count == 1
            assert not supervisor._drainers[id(managed)].is_set()
            assert all(not spool.exits and not spool.raw.closed for spool in spools)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert supervisor.active_count == 0 and not supervisor.cleanup_failed
            assert events.count('original terminate') == 1 and events.count('original Job close') == 1
            assert all(spool.raw.closed and spool.exits == 1 for spool in spools)
            await supervisor.close()
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        for spool in spools:
            spool.raw.close()
        process.stdout.close()
        process.stderr.close()


def test_original_text_known_parent_wait_failed_job_close_keeps_both_original_spools(tmp_path, monkeypatch):
    spools = observe_spools(monkeypatch, 'healthy', [])
    process = process_fixture()
    calls = []
    class Job:
        def close(self):
            calls.append('original Job close')
            raise module.ProcessCleanupError('job handle cleanup unproved', source=self)
    job = Job()
    managed = module._ManagedProcess(process, 'local fixture not native', job)
    supervisor = module.ProcessSupervisor()
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    async def scenario():
        with pytest.raises(module.ProcessCleanupError) as error:
            await supervisor.run(['offline fixture'], cwd=tmp_path, run_id='original')
        assert error.value.source is job and supervisor._unresolved_sources[id(job)] is job
        assert supervisor._active['original'][0] is managed and not supervisor._drainers[id(managed)].is_set()
        assert all(not spool.exits and not spool.raw.closed for spool in spools)
        files = [value for value in supervisor._unresolved_sources.values()
                 if isinstance(value, module._ProcessFileLifetime)]
        assert len(files) == 2 and all(not value.producer_settled and not value.close_attempted for value in files)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert calls == ['original Job close']
    try:
        asyncio.run(scenario())
    finally:
        for spool in spools:
            spool.raw.close()  # Fixture-only, NOT original Job/source recovery.
        process.stdout.close()
        process.stderr.close()
