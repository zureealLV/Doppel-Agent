import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from mcp import types

from doppel_agent.api import create_app
from doppel_agent.mcp.client_manager import MCPCleanupError
from doppel_agent.provider import MockProvider


def write_skill(root, description='Find evidence'):
    path = root / 'skills' / 'evidence'
    path.mkdir(parents=True, exist_ok=True)
    (path/'SKILL.md').write_text(f'---\nname: evidence\ndescription: {description}\n---\nINSTRUCTIONS_NOT_IN_CATALOG',encoding='utf-8')


def test_skill_catalog_reload_is_atomic_and_progressive(tmp_path):
    write_skill(tmp_path)
    with TestClient(create_app(tmp_path,provider=MockProvider()),base_url='http://127.0.0.1') as client:
        response=client.get('/api/v1/skills')
        assert response.status_code==200
        assert response.json()['skills']==[{'name':'evidence','description':'Find evidence'}]
        assert 'INSTRUCTIONS_NOT_IN_CATALOG' not in response.text
        write_skill(tmp_path,'Changed description')
        assert client.get('/api/v1/skills').json()['skills'][0]['description']=='Find evidence'
        assert client.post('/api/v1/skills/reload').json()['skills'][0]['description']=='Changed description'
        (tmp_path/'skills/evidence/SKILL.md').write_text('invalid secret=DO_NOT_LEAK_ABCDEF123456',encoding='utf-8')
        bad=client.post('/api/v1/skills/reload')
        assert bad.status_code==422 and 'DO_NOT_LEAK' not in bad.text
        assert client.get('/api/v1/skills').json()['skills'][0]['description']=='Changed description'
        assert client.post('/api/v1/skills/reload',headers={'Origin':'http://evil.example'}).status_code==403


def configured_app(root):
    directory=root/'.doppel'
    directory.mkdir()
    (directory/'mcp.json').write_text(json.dumps({'servers':{'demo':{'transport':'stdio','command':sys.executable,'args':['PRIVATE_ARG'],'env_names':['PRIVATE_ENV']}}}),encoding='utf-8')
    return create_app(root,provider=MockProvider())


@pytest.mark.parametrize('headers, status', [({}, 200), ({'Origin': 'http://evil.example'}, 403)])
def test_compatibility_tools_get_never_connects_without_discovery(tmp_path, headers, status):
    app = configured_app(tmp_path)
    calls = []

    class Session:
        async def list_tools(self, *, params=None):
            calls.append('list_tools')
            return types.ListToolsResult(tools=[])

    @asynccontextmanager
    async def connector(_server):
        calls.append('connect')
        yield Session(), SimpleNamespace(
            protocol_version='2025-06-18',
            server_info=SimpleNamespace(name='fixture', version='1'), capabilities={},
        )

    app.state.run_service.mcp_manager.connector = connector
    with TestClient(app, base_url='http://127.0.0.1') as client:
        response = client.get('/api/v1/mcp/servers/demo/tools', headers=headers)
        assert calls == []  # Even an origin-less GET cannot enter the local connector.
        assert response.status_code == status
        if status == 200:
            assert response.json() == {'cache_state': 'missing', 'tools': []}
        assert 'PRIVATE_' not in response.text and sys.executable not in response.text


def test_compatibility_tools_get_reads_only_confirmed_discovery_cache(tmp_path):
    app = configured_app(tmp_path)
    calls = []

    class Session:
        async def list_tools(self, *, params=None):
            calls.append('list_tools')
            return types.ListToolsResult(tools=[types.Tool(
                name='read', inputSchema={'type': 'object'},
            )])

    @asynccontextmanager
    async def connector(_server):
        calls.append('connect')
        try:
            yield Session(), SimpleNamespace(
                protocol_version='2025-06-18',
                server_info=SimpleNamespace(name='fixture', version='1'), capabilities={},
            )
        finally:
            calls.append('close')

    service = app.state.run_service
    service.mcp_manager.connector = connector
    with TestClient(app, base_url='http://127.0.0.1') as client:
        discovery_url = '/api/v1/extensions/mcp/servers/demo/discovery'
        assert client.post(discovery_url, json={'action': 'refresh'}).status_code == 422
        assert client.post(discovery_url, json={'action': 'refresh', 'confirmed': True},
                           headers={'Origin': 'http://evil.example'}).status_code == 403
        assert calls == []
        refreshed = client.post(discovery_url, json={'action': 'refresh', 'confirmed': True})
        assert refreshed.status_code == 200
        assert calls == ['connect', 'list_tools']
        before = list(calls)
        response = client.get('/api/v1/mcp/servers/demo/tools')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['cache_state'] == 'cached'
        tool = response.json()['tools'][0]
        assert tool['logical_name'] == 'mcp__demo__read'
        assert tool['input_schema'] == {'type': 'object'}  # Preserve compatibility fields.
        assert tool['schema_hash']
        assert calls == before
        assert client.get('/api/v1/mcp/servers/demo/tools',
                          headers={'Origin': 'http://evil.example'}).status_code == 403
        service.mcp_manager._generations['demo'] += 1  # Fixture-only generation drift.
        stale = client.get('/api/v1/mcp/servers/demo/tools')
        assert stale.status_code == 200
        assert stale.json() == {'cache_state': 'stale_generation', 'tools': []}
        assert calls == before
    assert calls == [*before, 'close']


def test_mcp_inventory_never_connects_probe_and_catalog_are_explicit(tmp_path):
    app=configured_app(tmp_path)
    calls=[]
    class Session:
        async def list_tools(self, *, params=None):
            cursor=getattr(params,'cursor',None)
            calls.append(cursor)
            return types.ListToolsResult(tools=[types.Tool(name='first' if cursor is None else 'second',inputSchema={'type':'object'})],nextCursor='page2' if cursor is None else None)
    @asynccontextmanager
    async def connector(server):
        calls.append('connect')
        try:
            yield Session(),SimpleNamespace(protocol_version='2025-06-18',server_info=SimpleNamespace(name='fixture',version='1'),capabilities={})
        finally:
            calls.append('close')
    app.state.run_service.mcp_manager.connector=connector
    with TestClient(app,base_url='http://127.0.0.1') as client:
        response=client.get('/api/v1/mcp/servers')
        assert response.status_code==200
        assert response.json()['servers'][0]['name']=='demo'
        assert 'PRIVATE_' not in response.text and sys.executable not in response.text
        assert calls==[]
        for url in ('/api/v1/mcp/servers/missing/probe','/api/v1/mcp/servers/missing/tools'):
            method=client.post if url.endswith('probe') else client.get
            assert method(url).status_code==404
        assert client.post('/api/v1/mcp/servers/demo/probe',headers={'Origin':'http://evil.example'}).status_code==403
        assert calls==[]
        assert client.post('/api/v1/mcp/servers/demo/probe').json()['protocol_version']=='2025-06-18'
        assert client.get('/api/v1/mcp/servers/demo/tools').json()=={'cache_state':'missing','tools':[]}
        assert calls==['connect',None]  # Probe alone does not populate the catalog.
        refreshed=client.post('/api/v1/extensions/mcp/servers/demo/discovery',
                              json={'confirmed':True,'action':'refresh'})
        assert refreshed.status_code==200 and refreshed.json()['cache_state']=='cached'
        tools=client.get('/api/v1/mcp/servers/demo/tools').json()['tools']
        assert [t['logical_name'] for t in tools]==['mcp__demo__first','mcp__demo__second']
        assert tools[0]['schema_hash']
        assert calls==['connect',None,None,'page2']
    assert calls[-1]=='close'


def test_mcp_probe_failure_does_not_echo_transport_secrets(tmp_path):
    app=configured_app(tmp_path)
    service=app.state.run_service
    calls=[]
    @asynccontextmanager
    async def connector(server):
        calls.append('original opaque enter')
        raise RuntimeError('DO_NOT_LEAK_AUTH_TOKEN')
        yield
    app.state.run_service.mcp_manager.connector=connector
    try:
        with pytest.raises(MCPCleanupError), TestClient(app,base_url='http://127.0.0.1') as client:
            for url,body in (('/api/v1/mcp/servers/demo/probe',None),
                             ('/api/v1/extensions/mcp/servers/demo/discovery',
                              {'confirmed':True,'action':'refresh'})):
                response=client.post(url,json=body)
                assert response.status_code==502
                assert 'DO_NOT_LEAK' not in response.text
            cached=client.get('/api/v1/mcp/servers/demo/tools')
            assert cached.status_code==200 and cached.json()=={'cache_state':'missing','tools':[]}
            assert 'DO_NOT_LEAK' not in cached.text
            assert calls==['original opaque enter']
            assert service.mcp_manager.cleanup_failed
        assert service._owner.held and len(service.mcp_manager._connections)==1
    finally:
        service._owner.release()  # Disposable fixture teardown, NOT product closure proof.


def test_mcp_probe_timeout_retains_unreturned_opaque_startup(tmp_path):
    import asyncio
    app=configured_app(tmp_path)
    service=app.state.run_service
    service.mcp_discovery_timeout_seconds=0.02
    service.mcp_manager.connection_timeout_seconds=0.03
    closed=[]
    @asynccontextmanager
    async def connector(server):
        try:
            await asyncio.Event().wait()
            yield
        finally:
            closed.append(True)
    service.mcp_manager.connector=connector
    try:
        with pytest.raises(MCPCleanupError), TestClient(app,base_url='http://127.0.0.1') as client:
            response=client.post('/api/v1/mcp/servers/demo/probe')
            assert response.status_code==502  # Unknown cleanup takes precedence over timeout.
            assert closed==[True]  # Generator finally alone cannot certify opaque enter closure.
            assert service.mcp_manager.cleanup_failed and len(service.mcp_manager._connections)==1
        assert service._owner.held
    finally:
        service._owner.release()  # Disposable fixture teardown only.
