"""Persist exact successful probe evidence, never model/tool/billing proof."""
import json
from datetime import datetime, timezone

import pytest

from doppel_agent.provider import ModelTurn, ProviderRequestError
from doppel_agent.settings import SettingsStore
from doppel_agent.web.server import ConsoleServer

KEY = 'synthetic-receipt-secret'
RAW_REPLY = 'synthetic raw reply that must never be stored'


def config(**changes):
    return {'provider': 'mock', 'preset': 'mock', 'base_url': '', 'model': 'fixture-model',
            'region': 'cn', 'plan': 'standard', **changes}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr('doppel_agent.settings._protect', lambda value: 'encrypted:' + value)
    monkeypatch.setattr('doppel_agent.settings._unprotect', lambda value: value.removeprefix('encrypted:'))
    server = ConsoleServer(('127.0.0.1', 0), tmp_path)
    saved = server.manager.settings.save_profile(config())
    try:
        yield server.manager, saved
    finally:
        server.server_close()


class TextProvider:
    def next_turn(self, messages, tools):
        assert tools == []
        return ModelTurn(RAW_REPLY)


def test_successful_saved_mock_probe_persists_exact_explicit_mock_receipt(fixture):
    manager, saved = fixture
    result = manager.probe({'profile_id': saved['id']})
    receipt = result['validation_receipt']
    assert receipt['protocol'] == 'openai-chat-completions'
    assert receipt['model'] == 'fixture-model'
    assert receipt['credential_revision'] == saved['credential_revision']
    assert receipt['kind'] == 'mock-probe'
    assert receipt['tools'] == 'unknown'
    assert datetime.fromisoformat(receipt['verified_at']).utcoffset() == timezone.utc.utcoffset(None)
    assert manager.settings.public()['validation_receipt'] == receipt
    assert SettingsStore(manager.settings.path).public()['validation_receipt'] == receipt


def test_successful_text_probe_stores_no_raw_body_or_credentials(fixture, monkeypatch):
    manager, _ = fixture
    saved = manager.settings.save_profile(config(provider='openai', preset='custom',
        base_url='https://fixture.invalid/v1'), api_key=KEY)
    monkeypatch.setattr(manager, '_provider', lambda _: TextProvider())
    response = manager.probe({'profile_id': saved['id']})
    assert response['ok'] is True and response['reply'] == RAW_REPLY
    receipt = response['validation_receipt']
    assert receipt['kind'] == 'text-probe'
    assert receipt['identity']['endpoint'] == ['https', 'fixture.invalid', 443, '/v1']
    raw = manager.settings.path.read_text()
    assert RAW_REPLY not in raw
    # Test encryption intentionally contains the synthetic key; receipt cannot.
    assert KEY not in json.dumps(receipt)
    assert 'billing' not in json.dumps(receipt)
    assert manager.settings.public()['validation_receipt'] == receipt


@pytest.mark.parametrize('draft', [{}, {'api_key': 'typed-replacement'}, {'model': 'different-model'}])
def test_unsaved_or_draft_or_typed_key_success_is_not_persisted_as_saved_validation(fixture, monkeypatch, draft):
    manager, saved = fixture
    monkeypatch.setattr(manager, '_provider', lambda _: TextProvider())
    body = draft if not draft else {'profile_id': saved['id'], **draft}
    result = manager.probe(body)
    assert result.get('validation_receipt') is None
    assert manager.settings.public().get('validation_receipt') is None


def test_name_only_preserves_receipt(fixture):
    manager, saved = fixture
    receipt = manager.probe({'profile_id': saved['id']})['validation_receipt']
    renamed = manager.settings.save_profile(config(name='renamed'))
    assert renamed['validation_receipt'] == receipt


@pytest.mark.parametrize('change', [{'model': 'new'}, {'region': 'global'}, {'plan': 'coding'},
    {'preset': 'other'}, {'base_url': 'http://127.0.0.1/v1'}])
def test_configuration_change_invalidates_previous_receipt(fixture, change):
    manager, saved = fixture
    assert manager.probe({'profile_id': saved['id']})['validation_receipt']
    changed = manager.settings.save_profile({**config(), **change})
    assert changed.get('validation_receipt') is None
    assert manager.settings.public().get('validation_receipt') is None


@pytest.mark.parametrize('forget', [False, True])
def test_key_replacement_or_removal_invalidates_previous_receipt(fixture, monkeypatch, forget):
    manager, _ = fixture
    cfg = config(provider='openai', preset='custom', base_url='https://fixture.invalid/v1')
    saved = manager.settings.save_profile(cfg, api_key=KEY)
    monkeypatch.setattr(manager, '_provider', lambda _: TextProvider())
    assert manager.probe({'profile_id': saved['id']})['validation_receipt']
    updated = manager.settings.save_profile(cfg, api_key='' if forget else 'replacement', forget_key=forget)
    assert updated.get('validation_receipt') is None


def test_late_success_cannot_validate_configuration_edited_during_original_probe(fixture, monkeypatch):
    manager, saved = fixture
    class EditingProvider:
        def next_turn(self, *_):
            manager.settings.save_profile(config(model='edited-while-in-flight'))
            return ModelTurn(RAW_REPLY)
    monkeypatch.setattr(manager, '_provider', lambda _: EditingProvider())
    result = manager.probe({'profile_id': saved['id']})
    assert result['ok'] is True
    assert result.get('validation_receipt') is None
    assert manager.settings.public().get('validation_receipt') is None


@pytest.mark.parametrize('status,kind,code', [
    (401, 'http_status', 'authentication'), (403, 'http_status', 'permission'),
    (429, 'rate_limited', 'rate_limited'), (404, 'http_status', 'model_or_endpoint'),
    (400, 'http_status', 'request_or_protocol'), (422, 'http_status', 'request_or_protocol'),
    (None, 'connection', 'network'), (None, 'timeout', 'timeout'),
    (None, 'protocol', 'request_or_protocol'), (503, 'http_status', 'unavailable'),
])
def test_known_probe_failure_has_fixed_safe_code_no_false_balance_claim(fixture, monkeypatch, status, kind, code):
    manager, saved = fixture
    class Failing:
        def next_turn(self, *_):
            raise ProviderRequestError(KEY + ' raw-provider-body balance??', kind=kind, attempts=1, status_code=status)
    monkeypatch.setattr(manager, '_provider', lambda _: Failing())
    with pytest.raises(RuntimeError, match='^provider_probe_' + code + '$') as caught:
        manager.probe({'profile_id': saved['id']})
    assert KEY not in str(caught.value) and 'balance' not in str(caught.value)
    assert manager.settings.public().get('validation_receipt') is None


def test_unknown_probe_failure_remains_unknown_fixed_error(fixture, monkeypatch):
    manager, saved = fixture
    class Failing:
        def next_turn(self, *_):
            raise RuntimeError(KEY + ' raw-provider-body')
    monkeypatch.setattr(manager, '_provider', lambda _: Failing())
    with pytest.raises(RuntimeError, match='^provider_probe_unavailable$'):
        manager.probe({'profile_id': saved['id']})
    assert manager.settings.public().get('validation_receipt') is None



def test_saved_config_change_has_persistent_invalidated_flag_and_reprobe_clears_it(fixture):
    manager, saved = fixture
    assert manager.settings.public()['validation_invalidated'] is False
    manager.probe({'profile_id': saved['id']})
    updated = manager.settings.save_profile(config(model='edited'))
    assert updated['validation_receipt'] is None
    assert updated['validation_invalidated'] is True
    assert SettingsStore(manager.settings.path).public()['validation_invalidated'] is True
    result = manager.probe({'profile_id': saved['id']})
    assert result['validation_receipt']['model'] == 'edited'
    assert manager.settings.public()['validation_invalidated'] is False


def test_settings_body_cannot_forge_validation_receipt(fixture):
    manager, saved = fixture
    forged = {**config(), 'validation_receipt': {'verified_at': 'fake', 'tools': 'supported'}}
    updated = manager.settings.save_profile(forged)
    assert updated['validation_receipt'] is None
    assert updated['validation_invalidated'] is False


def test_known_failure_keeps_original_owner_fault_and_is_not_retry_authority(tmp_path, monkeypatch):
    server = ConsoleServer(('127.0.0.1', 0), tmp_path, effect_admission=lambda: None)
    try:
        class Failing:
            def next_turn(self, *_):
                raise ProviderRequestError(KEY, kind='rate_limited', attempts=1, status_code=429)
        monkeypatch.setattr(server.manager, '_provider', lambda _: Failing())
        with pytest.raises(RuntimeError, match='^provider_probe_rate_limited$'):
            server.manager.probe({'provider': 'mock'})
        assert server.manager._operation_fault.is_set()
        with pytest.raises(RuntimeError):
            server.manager.probe({'provider': 'mock'})
    finally:
        server.server_close()
