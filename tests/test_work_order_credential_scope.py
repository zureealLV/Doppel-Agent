import pytest
from doppel_agent.tasks.work_orders import execution_settings


def test_work_order_replay_preserves_exact_opaque_credential_binding_metadata():
    profile = {'id': 'fixture', 'provider': 'openai', 'model': 'fixture-model', 'base_url': 'https://fixture.example/v1',
               'preset': 'custom', 'region': 'cn', 'plan': ' payg ', 'credential_revision': 'opaque-generation'}
    result = execution_settings({'profile_id': 'fixture', 'profiles': {'fixture': profile}})
    assert result['profiles']['fixture'] == profile
    assert execution_settings(result) == result


@pytest.mark.parametrize('field', ['preset', 'region', 'plan', 'credential_revision'])
def test_work_order_rejects_nonstring_credential_binding_metadata(field):
    profile = {'id': 'fixture', 'provider': 'mock', 'model': 'offline', field: {'secret': 'not-valid'}}
    with pytest.raises(ValueError):
        execution_settings({'profiles': {'fixture': profile}})


def test_work_order_does_not_admit_secret_fields_to_frozen_profile():
    with pytest.raises(ValueError, match='frozen profile fields'):
        execution_settings({'profiles': {'fixture': {'id': 'fixture', 'provider': 'mock', 'model': 'offline', 'api_key': 'SYNTHETIC'}}})
