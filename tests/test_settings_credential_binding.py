"""Synthetic credential ownership: no real settings, key, balance or paid calls."""
import json
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from doppel_agent.settings import SettingsStore
from doppel_agent.provider import Message, OpenAICompatibleProvider, ProviderRequestError
from doppel_agent.web.server import ConsoleServer

KEY = 'synthetic-only-sentinel'


def config(**changes):
    return dict(provider='openai', preset='fixture', base_url='https://fixture.invalid/v1',
                model='fixture-model', region='cn', plan='standard', **changes)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr('doppel_agent.settings._protect', lambda key: 'encrypted:' + key)
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda key: key.removeprefix('encrypted:'))
    result = SettingsStore(tmp_path / 'synthetic-settings.json')
    result.save_profile(config(), api_key=KEY)
    return result


@pytest.mark.parametrize('change', [
    {'base_url': 'https://other.invalid/v1'}, {'base_url': 'https://fixture.invalid:8443/v1'},
    {'base_url': 'https://fixture.invalid/v2'}, {'base_url': 'https://fixture.invalid/v1/coding'},
    {'base_url': 'http://127.0.0.1/v1'}, {'region': 'global'}, {'plan': 'coding'},
    {'preset': 'other-provider'}, {'provider': 'mock'},
])
def test_empty_key_identity_change_is_rejected_atomically(store, change):
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        store.save_profile({**config(), **change})
    assert store.path.read_bytes() == before
    assert store.api_key() == KEY
    assert not store.failed
    assert KEY not in json.dumps(store.public())


def test_name_model_edit_and_equivalent_endpoint_keep_bound_key(store):
    saved = store.save_profile({**config(), 'name': 'Renamed', 'model': 'another-model',
                                'base_url': 'https://FIXTURE.invalid:443/v1/'})
    assert saved['api_key_saved'] is True
    assert saved['region'] == 'cn'
    assert saved['plan'] == 'standard'
    assert store.api_key() == KEY


def test_replacement_or_explicit_removal_permits_identity_change(store):
    changed = {**config(), 'plan': 'coding'}
    store.save_profile(changed, api_key='synthetic-replacement')
    assert store.api_key() == 'synthetic-replacement'
    store.save_profile({**changed, 'region': 'global'}, forget_key=True)
    assert store.api_key() == ''


def test_legacy_unbound_key_is_not_silently_rebound(store):
    data = json.loads(store.path.read_text())
    data['profiles'][0].pop('credential_binding', None)
    store.path.write_text(json.dumps(data))
    assert store.profile()['api_key'] == KEY
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        store.save_profile({**config(), 'plan': 'coding'})
    assert store.path.read_bytes() == before


def test_corrupt_binding_never_decrypts(store, monkeypatch):
    data = json.loads(store.path.read_text())
    data['profiles'][0]['base_url'] = 'https://other.invalid/v1'
    store.path.write_text(json.dumps(data))
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('must not decrypt'))
    assert store.public()['credential_reentry_required'] is True
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        store.profile()


@pytest.mark.parametrize('change', [{'plan': 'coding'}, {'region': 'global'},
    {'preset': 'other'}, {'base_url': 'https://other.invalid/v1'}])
def test_probe_and_run_provider_resolve_same_ownership_before_decrypt(store, monkeypatch, change):
    server = ConsoleServer(('127.0.0.1', 0), store.path.parent)
    try:
        server.manager.settings = store
        monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('must not decrypt'))
        request = {'profile_id': store.public()['id'], **change}
        with pytest.raises(ValueError, match='^credential_reentry_required$'):
            server.manager._provider(request)
    finally:
        server.server_close()


def test_unsaved_override_with_replacement_key_never_decrypts_old(store, monkeypatch):
    server = ConsoleServer(('127.0.0.1', 0), store.path.parent)
    try:
        server.manager.settings = store
        monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('must not decrypt'))
        provider = server.manager._provider({'profile_id': store.public()['id'],
            'base_url': 'https://other.invalid/v1', 'api_key': 'synthetic-replacement'})
        assert provider.api_key == 'synthetic-replacement'
    finally:
        server.server_close()


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_authenticated_redirect_never_reaches_other_origin(status):
    hits = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            hits.append(self.headers.get('Authorization'))
            self.send_response(200)
            self.end_headers()
        do_POST = do_GET
        def log_message(self, *_): pass

    target = ThreadingHTTPServer(('127.0.0.1', 0), Target)

    class Source(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.send_response(status)
            self.send_header('Location', f'http://127.0.0.1:{target.server_port}/other')
            self.end_headers()
        def log_message(self, *_): pass

    source = ThreadingHTTPServer(('127.0.0.1', 0), Source)
    threads = [Thread(target=server.serve_forever, daemon=True) for server in (source, target)]
    for thread in threads:
        thread.start()
    try:
        provider = OpenAICompatibleProvider(f'http://127.0.0.1:{source.server_port}', 'fixture', KEY)
        with pytest.raises(ProviderRequestError):
            provider.next_turn([Message('user', 'offline fixture')], [])
        assert hits == []
    finally:
        for server in (source, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(3)


def test_credential_revision_changes_only_on_replacement_or_removal(store):
    first = store.public()['credential_revision']
    assert first and KEY not in first
    assert store.save_profile({**config(), 'model': 'new-model'})['credential_revision'] == first
    replaced = store.save_profile(config(), api_key='replacement')['credential_revision']
    assert replaced != first
    removed = store.save_profile(config(), forget_key=True)['credential_revision']
    assert removed != replaced


def test_omitted_region_plan_preserves_legacy_callers_contract(store):
    legacy = config()
    legacy.pop('region')
    legacy.pop('plan')
    saved = store.save_profile(legacy)
    assert (saved['region'], saved['plan']) == ('cn', 'standard')


@pytest.mark.parametrize('mutation', ['endpoint', 'replacement', 'removal'])
def test_native_snapshot_never_uses_new_credentials_or_cached_old_credentials(store, monkeypatch, mutation):
    from doppel_agent.runtime.service import RunService
    service = RunService(store.path.parent)
    service.settings = store
    profile = store.public()
    snapshot = service._freeze_public_profile(profile)
    first = service._provider(profile['id'], 'legacy', snapshot)
    assert first.api_key == KEY
    if mutation == 'endpoint':
        store.save_profile({**config(), 'base_url': 'https://other.invalid/v1'}, api_key='new-key')
    elif mutation == 'replacement':
        store.save_profile(config(), api_key='new-key')
    else:
        store.save_profile(config(), forget_key=True)
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('must not decrypt'))
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        service._provider(profile['id'], 'legacy', snapshot)


def test_native_cache_is_revision_bound_and_new_snapshot_uses_new_key(store):
    from doppel_agent.runtime.service import RunService
    service = RunService(store.path.parent)
    service.settings = store
    profile = store.public()
    first = service._provider(profile['id'], 'legacy')
    store.save_profile(config(), api_key='new-key')
    second = service._provider(profile['id'], 'legacy')
    assert first is not second
    assert first.api_key == KEY
    assert second.api_key == 'new-key'


def test_native_snapshot_freezes_contract_revision_without_secrets(store):
    from doppel_agent.runtime.service import RunService
    snapshot = RunService._freeze_public_profile(store.public())
    assert snapshot['credential_revision'] == store.public()['credential_revision']
    assert snapshot['region'] == 'cn'
    assert snapshot['plan'] == 'standard'
    assert snapshot['preset'] == 'fixture'
    assert KEY not in json.dumps(snapshot)


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_async_injected_native_http_client_cannot_enable_credential_redirect(status):
    import asyncio
    import httpx
    from doppel_agent.provider import AsyncOpenAICompatibleProvider

    async def scenario():
        destinations = []
        async def handler(request):
            destinations.append(str(request.url))
            if request.url.host == 'source.invalid':
                return httpx.Response(status, headers={'Location': 'https://target.invalid/other'})
            return httpx.Response(200, json={'choices': [{'message': {'content': 'wrong-origin'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            provider = AsyncOpenAICompatibleProvider('https://source.invalid', 'fixture', KEY,
                                                     client=client, max_attempts=1)
            with pytest.raises(ProviderRequestError):
                await provider.anext_turn([Message('user', 'offline fixture')], [])
        assert destinations == ['https://source.invalid/chat/completions']
    asyncio.run(scenario())


def test_explicit_port_zero_is_not_equivalent_to_default_port(store):
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        store.save_profile({**config(), 'base_url': 'https://fixture.invalid:0/v1'})


def test_legacy_snapshot_without_revision_only_uses_legacy_bound_credential(store):
    from doppel_agent.runtime.service import RunService
    data = json.loads(store.path.read_text())
    data['profiles'][0].pop('credential_revision')
    store.path.write_text(json.dumps(data))
    service = RunService(store.path.parent)
    service.settings = store
    public = store.public()
    snapshot = {key: public[key] for key in ('id', 'provider', 'model', 'base_url')}
    assert service._provider(public['id'], 'legacy', snapshot).api_key == KEY
    store.save_profile(config(), api_key='new-key')
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        service._provider(public['id'], 'legacy', snapshot)



def test_http_save_probe_and_run_reject_credential_rebind_without_secret_leak(store, monkeypatch):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    server = ConsoleServer(('127.0.0.1', 0), store.path.parent)
    server.manager.settings = store
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = store.path.read_bytes()
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('must not decrypt'))
    try:
        changed = {**config(), 'plan': 'coding', 'profile_id': store.public()['id']}
        for route in ('/api/settings', '/api/probe', '/api/runs'):
            body = {'config': changed, 'profile_id': changed['profile_id'], 'prompt': 'offline fixture'}
            request = Request(f'http://127.0.0.1:{server.server_port}{route}',
                data=json.dumps(body).encode(), method='POST',
                headers={'Content-Type': 'application/json', 'X-Doppel-UI': '1'})
            with pytest.raises(HTTPError) as caught:
                urlopen(request, timeout=5)
            with caught.value as error:
                assert error.code == 400
                assert json.load(error) == {'error': 'credential_reentry_required'}
        assert store.path.read_bytes() == before
        assert not server.manager.jobs
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)



def test_explicit_replacement_run_does_not_decrypt_original_for_display_model(store, monkeypatch):
    from doppel_agent.provider import MockProvider
    server = ConsoleServer(('127.0.0.1', 0), store.path.parent)
    try:
        server.manager.settings = store
        monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('must not decrypt'))
        monkeypatch.setattr('doppel_agent.web.server.OpenAICompatibleProvider', lambda *_: MockProvider())
        response = server.manager.submit({'prompt': 'offline fixture', 'config': {
            'profile_id': store.public()['id'], 'model': 'new-model', 'api_key': 'synthetic-replacement'}})
        assert response['run_id']
    finally:
        server.server_close()


@pytest.mark.parametrize('change', [{'preset': 'other'}, {'region': 'global'}, {'plan': 'coding'}])
def test_contract_change_with_replacement_key_clears_old_tariff(store, change):
    from test_billing_tariff import tariff
    before = store.save_profile({**config(), 'billing_tariff': tariff(), 'billing_tariff_confirmed': True})
    assert before['billing_tariff']
    saved = store.save_profile({**config(), **change}, api_key='synthetic-replacement')
    assert saved['billing_tariff'] is None
    confirmed = store.save_profile({**config(), **change,
        'billing_tariff': tariff(), 'billing_tariff_confirmed': True})
    assert confirmed['billing_tariff'] == before['billing_tariff']
