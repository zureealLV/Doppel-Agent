"""S7 extension transport definitions. Local fixtures only; not native acceptance."""

import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp import types

from doppel_agent.api import create_app
from doppel_agent.api.routes.extensions import skill_snapshot, tool_snapshot
from doppel_agent.mcp.types import MCPToolDescriptor
from doppel_agent.mcp.client_manager import MCPCleanupError
from doppel_agent.provider import MockProvider


def extension_app(root):
    directory = root / '.doppel'
    directory.mkdir()
    (directory / 'mcp.json').write_text(json.dumps({'servers': {'demo': {
        'transport': 'stdio', 'command': sys.executable,
        'args': ['PRIVATE_ARG'], 'env_names': ['PRIVATE_ENV'],
    }}}), encoding='utf-8')
    app = create_app(root, provider=MockProvider())
    calls = []

    class Session:
        async def list_tools(self, *, params=None):
            calls.append('list_tools')
            return types.ListToolsResult(tools=[types.Tool(
                name='read', description='REMOTE_TEXT_NOT_TRUSTED',
                inputSchema={'type': 'object', 'PRIVATE_SCHEMA': 'NOT_EXPORTED'},
            )])

    @asynccontextmanager
    async def connector(_server):
        calls.append('connect')
        try:
            yield Session(), SimpleNamespace(
                protocol_version='2025-06-18',
                server_info=SimpleNamespace(name='fixture', version='1'),
                capabilities={'PRIVATE_CAPABILITY': 'NOT_EXPORTED'},
            )
        finally:
            calls.append('close')

    app.state.run_service.mcp_manager.connector = connector
    return app, calls


BASE = '/api/v1/extensions/mcp/servers/demo'


def test_tool_snapshot_has_explicit_count_and_utf8_text_budgets():
    tools = tuple(MCPToolDescriptor(
        f'mcp__demo__{i}', 'demo', f'tool{i}', '界' * 1000, '文' * 10000,
        {'PRIVATE_SCHEMA': 'NOT_EXPORTED'}, schema_hash='a' * 64,
    ) for i in range(101))
    snapshot = tool_snapshot('demo', 'cached', tools)
    assert snapshot['total'] == 101 and snapshot['limit'] == 100 and snapshot['truncated'] is True
    assert len(snapshot['tools']) == 100
    assert snapshot['tool_execution_verified'] is False
    for tool in snapshot['tools']:
        assert len(tool['title'].encode('utf-8')) <= 256
        assert len(tool['description'].encode('utf-8')) <= 512
        assert tool['text_truncated'] is True
        assert 'input_schema' not in tool
    empty = tool_snapshot('demo', 'cached', ())
    assert empty['total'] == 0 and empty['cache_state'] == 'cached'


def test_skill_snapshot_bounds_text_counts_and_never_returns_body_or_paths():
    catalog = {'skills': [{'name': f'skill{i}', 'description': '界' * 500,
                           'instructions': 'PRIVATE_BODY', 'path': 'PRIVATE_PATH'} for i in range(101)],
               'warnings': ['警' * 500] * 51}
    result = skill_snapshot(catalog)
    assert result['total'] == 101 and result['truncated'] is True and len(result['skills']) == 100
    assert result['warning_total'] == 51 and result['warning_truncated'] is True and len(result['warnings']) == 50
    assert 'PRIVATE_' not in json.dumps(result)
    assert all(len(skill['description'].encode('utf-8')) <= 512 and skill['text_truncated'] for skill in result['skills'])
    assert all(len(warning.encode('utf-8')) <= 256 for warning in result['warnings'])


def test_extension_entry_and_cache_miss_never_connect(tmp_path):
    app, calls = extension_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        inventory = client.get('/api/v1/extensions/mcp/servers')
        assert inventory.status_code == 200
        assert inventory.json()['servers'][0]['name'] == 'demo'
        assert inventory.headers['cache-control'] == 'no-store'
        assert 'PRIVATE_' not in inventory.text and sys.executable not in inventory.text
        cached = client.get(BASE + '/tools/cached')
        assert cached.status_code == 200
        assert cached.json()['cache_state'] == 'missing'
        assert cached.json()['total'] is None  # miss is not a proven empty server
        assert cached.json()['tools'] == []
        assert cached.json()['tool_execution_verified'] is False
        assert calls == []


def test_extension_skill_headers_and_atomic_reload_do_not_export_instructions(tmp_path):
    app, calls = extension_app(tmp_path)
    directory = tmp_path / 'skills' / 'evidence'
    directory.mkdir(parents=True)
    source = directory / 'SKILL.md'
    source.write_text('---\nname: evidence\ndescription: Find evidence\n---\nPRIVATE_INSTRUCTIONS', encoding='utf-8')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        headers = client.get('/api/v1/extensions/skills')
        assert headers.status_code == 200 and headers.headers['cache-control'] == 'no-store'
        assert headers.json()['skills'] == [{'name': 'evidence', 'description': 'Find evidence', 'text_truncated': False}]
        assert headers.json()['output']['instructions_returned'] is False
        assert 'PRIVATE_INSTRUCTIONS' not in headers.text
        source.write_text('---\nname: evidence\ndescription: Changed\n---\nPRIVATE_INSTRUCTIONS', encoding='utf-8')
        assert client.get('/api/v1/extensions/skills').json()['skills'][0]['description'] == 'Find evidence'
        reloaded = client.post('/api/v1/extensions/skills/reload', json={'confirmed': True, 'action': 'reload_skills'})
        assert reloaded.json()['skills'][0]['description'] == 'Changed'
        assert reloaded.json()['action'] == 'reload_skills'
        source.write_text('invalid secret=DO_NOT_LEAK_AUTH_TOKEN', encoding='utf-8')
        failed = client.post('/api/v1/extensions/skills/reload', json={'confirmed': True, 'action': 'reload_skills'})
        assert failed.status_code == 422 and 'DO_NOT_LEAK' not in failed.text
        assert client.get('/api/v1/extensions/skills').json()['skills'][0]['description'] == 'Changed'
        assert client.post('/api/v1/extensions/skills/reload', json={'confirmed': True, 'action': 'probe'}).status_code == 422
        assert client.post(BASE + '/discovery', json={'confirmed': True, 'action': 'reload_skills'}).status_code == 422
        assert calls == []


@pytest.mark.parametrize('raw', [
    '{}', '{"confirmed":false,"action":"refresh"}',
    '{"confirmed":1,"action":"refresh"}',
    '{"confirmed":true,"action":"execute"}',
    '{"confirmed":true,"confirmed":false,"action":"probe"}',
    '{"confirmed":true,"action":"probe","secret":"DO_NOT_LEAK"}',
    '{"confirmed":true,"action":NaN}', 'DO_NOT_LEAK', '["DO_NOT_LEAK"]',
])
def test_extension_discovery_requires_exact_explicit_confirmation(tmp_path, raw):
    app, calls = extension_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        response = client.post(BASE + '/discovery', content=raw,
                               headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert response.json() == {'detail': 'invalid_extension_request'}
        assert response.headers['cache-control'] == 'no-store'
        assert 'DO_NOT_LEAK' not in response.text
        assert calls == []


def test_explicit_probe_and_refresh_keep_cache_and_execution_claims_separate(tmp_path):
    app, calls = extension_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        probe = client.post(BASE + '/discovery', json={'confirmed': True, 'action': 'probe'})
        assert probe.status_code == 200
        assert probe.json() == {'server': 'demo', 'action': 'probe', 'probe_completed': True,
                                'protocol_version': '2025-06-18', 'tool_execution_verified': False}
        assert 'PRIVATE_' not in probe.text
        assert client.get(BASE + '/tools/cached').json()['cache_state'] == 'missing'
        refreshed = client.post(BASE + '/discovery', json={'confirmed': True, 'action': 'refresh'})
        assert refreshed.status_code == 200
        assert refreshed.json()['action'] == 'refresh'
        assert refreshed.json()['total'] == 1
        assert 'PRIVATE_SCHEMA' not in refreshed.text and 'input_schema' not in refreshed.text
        assert refreshed.json()['output'] == {'sensitive': True, 'globally_redacted': False,
                                               'remote_text_trusted': False}
        before = list(calls)
        cached = client.get(BASE + '/tools/cached').json()
        assert cached['cache_state'] == 'cached'
        assert cached['tools'][0]['logical_name'] == 'mcp__demo__read'
        assert len(cached['tools'][0]['schema_hash']) == 64
        assert calls == before
        # A fresh explicit refresh really invokes discovery rather than cache replay.
        client.post(BASE + '/discovery', json={'confirmed': True, 'action': 'refresh'})
        assert calls.count('list_tools') == 3
    assert calls[-1] == 'close'


def test_generation_changed_cache_never_reconnects_or_claims_empty(tmp_path):
    app, calls = extension_app(tmp_path)
    service = app.state.run_service
    with TestClient(app, base_url='http://127.0.0.1') as client:
        client.post(BASE + '/discovery', json={'confirmed': True, 'action': 'refresh'})
        before = list(calls)
        service.mcp_manager._generations['demo'] += 1  # fixture-only lifecycle drift
        response = client.get(BASE + '/tools/cached')
        assert response.json()['cache_state'] == 'stale_generation'
        assert response.json()['total'] is None
        assert response.json()['tools'] == []
        assert calls == before


def test_extension_limits_errors_origin_and_retained_owner_do_not_disclose_or_reopen(tmp_path):
    app, calls = extension_app(tmp_path)
    service = app.state.run_service
    with pytest.raises(MCPCleanupError), TestClient(app, base_url='http://127.0.0.1') as client:
        for url in (BASE + '/tools/cached', '/api/v1/extensions/mcp/servers'):
            bad = client.get(url, headers={'Origin': 'https://evil.invalid'})
            assert bad.status_code == 403 and bad.headers['cache-control'] == 'no-store'
        assert client.get(BASE + '/tools/cached?secret=DO_NOT_LEAK').status_code == 422
        assert client.post(BASE + '/discovery', content=b'x' * 1025).status_code == 413
        assert client.get(BASE.replace('/demo', '/missing') + '/tools/cached').status_code == 404
        assert calls == []

        @asynccontextmanager
        async def failed(_server):
            raise RuntimeError('DO_NOT_LEAK_AUTH_TOKEN')
            yield

        app.state.run_service.mcp_manager.connector = failed
        error = client.post(BASE + '/discovery', json={'confirmed': True, 'action': 'refresh'})
        assert error.status_code == 502 and 'DO_NOT_LEAK' not in error.text
        assert error.headers['cache-control'] == 'no-store'
        assert service.mcp_manager.cleanup_failed
    assert service._owner.held and len(service.mcp_manager._connections) == 1
    # Failed close retains metadata authority, but cannot reopen the connector.
    try:
        for path in (BASE + '/tools/cached', '/api/v1/extensions/mcp/servers'):
            response = client.get(path)
            assert response.status_code == 200
            if path.endswith('/cached'):
                assert response.json()['cache_state'] == 'missing' and response.json()['total'] is None
            assert service._owner.held and len(service.mcp_manager._connections) == 1
        assert calls == []
    finally:
        service._owner.release()  # Disposable retained owner, not successful service cleanup.


def test_known_closed_owner_passive_reads_refuse_without_reacquisition(tmp_path):
    app, calls = extension_app(tmp_path)
    service = app.state.run_service
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/api/v1/extensions/mcp/servers').status_code == 200
    assert not service._owner.held
    for path in (BASE + '/tools/cached', '/api/v1/extensions/mcp/servers'):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()['detail'] == 'extension_service_unavailable'
        assert not service._owner.held
    assert calls == []


def test_cancelled_skill_validation_keeps_original_owner_until_worker_join(tmp_path, monkeypatch):
    # Fixture-only gated worker, not a real filesystem/OS drain measurement.
    import asyncio
    import threading

    app, calls = extension_app(tmp_path)
    service = app.state.run_service
    started, release = threading.Event(), threading.Event()

    class GatedRegistry:
        def __init__(self, _workspace):
            pass

        def catalog(self):
            started.set()
            if not release.wait(5):
                raise TimeoutError('fixture worker was not released')
            return ()

        @property
        def warnings(self):
            return ()

    monkeypatch.setattr('doppel_agent.runtime.service.SkillRegistry', GatedRegistry)

    async def scenario():
        await service.start()
        operation = asyncio.create_task(service.skill_catalog(reload=True))
        closing = None
        try:
            assert await asyncio.to_thread(started.wait, 2)
            operation.cancel()
            for _ in range(12):
                await asyncio.sleep(0)
            closing = asyncio.create_task(service.close())
            for _ in range(12):
                await asyncio.sleep(0)
            assert not operation.done() and not closing.done()
            assert service._owner.held and service.cleanup_complete is False
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)
            if closing is not None:
                await closing
            else:
                await service.close()
        assert operation.cancelled()
        assert service._skill_registry is None  # cancelled candidate not installed
        assert not service._owner.held and service.cleanup_complete is True

    asyncio.run(scenario())
    assert calls == []
