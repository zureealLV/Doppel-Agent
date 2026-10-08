"""FIRST hard-process-death integration definitions, ALL UNRUN until whole S9.

Actual original Graph/Deep/SQLite/approval/patch/OS handle; ONLY provider HTTP
offline. This is not native EXE acceptance, invoice proof or hostile power loss.
"""

import asyncio
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from bench.runtime_process_evidence import ProcessIdentity
from doppel_agent.provider import AsyncOpenAICompatibleProvider
import doppel_agent.provider as provider_module
import doppel_agent.runtime.service as service_module
import doppel_agent.settings as settings_module
from doppel_agent.runtime.provider_recording import ProviderReceiptError
from doppel_agent.runtime.service import RunService


PRIVATE = 'PRIVATE_ORIGINAL_HARD_CRASH_SOURCE'
CALL = PRIVATE + '_CALL'
WATCHDOG = 30
ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / 'tests' / 'fixtures' / 'workbench_crash_worker.py'


def original_crash(tmp_path, mode, phase):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    ready, release = tmp_path / 'ready.json', tmp_path / 'release'
    # Explicit small environment: no credential/environment dump or inherited
    # provider configuration. Use current verified test interpreter at S9.
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    env.update(PYTHONPATH=os.pathsep.join((str(ROOT / 'src'), str(ROOT))), PYTHONUTF8='1',
               PYTHONDONTWRITEBYTECODE='1')
    identity = launcher_identity = None
    with (tmp_path / 'worker.log').open('wb') as output:
        process = subprocess.Popen([sys.executable, '-B', str(WORKER), str(workspace),
            str(ready), str(release), mode, phase], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            launcher_identity = ProcessIdentity(process.pid)
            deadline = time.monotonic() + WATCHDOG
            while not ready.exists():
                assert process.poll() is None, 'original crash worker exited before readiness; inspect disposable worker.log'
                if time.monotonic() >= deadline:
                    raise TimeoutError('original crash worker readiness unavailable')
                time.sleep(0.025)
            captured = json.loads(ready.read_text(encoding='utf-8'))
            assert captured['mode'] == mode and captured['phase'] == phase
            # Windows venv python.exe is a redirector: bind BOTH the original
            # Popen launcher and actual interpreter, with original parent lineage.
            # Never substitute the launcher PID for the runtime whose exit is tested.
            assert captured['pid'] == process.pid or (os.name == 'nt' and captured['parent_pid'] == process.pid)
            assert not launcher_identity.exited()
            identity = ProcessIdentity(captured['pid'])  # While gated alive, not PID-after-exit inference.
            assert not identity.exited()
            release.write_bytes(b'parent-bound-original-process')
            assert process.wait(timeout=WATCHDOG) == 73
            assert identity.exited()
            assert launcher_identity.exited()
            assert captured['requests'] == 1
            return workspace, captured
        finally:
            # Fixture teardown ONLY own Popen process; never process-name kill.
            if identity is not None and not identity.exited():
                release.write_bytes(b'fixture-teardown-release-original-runtime')
                process.wait(timeout=WATCHDOG)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=WATCHDOG)
            if identity is not None:
                identity.close()
            if launcher_identity is not None:
                launcher_identity.close()


@pytest.mark.parametrize('mode', ['graph', 'deep'])
@pytest.mark.parametrize('phase', ['paused', 'provider_started_before_response', 'patch_effect_before_seal'])
def test_actual_original_process_death_preserves_receipts_and_never_replays_unknown_effect(tmp_path, monkeypatch, mode, phase):
    if os.name != 'nt' and not hasattr(os, 'pidfd_open'):
        pytest.skip('requires original OS process identity; does not waive Windows S9 gate')
    workspace, captured = original_crash(tmp_path, mode, phase)
    original = captured['record']
    target = workspace / 'crash-patch.txt'
    assert target.exists() is (phase == 'patch_effect_before_seal')
    if target.exists():
        assert target.read_bytes() == PRIVATE.encode()

    async def scenario():
        requests = []
        async def handler(request):
            assert phase == 'paused'  # No model/effect replay for unknown original source.
            requests.append(json.loads(request.content))
            assert len(requests) == 1 and requests[0]['model'] == PRIVATE
            return httpx.Response(200, request=request, json={
                'choices': [{'message': {'content': PRIVATE}}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 3}})
        async def forbidden_network(*_args, **_kwargs):
            raise AssertionError('crash recovery attempted real HTTP')
        def forbidden_credentials(*_args, **_kwargs):
            raise AssertionError('crash recovery attempted credential or sync provider IO')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
            def original_provider(*args, **kwargs):
                return AsyncOpenAICompatibleProvider(*args, **kwargs, client=upstream)
            monkeypatch.setattr(service_module, 'AsyncOpenAICompatibleProvider', original_provider)
            monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden_network)
            monkeypatch.setattr(provider_module, 'urlopen', forbidden_credentials)
            monkeypatch.setattr(settings_module, '_protect', forbidden_credentials)
            monkeypatch.setattr(settings_module, '_unprotect', forbidden_credentials)
            service = RunService(workspace)
            await service.start()
            try:
                restored = await service.get(original['run_id'])
                assert restored['thread_id'] == original['thread_id']
                assert restored['profile_snapshot'] == original['profile_snapshot']
                assert service.scheduler.accepted_count == 0 and not service._providers and not requests
                events = await service.list_events(original['run_id'])
                assert not any(row['type'] in {'run.recovered_after_restart', 'deep.fallback'} for row in events)
                report = await service.run_report(original['run_id'])
                assert PRIVATE not in json.dumps(report)
                assert report['physical_drain_verified'] is report['cost']['billing_complete'] is False
                if phase == 'paused':
                    assert service._started and not service._provider_receipt_fault.broken
                    assert restored['status'] == 'interrupted' and restored['lease_active']
                    assert report['provider_usage']['selected']['known_units'] == 1
                    assert Fraction(report['cost']['currencies'][0]['amount']) == Fraction('0.000005')
                    await service.resume(original['run_id'], restored['metadata']['interrupts'][0]['id'], {'action': 'approve'})
                    result = await asyncio.wait_for(service.scheduler.wait(original['run_id']), WATCHDOG)
                    assert result.status == 'completed' and target.read_bytes() == PRIVATE.encode()
                    final = await service.get(original['run_id'])
                    assert final['thread_id'] == original['thread_id'] and not final['lease_active']
                    assert 'fallback_runtime' not in final['metadata'] and len(requests) == 1
                    report = await service.run_report(original['run_id'])
                    assert report['provider_usage']['selected']['known_units'] == 2
                    assert Fraction(report['cost']['currencies'][0]['amount']) == Fraction('0.00002125')
                    assert report['evidence']['patches']['items'][0]['confirmed_applied']
                else:
                    assert service._metadata_ready and not service._started and service._owner.held
                    assert service._provider_receipt_fault.broken
                    assert report['service']['execution_admission'] == 'quarantined'
                    with pytest.raises(ProviderReceiptError):
                        await service.create({'prompt': PRIVATE, 'mode': mode, 'permissions': {}})
                    with pytest.raises(ProviderReceiptError):
                        await service.resume(original['run_id'], 'unavailable', {'action': 'approve'})
                    if phase == 'provider_started_before_response':
                        assert report['provider_usage']['counts']['calls_unsettled'] == 1
                        assert report['provider_usage']['selected']['known_units'] == 0
                        assert not target.exists()  # Fixture observation, NOT absence of hidden provider effects.
                    else:
                        assert service._tool_startup_recovery.quarantined
                        assert report['provider_usage']['selected']['known_units'] == 1
                        patch, = report['evidence']['patches']['items']
                        assert patch['status'] == 'running' and patch['receipt_status'] == 'prepared'
                        assert not patch['confirmed_applied']
                        assert target.read_bytes() == PRIVATE.encode()  # No undo/replay/claimed success.
                    assert not requests and not service._providers
            finally:
                await service.close()
            assert service.cleanup_complete and not service._owner.held
    asyncio.run(scenario())
