"""h3 FIRST ORIGINAL snapshot/thread/resume sources, ALL UNRUN.

Local ctypes API proxy ONLY. No Windows handles, process body, Job/native proof.
"""

import ctypes
import io
from types import SimpleNamespace

import pytest

import doppel_agent.workspace.process_supervisor as module


def observe_resume(monkeypatch, *, phase='healthy', failed_close=None):
    events = []
    class Function:
        def __init__(self, name):
            self.name = name
            self.argtypes = self.restype = None
        def __call__(self, *args):
            events.append((self.name, args[0] if args else None))
            if self.name == 'CreateToolhelp32Snapshot':
                if phase == 'snapshot_opaque':
                    raise OSError('PRIVATE_SNAPSHOT_LOST_RETURN')
                return {'snapshot_null': 0, 'snapshot_invalid': ctypes.c_void_p(-1).value,
                        'snapshot_unusable': 'PRIVATE_SNAPSHOT_HANDLE'}.get(phase, 101)
            if self.name == 'Thread32First':
                if phase == 'enumeration_error':
                    raise OSError('PRIVATE_ENUMERATION_ERROR')
                entry = args[1]._obj
                entry.th32OwnerProcessID, entry.th32ThreadID = 77, 88
                return 'PRIVATE_ENUM_ACK' if phase == 'enumeration_unusable' else 1
            if self.name == 'Thread32Next':
                return 0
            if self.name == 'OpenThread':
                if phase == 'thread_opaque':
                    raise OSError('PRIVATE_OPEN_THREAD_LOST_RETURN')
                return {'thread_null': 0, 'thread_unusable': 'PRIVATE_THREAD_HANDLE'}.get(phase, 202)
            if self.name == 'ResumeThread':
                if phase == 'resume_opaque':
                    raise OSError('PRIVATE_RESUME_LOST_RETURN')
                return {'resume_zero': 0, 'resume_failure': 0xFFFFFFFF,
                        'resume_bool': True, 'resume_unusable': 'PRIVATE_RESUME_ACK'}.get(phase, 1)
            if self.name == 'CloseHandle':
                if args[0] == failed_close:
                    if phase == 'close_false':
                        return 0
                    if phase == 'close_unusable':
                        return 'PRIVATE_CLOSE_ACK'
                    raise OSError('PRIVATE_HANDLE_CLOSE_UNKNOWN')
                return 1
            raise AssertionError('unexpected original API')
    kernel = SimpleNamespace(**{name: Function(name) for name in (
        'CreateToolhelp32Snapshot', 'Thread32First', 'Thread32Next', 'OpenThread', 'ResumeThread', 'CloseHandle')})
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 18, raising=False)
    return kernel, events


@pytest.mark.parametrize('phase', ['snapshot_opaque', 'snapshot_unusable', 'thread_opaque', 'thread_unusable',
                                   'resume_opaque', 'resume_bool', 'resume_unusable'])
def test_original_resume_opaque_or_unusable_attempt_retains_exact_sources_without_invented_cleanup(monkeypatch, phase):
    kernel, events = observe_resume(monkeypatch, phase=phase)
    with pytest.raises(module.ProcessCleanupError) as error:
        module._resume_suspended_primary_thread(77)
    source = error.value.source
    assert source.kernel32 is kernel and source.process_id == 77 and source.cleanup_uncertain
    target = source.snapshot if phase.startswith('snapshot') else source.thread
    assert target.factory_attempted
    assert target.factory_returned is (not phase.endswith('opaque') or phase == 'resume_opaque')
    if phase.startswith('resume'):
        assert source.resume_attempted and source.resume_returned is (phase != 'resume_opaque')
        assert source.snapshot.close_returned and source.thread.close_returned
    else:
        assert not target.close_attempted  # Unreturned/unusable handle isn't authority to close.
        assert all(name != 'ResumeThread' for name, _ in events)
    assert 'PRIVATE_' not in str(error.value)


@pytest.mark.parametrize('handle', [101, 202])
@pytest.mark.parametrize('phase', ['close_false', 'close_throw', 'close_unusable', 'closed_then_throw'])
def test_original_resume_failed_individual_close_retains_same_handle_and_attempt_no_second_close(monkeypatch, handle, phase):
    _, events = observe_resume(monkeypatch, phase=phase, failed_close=handle)
    with pytest.raises(module.ProcessCleanupError) as error:
        module._resume_suspended_primary_thread(77)
    source = error.value.source
    target = source.snapshot if handle == 101 else source.thread
    assert source.cleanup_uncertain and target.handle == handle and target.cleanup_uncertain
    assert target.close_attempted and not target.close_returned
    before = list(events)
    with pytest.raises(module.ProcessCleanupError):
        module._close_original_resume_handle(source, target)
    assert events == before
    assert len([value for value in events if value == ('CloseHandle', handle)]) == 1
    if handle == 101:
        assert not source.thread.factory_attempted and not source.resume_attempted
    else:
        assert source.snapshot.close_returned and source.resume_attempted and source.resume_returned


@pytest.mark.parametrize('phase', ['snapshot_null', 'snapshot_invalid', 'thread_null', 'enumeration_error',
                                   'enumeration_unusable', 'resume_zero', 'resume_failure'])
def test_original_resume_known_refusal_after_known_individual_closes_not_resource_unknown(monkeypatch, phase):
    _, events = observe_resume(monkeypatch, phase=phase)
    with pytest.raises(module.ProcessSupervisionError) as error:
        module._resume_suspended_primary_thread(77)
    source = error.value.source
    assert not source.cleanup_uncertain
    if source.snapshot.usable:
        assert source.snapshot.close_returned
    if source.thread.usable:
        assert source.thread.close_returned
    assert len([value for value in events if value == ('CloseHandle', 101)]) <= 1
    assert len([value for value in events if value == ('CloseHandle', 202)]) <= 1
    assert 'PRIVATE_' not in str(error.value)


def test_original_resume_healthy_snapshot_closed_before_thread_open_and_exact_resume_once(monkeypatch):
    _, events = observe_resume(monkeypatch)
    module._resume_suspended_primary_thread(77)
    assert events == [('CreateToolhelp32Snapshot', 4), ('Thread32First', 101), ('Thread32Next', 101),
                      ('CloseHandle', 101), ('OpenThread', 2), ('ResumeThread', 202), ('CloseHandle', 202)]


@pytest.mark.parametrize('phase', ['resume_opaque', 'close_throw'])
def test_original_start_keeps_returned_job_and_independent_resume_source_without_resume_retry(tmp_path, monkeypatch, phase):
    root = tmp_path  # Real WindowsPath before platform simulation.
    _, events = observe_resume(monkeypatch, phase=phase, failed_close=202 if phase == 'close_throw' else None)
    process = SimpleNamespace(pid=77, _handle=333, stdout=io.BytesIO(), stderr=io.BytesIO())
    job = SimpleNamespace()
    monkeypatch.setattr(module.os, 'name', 'nt')
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(module, '_WindowsJob', lambda *args: job)
    def forbidden(*args, **kwargs):
        pytest.fail('no fallback/kill/wait/second native operation after unknown original resume')
    process.kill = process.wait = forbidden
    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    try:
        with pytest.raises(module.ProcessCleanupError) as error:
            module.ProcessSupervisor._start(['fixture'], root, {}, None, None, require_tree_ownership=True)
        source, managed = error.value.source, error.value.managed
        assert managed.process is process and managed.job is job
        assert managed.startup.job is job and managed.startup.job_returned
        assert managed.startup.original_sources[id(source)] is source
        assert source.snapshot.close_returned and source.thread.handle == 202 and source.cleanup_uncertain
        assert len([name for name, _ in events if name == 'ResumeThread']) == 1
        assert not process.stdout.closed and not process.stderr.closed
    finally:
        process.stdout.close()
        process.stderr.close()  # Fixture-only disposal, NOT original process/tree cleanup.
