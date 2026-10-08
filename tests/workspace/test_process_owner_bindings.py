"""h3 FIRST existing Core/runtime/tool/native owner bindings, ALL UNRUN.

Local original memory callbacks/disposable stores ONLY, no Win32/process/native
launch or paid provider. Source retention is not full durable D integration proof.
"""

import asyncio
from types import SimpleNamespace

import pytest

from doppel_agent.core import Core
from doppel_agent.provider import MockProvider
from doppel_agent.runtime.provider_recording import ProviderReceiptFault
from doppel_agent.workspace.process_supervisor import ProcessCleanupError, ProcessSupervisor


class FalsySupervisor(ProcessSupervisor):
    def __bool__(self):
        return False  # Must not cause replacement of an explicitly supplied original.


def test_direct_core_default_supervisor_fault_retains_same_source_before_next_provider(tmp_path):
    fault = ProviderReceiptFault()
    core = Core(tmp_path, MockProvider(), process_failure=fault.mark_failed,
                process_cleanup_failure=fault.retain_cleanup)
    supervisor, source = core.process_supervisor, object()
    supervisor._mark_cleanup_failed(source)  # Original callback, no fake process execution.
    assert core.process_cleanup_uncertain and core.process_failed
    assert core._unresolved_process_sources[id(supervisor)] is supervisor
    assert supervisor._unresolved_sources[id(source)] is source
    assert fault._cleanup_sources[id(supervisor)] is supervisor and fault.cleanup_uncertain
    with pytest.raises(ProcessCleanupError, match='quarantine'):
        core.run('must not call provider')


def test_direct_core_injected_original_not_replaced_or_hooks_overwritten(tmp_path):
    fault = ProviderReceiptFault()
    supervisor = FalsySupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    before = supervisor._failure, supervisor._cleanup_failure
    core = Core(tmp_path, MockProvider(), process_supervisor=supervisor)
    assert core.process_supervisor is supervisor
    assert (supervisor._failure, supervisor._cleanup_failure) == before
    supervisor._mark_cleanup_failed(object())
    assert core.process_failed and core.process_cleanup_uncertain
    with pytest.raises(ProcessCleanupError):
        core.run('must not call provider')
    assert fault.cleanup_uncertain and core._unresolved_process_sources[id(supervisor)] is supervisor


@pytest.mark.parametrize('kind', ['legacy', 'deep'])
def test_original_runtime_default_process_supervisor_uses_same_receipt_fault(tmp_path, kind):
    from doppel_agent.runtime.legacy import LegacyRuntime
    from doppel_agent.runtime.deep import DeepAgentRuntime
    runtime_type = LegacyRuntime if kind == 'legacy' else DeepAgentRuntime
    fault = ProviderReceiptFault()
    runtime = runtime_type(tmp_path, MockProvider(), provider_receipt_fault=fault)
    supervisor, source = runtime.process_supervisor, object()
    supervisor._mark_cleanup_failed(source)
    assert supervisor._unresolved_sources[id(source)] is source
    assert fault._cleanup_sources[id(supervisor)] is supervisor and fault.cleanup_uncertain
    with pytest.raises(RuntimeError):
        fault.check_cleanup()


@pytest.mark.parametrize('kind', ['legacy', 'deep'])
def test_original_runtime_keeps_explicit_falsy_service_supervisor_and_original_hooks(tmp_path, kind):
    from doppel_agent.runtime.legacy import LegacyRuntime
    from doppel_agent.runtime.deep import DeepAgentRuntime
    fault = ProviderReceiptFault()
    supervisor = FalsySupervisor(failure=fault.mark_failed, cleanup_failure=fault.retain_cleanup)
    before = supervisor._failure, supervisor._cleanup_failure
    runtime_type = LegacyRuntime if kind == 'legacy' else DeepAgentRuntime
    runtime = runtime_type(tmp_path, MockProvider(), process_supervisor=supervisor, provider_receipt_fault=fault)
    assert runtime.process_supervisor is supervisor
    assert (supervisor._failure, supervisor._cleanup_failure) == before


def test_command_and_verification_keep_same_explicit_falsy_original_supervisor(tmp_path, monkeypatch):
    from doppel_agent.tools import run_command_tool
    from doppel_agent.workspace.verification import VerificationPipeline
    supervisor, calls = FalsySupervisor(), []
    async def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(exit_code=0, stdout='fixture', stderr='', supervision='local fixture not native')
    monkeypatch.setattr(supervisor, 'run', run)
    tool = run_command_tool(tmp_path, supervisor=supervisor)
    asyncio.run(tool.async_handler({'argv': ['fixture']}, 'original', 'call'))
    assert len(calls) == 1 and calls[0][1]['run_id'] == 'original'
    pipeline = VerificationPipeline(tmp_path, supervisor=supervisor)
    assert pipeline.supervisor is supervisor


def test_original_native_manager_retains_unpublished_core_process_source_and_fences_close(tmp_path):
    from doppel_agent.web.server import JobManager, LegacyOperationEvidenceError
    fault = ProviderReceiptFault()
    manager = JobManager(tmp_path, effect_admission=fault.check, effect_failure=fault.mark_failed)
    core = Core(tmp_path, MockProvider(), process_failure=manager._mark_cleanup_uncertain,
                process_cleanup_failure=manager._retain_process_cleanup)
    supervisor, source = core.process_supervisor, object()
    try:
        supervisor._mark_cleanup_failed(source)  # Core not yet in manager._cores: root callback still required.
        assert manager._unresolved_process_sources[id(supervisor)] is supervisor
        assert manager._operation_cleanup_uncertain.is_set() and fault.broken
        with pytest.raises(LegacyOperationEvidenceError):
            manager._assert_effect_admission()
        with pytest.raises(RuntimeError, match='legacy_operation_cleanup_unresolved'):
            manager.close_owned()
    finally:
        manager.pool.shutdown(wait=True, cancel_futures=False)  # Isolated fixture pool only.
