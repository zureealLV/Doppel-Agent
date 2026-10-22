"""Save/default separation on synthetic local settings and legacy API."""
from copy import deepcopy

import pytest

from doppel_agent.settings import SettingsStore
from doppel_agent.web.server import ConsoleServer


def config(**changes):
    return {'provider': 'mock', 'preset': 'mock', 'base_url': '', 'model': 'mock', **changes}


def test_save_without_default_retains_default_and_reports_exact_saved_profile(tmp_path):
    store = SettingsStore(tmp_path / 'settings.json')
    first = store.save_profile(config(name='original'))
    saved = store.save_profile(config(name='saved only'), profile_id='new', make_default=False)
    assert saved['active_profile_id'] == first['active_profile_id']
    assert saved['saved_profile_id'] != first['active_profile_id']
    assert next(p for p in saved['profiles'] if p['id'] == saved['saved_profile_id'])['name'] == 'saved only'
    updated = store.save_profile(config(name='edited'), profile_id=saved['saved_profile_id'], make_default=False)
    assert updated['active_profile_id'] == first['active_profile_id']
    assert updated['saved_profile_id'] == saved['saved_profile_id']
    assert SettingsStore(store.path).public()['active_profile_id'] == first['active_profile_id']


def test_old_save_caller_still_changes_default_and_explicit_set_default_works(tmp_path):
    store = SettingsStore(tmp_path / 'settings.json')
    first = store.save_profile(config())
    second = store.save_profile(config(name='second'), profile_id='new')
    assert second['active_profile_id'] == second['saved_profile_id'] != first['active_profile_id']
    explicit = store.save_profile(config(name='original'), profile_id=first['active_profile_id'], make_default=True)
    assert explicit['active_profile_id'] == explicit['saved_profile_id'] == first['active_profile_id']


@pytest.mark.parametrize('value', [None, 0, 1, '', 'false', [], {}])
def test_invalid_make_default_fails_before_settings_mutation(tmp_path, value):
    store = SettingsStore(tmp_path / 'settings.json')
    store.save_profile(config())
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='^make_default_must_be_boolean$'):
        store.save_profile(config(), make_default=value)
    assert store.path.read_bytes() == before
    assert not store.failed


def test_manager_save_does_not_mutate_caller_payload_or_apply_to_conversation(tmp_path):
    server = ConsoleServer(('127.0.0.1', 0), tmp_path)
    try:
        first = server.manager.save_settings({'config': config(name='default')})
        conversation = server.manager.conversations.create('offline fixture')
        server.manager.conversations.set_profile(conversation['id'], first['active_profile_id'])
        data = {'config': config(name='only saved', api_key=''), 'profile_id': 'new', 'make_default': False}
        before = deepcopy(data)
        saved = server.manager.save_settings(data)
        assert data == before
        assert saved['active_profile_id'] == first['active_profile_id']
        assert saved['saved_profile_id'] != first['active_profile_id']
        assert server.manager.conversations.get(conversation['id'])['profile_id'] == first['active_profile_id']
    finally:
        server.server_close()


@pytest.mark.parametrize('value', [None, 0, 1, 'false', {}])
def test_manager_rejects_invalid_default_intent(tmp_path, value):
    server = ConsoleServer(('127.0.0.1', 0), tmp_path)
    try:
        with pytest.raises(ValueError, match='^make_default_must_be_boolean$'):
            server.manager.save_settings({'config': config(), 'make_default': value})
        assert not server.manager.settings.path.exists()
    finally:
        server.server_close()
