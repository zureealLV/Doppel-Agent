"""h3 FIRST original worker/pipe/close definitions, ALL UNRUN.

Local original asyncio/BytesIO fixture observations only. No real child/Win32/
physical process/tree proof. Disposable fixture cleanup never resets owner fault.
"""

import asyncio
import io
import threading
from types import SimpleNamespace

import pytest

import doppel_agent.workspace.process_supervisor as module
from doppel_agent.runtime.provider_recording import ProviderReceiptFault


def configured(monkeypatch, *, stdout=None, stderr=None, wait=None):
    process = SimpleNamespace(stdout=io.BytesIO(b'offline') if stdout is None else stdout,
                              stderr=io.BytesIO() if stderr is None else stderr,
                              returncode=0, wait=lambda: 0)
    if wait is not None:
        process.wait = wait
    events = []
    class Job:
        def terminate(self):
            events.append('terminate')
        def terminate_checked(self):
            events.append('terminate checked')
        def wait_empty(self):
            events.append('fixture zero observation not native')
        def close(self):
            events.append('close Job')
        def close_checked(self):
            self.close()
    managed = module._ManagedProcess(process, 'local fixture not native', Job())
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    monkeypatch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
    return supervisor, managed, fault, events


@pytest.mark.parametrize('binary', [False, True])
@pytest.mark.parametrize('phase', ['thread_opaque', 'task_before', 'task_lost_return', 'task_none'])
def test_original_worker_factory_attempt_retains_same_coroutine_managed_owner_no_second_factory(tmp_path, monkeypatch, phase, binary):
    supervisor, managed, fault, events = configured(monkeypatch)
    original_thread, original_task = asyncio.to_thread, asyncio.create_task
    allocated_tasks, unreturned_coroutines, calls = [], [], []
    async def scenario():
        with monkeypatch.context() as patch:
            def thread_factory(function, *args):
                calls.append('original to_thread')
                coroutine = original_thread(function, *args)
                if phase == 'thread_opaque':
                    unreturned_coroutines.append(coroutine)
                    raise OSError('PRIVATE_COROUTINE_NO_RETURN')
                return coroutine
            def task_factory(coroutine, **kwargs):
                calls.append('original create_task')
                if phase == 'task_before':
                    raise OSError('PRIVATE_TASK_BEFORE_RETURN')
                if phase == 'task_none':
                    return None
                task = original_task(coroutine, **kwargs)
                allocated_tasks.append(task)
                raise OSError('PRIVATE_TASK_ALLOCATED_REPLY_LOST')
            patch.setattr(module.asyncio, 'to_thread', thread_factory)
            patch.setattr(module.asyncio, 'create_task', task_factory)
            operation = supervisor.run_binary if binary else supervisor.run
            with pytest.raises(module.ProcessCleanupError, match='^process worker allocation unproved$') as error:
                await operation(['offline fixture'], cwd=tmp_path, run_id='original')
        source = error.value.source
        owned = supervisor._operations[id(managed)]
        assert owned.workers[0] is source and source.managed is managed
        assert source.coroutine_attempted and not source.cleanup_known
        assert source.task_attempted is (phase != 'thread_opaque')
        assert source.task_returned is (phase == 'task_none') and source.task is None
        assert supervisor._unresolved_sources[id(source)] is source and fault.cleanup_uncertain
        assert supervisor.active_count == 1 and not supervisor._drainers[id(managed)].is_set()
        assert 'close Job' not in events
        before = list(calls)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.cancel_run('original')
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert calls == before
        # Dispose ONLY originals observed by fixture, no original receipt recovery.
        await asyncio.gather(*allocated_tasks, return_exceptions=True)
        if source.coroutine is not None and not allocated_tasks:
            source.coroutine.close()
        for coroutine in unreturned_coroutines:
            coroutine.close()
    try:
        asyncio.run(scenario())
    finally:
        for frame in supervisor._unresolved_sources.values():
            if isinstance(frame, module._ProcessFileLifetime) and frame.resource is not None:
                frame.resource.close()
        managed.process.stdout.close()
        managed.process.stderr.close()


@pytest.mark.parametrize('index', [1, 2, 3])
def test_original_binary_partial_worker_task_allocation_keeps_all_earlier_tasks_and_both_pipes(tmp_path, monkeypatch, index):
    supervisor, managed, fault, events = configured(monkeypatch)
    original_task = asyncio.create_task
    tasks, calls = [], []
    async def scenario():
        with monkeypatch.context() as patch:
            def factory(coroutine, **kwargs):
                calls.append(coroutine)
                task = original_task(coroutine, **kwargs)
                tasks.append(task)
                if len(calls) == index:
                    raise OSError('PRIVATE_NTH_TASK_LOST_RETURN')
                return task
            patch.setattr(module.asyncio, 'create_task', factory)
            with pytest.raises(module.ProcessCleanupError) as error:
                await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original')
        owned = supervisor._operations[id(managed)]
        assert owned.workers[-1] is error.value.source and len(owned.workers) == index
        assert [frame.task for frame in owned.workers[:-1]] == tasks[:-1]
        assert owned.workers[-1].task is None and owned.workers[-1].coroutine is tasks[-1].get_coro()
        assert all(not frame.close_attempted and not frame.producer_settled for frame in owned.files)
        assert fault.cleanup_uncertain and supervisor.active_count == 1 and 'close Job' not in events
        await asyncio.gather(*tasks, return_exceptions=True)  # Fixture-only join of SAME tasks.
        assert supervisor.cleanup_failed and supervisor.active_count == 1
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


@pytest.mark.parametrize('phase', ['cancel_error', 'cancel_cancelled', 'drainer_error', 'drainer_false',
                                   'gather_opaque', 'gather_lost_return', 'gather_none',
                                   'close_task_lost_return', 'cancel_task_lost_return', 'drainer_task_lost_return'])
def test_original_close_fault_retains_same_close_workers_gathers_and_fault_callback(monkeypatch, phase):
    """FIRST close exit gates; injected ORIGINAL callbacks, never physical drain proof."""
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    original_task, original_gather = asyncio.create_task, asyncio.gather
    tasks, futures, calls = [], [], []
    marker = object()
    class Drainer:
        def wait(self, timeout):
            calls.append(('drainer', self, timeout))
            if phase == 'drainer_error':
                raise OSError('PRIVATE_ORIGINAL_DRAINER')
            return phase != 'drainer_false'
    drainer = Drainer()
    supervisor._active['original'] = [marker]
    supervisor._drainers[id(marker)] = drainer
    async def cancel(run_id):
        calls.append(('cancel', run_id))
        if phase == 'cancel_error':
            raise OSError('PRIVATE_ORIGINAL_CANCEL')
        if phase == 'cancel_cancelled':
            raise asyncio.CancelledError
        supervisor._active.clear()  # Explicit fixture registration, not process receipt.
    monkeypatch.setattr(supervisor, 'cancel_run', cancel)
    async def scenario():
        task_index = {'close_task_lost_return': 1, 'cancel_task_lost_return': 2,
                      'drainer_task_lost_return': 3}.get(phase)
        with monkeypatch.context() as patch:
            def make_task(coroutine, **kwargs):
                task = original_task(coroutine, **kwargs)
                tasks.append(task)
                if len(tasks) == task_index:
                    raise OSError('PRIVATE_ORIGINAL_CLOSE_TASK_LOST_RETURN')
                return task
            def gather(*sources, **kwargs):
                if phase == 'gather_opaque':
                    raise OSError('PRIVATE_ORIGINAL_CLOSE_GATHER')
                if phase == 'gather_none':
                    return None
                future = original_gather(*sources, **kwargs)
                futures.append(future)
                if phase == 'gather_lost_return':
                    raise OSError('PRIVATE_ORIGINAL_CLOSE_GATHER_LOST_RETURN')
                return future
            patch.setattr(module.asyncio, 'create_task', make_task)
            patch.setattr(module.asyncio, 'gather', gather)
            with pytest.raises(module.ProcessCleanupError) as error:
                await supervisor.close()
        owned, same_task = supervisor._close_source, supervisor._close_task
        assert owned is not None and owned.cleanup_uncertain and not owned.drained
        assert supervisor.cleanup_failed and fault.broken and fault.cleanup_uncertain
        assert fault._cleanup_sources[id(supervisor)] is supervisor
        assert supervisor._unresolved_sources[id(owned)] is owned
        for worker in owned.workers:
            assert supervisor._unresolved_sources[id(worker)] is worker
            if worker.source is not None:
                assert supervisor._unresolved_sources[id(worker.source)] is worker.source
        for gathered in owned.gathers:
            assert supervisor._unresolved_sources[id(gathered)] is gathered
        assert 'PRIVATE_' not in str(error.value)
        before = list(calls)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert supervisor._close_source is owned and supervisor._close_task is same_task and calls == before
        # ONLY fixture-observed same allocated tasks/futures. No supervisor recovery.
        await original_gather(*tasks, *futures, return_exceptions=True)
        assert fault.cleanup_uncertain and supervisor.cleanup_failed
    asyncio.run(scenario())


def test_original_cancelled_wait_future_does_not_prove_entered_thread_finished(tmp_path, monkeypatch):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def wait():
        entered.set()
        try:
            if not release.wait(5):
                raise RuntimeError('fixture worker release missing')
            return 0
        finally:
            exited.set()
    supervisor, managed, fault, events = configured(monkeypatch, wait=wait)
    async def scenario():
        running = asyncio.create_task(supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original',
                                                             require_tree_ownership=True))
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    if running.done():
                        await running
                        pytest.fail('original wait worker did not enter')
                    await asyncio.sleep(0)
            owned = supervisor._operations[id(managed)]
            worker = owned.workers[0]
            assert worker.started and not worker.finished
            worker.task.cancel()  # Cancel ORIGINAL Future only; thread still actually entered.
            with pytest.raises(module.ProcessCleanupError):
                await running
            assert worker.task.cancelled() and not worker.finished and not exited.is_set()
            assert not owned.drained and supervisor.active_count == 1 and fault.cleanup_uncertain
            assert not managed.process.stdout.closed and not managed.process.stderr.closed and 'close Job' not in events
            assert not supervisor._drainers[id(managed)].is_set()
            release.set()
            async with asyncio.timeout(2):
                while not exited.is_set() or not worker.finished:
                    await asyncio.sleep(0)
            assert worker.returned and supervisor.cleanup_failed  # Late return does NOT reset the original fault.
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await supervisor.close()
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


@pytest.mark.parametrize('phase', ['worker_lost_return', 'pipe_close', 'unregister_after'])
def test_actual_service_original_binary_fault_keeps_same_supervisor_close_task_and_root_owner(tmp_path, monkeypatch, phase):
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.service import RunService
    class Pipe(io.BytesIO):
        def close(self):
            raise OSError('PRIVATE_SERVICE_ORIGINAL_PIPE_CLOSE')
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        process = SimpleNamespace(stdout=Pipe() if phase == 'pipe_close' else io.BytesIO(b'fixture'),
                                  stderr=io.BytesIO(), returncode=0, wait=lambda: 0)
        managed = module._ManagedProcess(process, 'local fixture not native', None)
        supervisor = service.process_supervisor
        original_task, tasks = asyncio.create_task, []
        try:
            with monkeypatch.context() as patch:
                patch.setattr(supervisor, '_start', lambda *args, **kwargs: managed)
                if phase == 'worker_lost_return':
                    def factory(coroutine, **kwargs):
                        task = original_task(coroutine, **kwargs)
                        tasks.append(task)
                        raise OSError('PRIVATE_SERVICE_ORIGINAL_TASK_LOST_RETURN')
                    patch.setattr(module.asyncio, 'create_task', factory)
                elif phase == 'unregister_after':
                    original_unregister = supervisor._unregister
                    def unregister(*args):
                        original_unregister(*args)
                        raise OSError('PRIVATE_SERVICE_UNREGISTER_ACK_LOST')
                    patch.setattr(supervisor, '_unregister', unregister)
                with pytest.raises(module.ProcessCleanupError) as error:
                    await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original')
            assert supervisor._unresolved_sources[id(error.value.source)] is error.value.source
            assert service._provider_receipt_fault._cleanup_sources[id(supervisor)] is supervisor
            assert service._provider_receipt_fault.broken and service._provider_receipt_fault.cleanup_uncertain
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await service.close()
            close = service._close_task
            with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                await service.close()
            assert service._close_task is close and service._owner.held and not service.cleanup_complete
            with pytest.raises(RuntimeError):
                service._assert_provider_admission()
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)  # Fixture-only original task disposal.
            io.BytesIO.close(process.stdout)
            process.stderr.close()
            service._owner.release()  # ONLY isolated fixture teardown, not production recovery.
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', [False, True])
def test_original_two_overflow_workers_and_cancel_share_one_entered_termination(tmp_path, monkeypatch, failure):
    """FIRST local thread gates ONLY; no native parent/tree exit claim."""
    readers = threading.Barrier(2)
    entered, release, three_calls = threading.Event(), threading.Event(), threading.Event()
    calls, lock, errors = [], threading.Lock(), []
    class Reader(io.BytesIO):
        def read(self, size=-1):
            readers.wait(5)
            return b'xx'  # Original requested limit+1, valid overflow receipt.
    supervisor, managed, fault, events = configured(monkeypatch, stdout=Reader(), stderr=Reader())
    def terminate():
        events.append('one entered original termination')
        entered.set()
        if not release.wait(5):
            raise RuntimeError('fixture original termination release missing')
        if failure:
            raise module.ProcessCleanupError('PRIVATE_ORIGINAL_TERMINATION', source=managed.job)
    monkeypatch.setattr(managed.job, 'terminate_checked', terminate)
    original = supervisor._terminate_original
    def observed(owned):
        with lock:
            calls.append(owned)
            if len(calls) >= 3:
                three_calls.set()
        return original(owned)
    monkeypatch.setattr(supervisor, '_terminate_original', observed)
    def release_same_attempt():
        if not three_calls.wait(5):
            errors.append('fixture did not observe both original readers and cancel')
        release.set()
    async def scenario():
        running = asyncio.create_task(supervisor.run_binary(['fixture'], cwd=tmp_path, run_id='original',
                                                             output_limit=1, require_tree_ownership=True))
        releaser = None
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    if running.done():
                        await running
                    await asyncio.sleep(0)
            owned = supervisor._operations[id(managed)]
            assert owned.terminate_attempted and not owned.terminate_returned and not owned.terminate_done.is_set()
            assert not owned.drained and not managed.process.stdout.closed and supervisor.active_count == 1
            releaser = threading.Thread(target=release_same_attempt)  # Fixture gate only, not production cleanup.
            releaser.start()
            if failure:
                with pytest.raises(module.ProcessCleanupError):
                    await supervisor.cancel_run('original')
                with pytest.raises(module.ProcessCleanupError):
                    await running
                assert fault.cleanup_uncertain and not owned.terminate_returned and not owned.drained
                assert supervisor.active_count == 1 and not supervisor._drainers[id(managed)].is_set()
                assert not managed.process.stdout.closed and 'close Job' not in events
                with pytest.raises(module.ProcessCleanupError, match='quarantine'):
                    await supervisor.close()
            else:
                await supervisor.cancel_run('original')
                with pytest.raises(module.ProcessOutputLimitError):
                    await running
                assert owned.terminate_returned and owned.drained and supervisor.active_count == 0
                assert not fault.cleanup_uncertain
                await supervisor.close()
            assert all(item is owned for item in calls) and not errors
            assert events.count('one entered original termination') == 1
        finally:
            release.set()
            if releaser is not None:
                releaser.join(5)
            await asyncio.gather(running, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


def test_original_termination_late_return_cannot_reset_reentrant_same_operation_fault(monkeypatch):
    supervisor, managed, fault, events = configured(monkeypatch)
    owned = supervisor._begin_original('original', managed, 'binary', strict=True)
    def terminate():
        with pytest.raises(module.ProcessCleanupError):
            supervisor._terminate_original(owned)  # SAME-thread cannot join its own native attempt.
        events.append('late original return')
    monkeypatch.setattr(managed.job, 'terminate_checked', terminate)
    try:
        with pytest.raises(module.ProcessCleanupError):
            supervisor._terminate_original(owned)
        assert owned.terminate_done.is_set() and not owned.terminate_returned
        assert owned.cleanup_uncertain and fault.cleanup_uncertain and supervisor.active_count == 1
        assert events == ['late original return']
        with pytest.raises(module.ProcessCleanupError):
            supervisor._terminate_original(owned)
        assert events == ['late original return']
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


def test_caller_cancel_joins_same_known_original_close_without_latching_cleanup_fault(monkeypatch):
    fault = ProviderReceiptFault()
    supervisor = module.ProcessSupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    entered, release = threading.Event(), threading.Event()
    class Drainer:
        def wait(self, timeout):
            entered.set()
            return release.wait(5)
    supervisor._drainers[1] = Drainer()  # Explicit registry-only fixture, not process/tree proof.
    async def scenario():
        caller = asyncio.create_task(supervisor.close())
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    await asyncio.sleep(0)
            same = supervisor._close_task
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            assert not caller.done() and not same.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert supervisor._close_task is same and supervisor._close_source.drained
            assert not fault.cleanup_uncertain and not supervisor.cleanup_failed
            await supervisor.close()
            assert supervisor._close_task is same
        finally:
            release.set()
            await asyncio.gather(caller, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['cancel_error', 'drainer_error', 'drainer_false', 'gather_lost_return'])
def test_actual_service_original_close_failure_retains_same_close_source_and_root_owner(tmp_path, monkeypatch, phase):
    from doppel_agent.provider import MockProvider
    from doppel_agent.runtime.service import RunService
    original_gather, futures = asyncio.gather, []
    async def scenario():
        service = RunService(tmp_path, provider=MockProvider())
        await service.start()
        supervisor, marker = service.process_supervisor, object()
        class Drainer:
            def wait(self, timeout):
                if phase == 'drainer_error':
                    raise OSError('PRIVATE_SERVICE_DRAINER')
                return phase != 'drainer_false'
        drainer = Drainer()
        supervisor._active['original'] = [marker]
        supervisor._drainers[id(marker)] = drainer
        async def cancel(run_id):
            if phase == 'cancel_error':
                raise OSError('PRIVATE_SERVICE_CANCEL')
            supervisor._active.clear()
        monkeypatch.setattr(supervisor, 'cancel_run', cancel)
        original = supervisor._gather_original
        def gather(owned, sources, **kwargs):
            if phase != 'gather_lost_return':
                return original(owned, sources, **kwargs)
            with monkeypatch.context() as patch:
                def lost(*items, **options):
                    future = original_gather(*items, **options)
                    futures.append(future)
                    raise OSError('PRIVATE_SERVICE_CLOSE_GATHER_REPLY_LOST')
                patch.setattr(module.asyncio, 'gather', lost)
                return original(owned, sources, **kwargs)
        monkeypatch.setattr(supervisor, '_gather_original', gather)
        try:
            with pytest.raises(module.ProcessCleanupError) as error:
                await service.close()
            same, source = service._close_task, supervisor._close_source
            assert 'PRIVATE_' not in str(error.value)
            assert source.cleanup_uncertain and service._owner.held and not service.cleanup_complete
            assert service._provider_receipt_fault._cleanup_sources[id(supervisor)] is supervisor
            assert supervisor._unresolved_sources[id(drainer)] is drainer
            with pytest.raises(module.ProcessCleanupError):
                await service.close()
            assert service._close_task is same and supervisor._close_source is source
            with pytest.raises(RuntimeError):
                service._assert_provider_admission()
        finally:
            await original_gather(*futures, *(worker.task for worker in supervisor._close_source.workers
                                             if worker.task is not None), return_exceptions=True)
            service._owner.release()  # Isolated fixture teardown ONLY; never production recovery.
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['before', 'lost_return', 'none'])
def test_original_binary_gather_factory_unknown_keeps_same_workers_not_success_or_rebuilt_gather(tmp_path, monkeypatch, phase):
    supervisor, managed, fault, events = configured(monkeypatch)
    original_gather = asyncio.gather
    originals = []
    async def scenario():
        with monkeypatch.context() as patch:
            def factory(*sources, **kwargs):
                if phase == 'before':
                    raise OSError('PRIVATE_GATHER_NO_RETURN')
                if phase == 'none':
                    return None
                future = original_gather(*sources, **kwargs)
                originals.append(future)
                raise OSError('PRIVATE_GATHER_ALLOCATED_NO_RETURN')
            patch.setattr(module.asyncio, 'gather', factory)
            with pytest.raises(module.ProcessCleanupError, match='^process gather allocation unproved$') as error:
                await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original')
        owned = supervisor._operations[id(managed)]
        source = error.value.source
        assert owned.gathers[0] is source and source.sources == tuple(frame.task for frame in owned.workers)
        assert source.attempted and source.returned is (phase == 'none') and source.future is None
        assert supervisor._unresolved_sources[id(source)] is source and fault.cleanup_uncertain
        assert supervisor.active_count == 1 and 'close Job' not in events
        await original_gather(*(frame.task for frame in owned.workers), *originals, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


@pytest.mark.parametrize('receipt', [None, False, '', bytearray(), memoryview(b''), b'oversized reply'])
def test_original_read_unusable_eof_or_overlong_chunk_is_fixed_protocol_failure_after_original_strict_drain(tmp_path, monkeypatch, receipt):
    class Reader(io.BytesIO):
        def read(self, size=-1):
            return receipt
    supervisor, managed, fault, events = configured(monkeypatch, stdout=Reader())
    async def scenario():
        with pytest.raises(module.ProcessReadError, match='^binary process read unavailable$'):
            await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original', output_limit=4,
                                         require_tree_ownership=True)
        assert events.count('terminate checked') == 1 and events.count('fixture zero observation not native') == 1
        assert supervisor.active_count == 0 and not fault.cleanup_uncertain and not supervisor.cleanup_failed
        assert managed.process.stdout.closed and managed.process.stderr.closed
        await supervisor.close()
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


@pytest.mark.parametrize('phase', ['wait_none', 'wait_bool', 'wait_error', 'mismatch', 'missing_returncode'])
def test_original_binary_wait_not_a_typed_matching_parent_receipt_cannot_release_pipes_job_or_owner(tmp_path, monkeypatch, phase):
    def wait():
        if phase == 'wait_error':
            raise OSError('PRIVATE_WAIT')
        return {'wait_none': None, 'wait_bool': True, 'mismatch': 1}.get(phase, 0)
    supervisor, managed, fault, events = configured(monkeypatch, wait=wait)
    if phase == 'missing_returncode':
        del managed.process.returncode
    async def scenario():
        with pytest.raises(module.ProcessCleanupError):
            await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original', require_tree_ownership=True)
        assert supervisor.cleanup_failed and fault.cleanup_uncertain and supervisor.active_count == 1
        assert 'close Job' not in events and not managed.process.stdout.closed and not managed.process.stderr.closed
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('phase', ['throw', 'closed_then_throw'])
def test_original_binary_failed_individual_pipe_close_keeps_exact_source_and_closes_other_original_once(tmp_path, monkeypatch, phase, index):
    class Pipe(io.BytesIO):
        def __init__(self, target):
            super().__init__()
            self.target, self.closes = target, 0
        def close(self):
            self.closes += 1
            if self.target:
                if phase == 'closed_then_throw':
                    super().close()
                raise OSError('PRIVATE_PIPE_EXIT')
            super().close()
    pipes = [Pipe(index == 0), Pipe(index == 1)]
    supervisor, managed, fault, events = configured(monkeypatch, stdout=pipes[0], stderr=pipes[1])
    async def scenario():
        with pytest.raises(module.ProcessCleanupError) as error:
            await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original', require_tree_ownership=True)
        source = error.value.source
        assert source.resource is pipes[index] and source.close_attempted and not source.close_returned
        assert supervisor._unresolved_sources[id(source)] is source and fault.cleanup_uncertain
        assert pipes[1-index].closed and all(pipe.closes == 1 for pipe in pipes)
        assert supervisor.active_count == 1 and not supervisor._drainers[id(managed)].is_set()
        with pytest.raises(module.ProcessCleanupError):
            module._close_original_process_file(source)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert all(pipe.closes == 1 for pipe in pipes)
    try:
        asyncio.run(scenario())
    finally:
        for pipe in pipes:
            io.BytesIO.close(pipe)  # Fixture-only actual disposable stream teardown.


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_original_unregister_failure_even_after_active_removed_keeps_same_gate_and_owner_no_second_call(tmp_path, monkeypatch, phase):
    supervisor, managed, fault, events = configured(monkeypatch)
    original_unregister = supervisor._unregister
    calls = []
    def unregister(run_id, value):
        calls.append((run_id, value))
        if phase == 'after':
            original_unregister(run_id, value)
        raise OSError('PRIVATE_UNREGISTER_REPLY_LOST')
    monkeypatch.setattr(supervisor, '_unregister', unregister)
    async def scenario():
        with pytest.raises(module.ProcessCleanupError, match='^process unregister unproved$') as error:
            await supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original')
        owned = error.value.source
        assert supervisor._operations[id(managed)] is owned and owned.unregister_attempted and not owned.unregister_returned
        assert owned.drainer is not None and owned.drainer.is_set() is (phase == 'after')
        assert supervisor.active_count == (phase == 'before') and fault.cleanup_uncertain
        with pytest.raises(module.ProcessCleanupError):
            supervisor._retire_original(owned)
        with pytest.raises(module.ProcessCleanupError, match='quarantine'):
            await supervisor.close()
        assert calls == [('original', managed)]
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()


def test_original_cancel_joins_existing_wait_worker_no_second_wait_and_close_reuses_original_task(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    waits = []
    def wait():
        waits.append('same original wait')
        entered.set()
        if not release.wait(5):
            raise RuntimeError('fixture release unavailable')
        return 0
    supervisor, managed, fault, events = configured(monkeypatch, wait=wait)
    async def scenario():
        running = asyncio.create_task(supervisor.run_binary(['offline fixture'], cwd=tmp_path, run_id='original'))
        closing = None
        try:
            async with asyncio.timeout(2):
                while not entered.is_set():
                    if running.done():
                        await running
                        pytest.fail('fixture ended before actual wait entry')
                    await asyncio.sleep(0)
            closing = asyncio.create_task(supervisor.close())
            async with asyncio.timeout(2):
                while 'terminate' not in events:
                    await asyncio.sleep(0)
            assert not closing.done() and waits == ['same original wait']
            original_close = supervisor._close_task
            release.set()
            await asyncio.gather(running, closing)
            await supervisor.close()
            assert supervisor._close_task is original_close and supervisor.active_count == 0
            assert waits == ['same original wait'] and events.count('terminate') == 1 and not fault.cleanup_uncertain
        finally:
            release.set()
            await asyncio.gather(running, *(tuple([closing]) if closing is not None else ()), return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        managed.process.stdout.close()
        managed.process.stderr.close()
