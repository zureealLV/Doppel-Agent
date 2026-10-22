"""Server discovery is explicit metadata, never a generation or key echo."""
import json
from threading import Event, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from doppel_agent.web.server import ConsoleServer, LegacyRequestClosing

KEY = 'synthetic-discovery-secret'
RESULT = {'status': 'ok', 'models': [{'id': 'fixture-model'}], 'metadata_only': True,
          'strategy': 'openai-list', 'pages': 1}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr('doppel_agent.settings._protect', lambda value: 'encrypted:' + value)
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda value: value.removeprefix('encrypted:'))
    server = ConsoleServer(('127.0.0.1', 0), tmp_path)
    calls = []
    def discover(base, key, strategy, **kwargs):
        calls.append((base, key, strategy))
        return {**RESULT, 'strategy': strategy, 'status': 'manual' if strategy == 'manual' else 'ok',
                'models': [] if strategy == 'manual' else RESULT['models']}
    monkeypatch.setattr('doppel_agent.web.server.discover_models', discover, raising=False)
    try:
        yield server, calls
    finally:
        server.server_close()


def config(**changes):
    return {'provider': 'openai', 'preset': 'custom', 'base_url': 'https://fixture.invalid/v1',
            'region': 'cn', 'plan': 'standard', **changes}


@pytest.mark.parametrize('base,preset,strategy', [
    ('https://api.deepseek.com', 'deepseek', 'openai-list'),
    ('https://api.deepseek.com/v1/', 'deepseek', 'openai-list'),
    ('https://API.OPENAI.com:443/v1/', 'openai', 'openai-list'),
    ('http://localhost:11434/v1', 'local', 'ollama-tags'),
    ('http://127.0.0.1:11434', 'local', 'ollama-tags'),
])
def test_known_endpoint_strategy_without_model_or_tariff(fixture, base, preset, strategy):
    server, calls = fixture
    result = server.manager.discover_models(config(base_url=base, preset=preset, api_key=KEY))
    assert result['metadata_only'] is True
    assert calls == [(base, KEY, strategy)]
    assert not server.manager.jobs


def test_custom_manual_default_does_not_decrypt_saved_key(fixture, monkeypatch):
    server, calls = fixture
    saved = server.manager.settings.save_profile({**config(), 'model': 'stored-model'}, api_key=KEY)
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('manual cannot decrypt'))
    result = server.manager.discover_models({'profile_id': saved['id']})
    assert result['status'] == 'manual'
    assert not calls or calls[0][1:] == ('', 'manual')


def test_custom_explicit_allowlisted_strategy_resolves_saved_key_not_model(fixture):
    server, calls = fixture
    saved = server.manager.settings.save_profile({**config(), 'model': ''}, api_key=KEY)
    result = server.manager.discover_models({'profile_id': saved['id'], 'strategy': 'openai-list'})
    assert result['models'] == [{'id': 'fixture-model'}]
    assert calls == [('https://fixture.invalid/v1', KEY, 'openai-list')]
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize('change', [{'base_url': 'https://other.invalid/v1'}, {'region': 'global'},
                                  {'plan': 'coding'}, {'preset': 'other'}, {'credential_revision': 'stale'}])
def test_saved_metadata_request_refuses_rebind_before_decrypt(fixture, monkeypatch, change):
    server, calls = fixture
    saved = server.manager.settings.save_profile({**config(), 'model': 'stored'}, api_key=KEY)
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda _: pytest.fail('cannot decrypt mismatched key'))
    with pytest.raises(ValueError, match='^credential_reentry_required$'):
        server.manager.discover_models({'profile_id': saved['id'], 'strategy': 'openai-list', **change})
    assert not calls


@pytest.mark.parametrize('strategy', ['malicious', 'https://evil.invalid', None, [], {}])
def test_invalid_strategy_never_enters_transport(fixture, strategy):
    server, calls = fixture
    with pytest.raises(ValueError, match='^invalid_discovery_strategy$'):
        server.manager.discover_models(config(strategy=strategy))
    assert not calls


def test_known_endpoint_cannot_be_forced_to_wrong_protocol(fixture):
    server, calls = fixture
    with pytest.raises(ValueError, match='^discovery_strategy_mismatch$'):
        server.manager.discover_models(config(base_url='https://api.openai.com/v1', strategy='ollama-tags'))
    assert not calls


@pytest.mark.parametrize('base', ['http://remote.invalid/v1', 'https://user:pass@fixture.invalid/v1',
                                 'https://fixture.invalid/v1?key=secret', 'file:///somewhere'])
def test_invalid_metadata_destination_is_controlled(fixture, base):
    server, calls = fixture
    with pytest.raises(ValueError, match='^invalid_discovery_destination$'):
        server.manager.discover_models(config(base_url=base, strategy='openai-list', api_key=KEY))
    assert not calls


def test_close_joins_exact_original_metadata_call_and_refuses_new_request(fixture):
    server, calls = fixture
    entered, release, closed = Event(), Event(), Event()
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return RESULT
    import doppel_agent.web.server as module
    original = module.discover_models
    module.discover_models = held
    request = Thread(target=lambda: server.manager.discover_models(config(strategy='openai-list')))
    closer = Thread(target=lambda: (server.manager.close_owned(), closed.set()))
    try:
        request.start()
        assert entered.wait(3)
        closer.start()
        assert not closed.wait(.05)
        with pytest.raises(LegacyRequestClosing):
            server.manager.discover_models(config(strategy='openai-list'))
        release.set()
        request.join(5)
        closer.join(5)
        assert closed.is_set()
    finally:
        release.set()
        module.discover_models = original


def test_http_discovery_route_and_fixed_error_without_secret(fixture, monkeypatch):
    server, calls = fixture
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def post(body):
        return urlopen(Request(base + '/api/models/discover', data=json.dumps(body).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'X-Doppel-UI': '1'}), timeout=5)
    try:
        with post({'config': config(strategy='openai-list', api_key=KEY)}) as response:
            assert json.load(response) == RESULT
        def broken(*args, **kwargs):
            raise RuntimeError(KEY + ' private-provider-error')
        monkeypatch.setattr('doppel_agent.web.server.discover_models', broken)
        with pytest.raises(HTTPError) as caught:
            post({'config': config(strategy='openai-list', api_key=KEY)})
        with caught.value as error:
            assert error.code == 503
            assert json.load(error) == {'error': 'provider_model_discovery_unavailable'}
    finally:
        server.shutdown()
        thread.join(5)


def test_effect_gate_runs_before_metadata_or_transport(fixture, monkeypatch):
    server, calls = fixture
    def denied():
        raise RuntimeError('original_owner_quarantined')
    server.manager._effect_admission = denied
    monkeypatch.setattr(server.manager.settings, 'public', lambda: pytest.fail('must not read after gate'))
    with pytest.raises(RuntimeError, match='^original_owner_quarantined$'):
        server.manager.discover_models({'profile_id': 'fixture', 'strategy': 'openai-list'})
    assert not calls


@pytest.mark.parametrize('strategy', ['manual', 'openai-list'])
def test_discovery_failure_is_fixed_even_for_manual_branch(fixture, monkeypatch, strategy):
    server, calls = fixture
    def broken(*args, **kwargs):
        raise RuntimeError(KEY + ' private-provider-error')
    monkeypatch.setattr('doppel_agent.web.server.discover_models', broken)
    with pytest.raises(RuntimeError, match='^provider_model_discovery_unavailable$'):
        server.manager.discover_models(config(strategy=strategy, api_key=KEY))
