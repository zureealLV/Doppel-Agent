"""S9-only original service crash fixture. NEVER run during construction.

Only the HTTP transport is offline. Hard exit is gated so parent binds the live
original process OS handle first; no orderly service close or fake checkpoint.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import time

import httpx
import pytest

from doppel_agent.provider import AsyncOpenAICompatibleProvider
import doppel_agent.provider as provider_module
import doppel_agent.runtime.service as service_module
import doppel_agent.settings as settings_module
from doppel_agent.runtime.service import RunService
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger


PRIVATE = 'PRIVATE_ORIGINAL_HARD_CRASH_SOURCE'
CALL = PRIVATE + '_CALL'
WATCHDOG = 30


async def main(workspace, ready, release, mode, phase):
    workspace, ready, release = map(Path, (workspace, ready, release))
    assert workspace.is_dir() and not (workspace / '.doppel').exists()
    target = workspace / 'crash-patch.txt'
    journal, scope = [], {}
    bound = asyncio.Event()
    service = RunService(workspace)

    def hard_exit(record):
        # Same original record, not manufactured runtime/ledger completion.
        payload = {'pid': os.getpid(), 'parent_pid': os.getppid(), 'phase': phase, 'mode': mode, 'record': record,
                   'requests': len(journal), 'target_present': target.exists()}
        temporary = ready.with_suffix('.pending')
        temporary.write_text(json.dumps(payload), encoding='utf-8')
        temporary.replace(ready)
        deadline = time.monotonic() + WATCHDOG
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('parent did not bind original crash process')
            time.sleep(0.01)
        os._exit(73)  # Intentional actual abrupt death, no finally/service.close.

    async def handler(request):
        journal.append(json.loads(request.content))
        await bound.wait()
        if phase == 'provider_started_before_response':
            hard_exit(await service.get(scope['record']['run_id']))
        assert len(journal) == 1  # Any second model turn before crash is a bug.
        return httpx.Response(200, request=request, json={
            'choices': [{'message': {'content': '', 'tool_calls': [{'id': CALL, 'type': 'function',
                'function': {'name': 'propose_patch', 'arguments': json.dumps({
                    'changes': [{'path': target.name, 'content': PRIVATE}]})}}]}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 1}})

    async def forbidden_network(*_args, **_kwargs):
        raise AssertionError('crash fixture attempted real network')

    def forbidden_credentials(*_args, **_kwargs):
        raise AssertionError('crash fixture attempted credential or sync provider IO')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        with pytest.MonkeyPatch.context() as patch:
            def original_provider(*args, **kwargs):
                return AsyncOpenAICompatibleProvider(*args, **kwargs, client=upstream)
            patch.setattr(service_module, 'AsyncOpenAICompatibleProvider', original_provider)
            patch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden_network)
            patch.setattr(provider_module, 'urlopen', forbidden_credentials)
            patch.setattr(settings_module, '_protect', forbidden_credentials)
            patch.setattr(settings_module, '_unprotect', forbidden_credentials)
            saved = await asyncio.to_thread(service.settings.save_profile, {
                'provider': 'openai', 'preset': 'openai', 'base_url': 'https://fixture.invalid/v1',
                'model': PRIVATE, 'billing_tariff_confirmed': True, 'billing_tariff': {
                    'version': 1, 'currency': 'CNY', 'effective_date': '2026-10-01',
                    'source_kind': 'offline_fixture', 'source_reference': PRIVATE,
                    'unit_tokens': 1000000, 'billing_basis': 'input_output_inclusive',
                    'reasoning_basis': 'included_in_output',
                    'rates': {'input': '1.25', 'output': '2.5', 'cached_input': None, 'uncached_input': None}}})
            await service.start()
            record, _ = await service.create({'prompt': PRIVATE, 'mode': mode,
                'permissions': {'workspace_write': True}, 'deadline_seconds': WATCHDOG})
            scope['record'] = record
            bound.set()
            first = await asyncio.wait_for(service.scheduler.wait(record['run_id']), WATCHDOG)
            pending = await service.get(record['run_id'])
            assert first.status == pending['status'] == 'interrupted' and not target.exists()
            assert pending['lease_active'] and 'fallback_runtime' not in pending['metadata']
            assert len(journal) == 1 and saved['active_profile_id']
            if phase == 'paused':
                hard_exit(pending)
            assert phase == 'patch_effect_before_seal'
            original_finish = ToolExecutionLedger._finish_patch
            def finish(ledger, run_id, call_id, receipt, **kwargs):
                if run_id == record['run_id'] and call_id == CALL and not kwargs.get('error'):
                    assert receipt.status == 'applied' and target.read_bytes() == PRIVATE.encode()
                    assert ledger.execution_status(run_id, call_id) == 'running'
                    hard_exit(pending)
                return original_finish(ledger, run_id, call_id, receipt, **kwargs)
            patch.setattr(ToolExecutionLedger, '_finish_patch', finish)
            await service.resume(record['run_id'], pending['metadata']['interrupts'][0]['id'], {'action': 'approve'})
            await asyncio.wait_for(service.scheduler.wait(record['run_id']), WATCHDOG)
            raise AssertionError('original patch did not enter pre-seal crash seam')


if __name__ == '__main__':
    assert len(sys.argv) == 6
    asyncio.run(main(*sys.argv[1:]))
