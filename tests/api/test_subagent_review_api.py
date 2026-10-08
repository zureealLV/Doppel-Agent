"""Reviewed child transport definitions, UNRUN; scoped fixtures, not engine/native QA."""

import json

import pytest
from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider

PARENT, OTHER, CHILD = 'a' * 32, 'b' * 32, 'c' * 32
BASE = f'/api/v1/subagent-review/runs/{PARENT}'


def fixture_app(root):
    app = create_app(root, provider=MockProvider())
    service = app.state.run_service
    # Deliberately seeded scope records; actual runtime integration belongs to C.
    for parent in (PARENT, OTHER):
        service.runs.create(parent, parent, 'graph', {
            'prompt': 'parent', 'permissions': {'delegate': True},
        }, None)
        service.runs.update(parent, 'completed')
    service.subagents.store.create_bounded(CHILD, PARENT, 'inspect', 4)
    service.subagents.store.update(CHILD, 'completed', answer='first answer')
    return app, service


def test_passive_snapshot_reports_actual_limits_and_keeps_full_history(tmp_path, monkeypatch):
    app, service = fixture_app(tmp_path)
    for i in range(19):
        service.subagents.store.follow_up(CHILD, f'turn {i}', expected_generation=i + 1)
        service.subagents.store.update(CHILD, 'completed', answer=f'answer {i}')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        async def forbidden(*_args, **_kwargs):
            raise AssertionError('passive read tried execution/start')
        monkeypatch.setattr(service, 'start', forbidden)
        monkeypatch.setattr(service.subagents, 'start', forbidden)
        monkeypatch.setattr(service.subagents.scheduler, 'submit', forbidden)
        response = client.get(BASE)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        value = response.json()
        assert value['parent_run_id'] == PARENT and value['total'] == 1
        assert value['limits'] == {'lifetime_per_parent': 4, 'global_active': 2, 'global_queue': 16}
        assert value['counts']['lifetime_for_parent'] == 1
        assert value['counts']['durable_active_for_parent'] == 0
        assert value['capabilities']['workspace_read'] is True
        assert all(value['capabilities'][key] is False for key in
                   ('workspace_write', 'command_execute', 'mcp_execute', 'delegate'))
        assert value['service']['owner_held'] is True and value['service']['read_only'] is True
        child = value['items'][0]
        assert child['generation'] == 20 and child['history_total'] == 19
        assert child['history_offset'] == 3 and child['history_limit'] == 16
        assert len(child['history']) == 16 and child['history_truncated'] is True
        page = client.get(BASE + f'/{CHILD}/history', params={'expected_generation': 20, 'offset': 0})
        assert page.status_code == 200
        assert page.json()['history'][0] == {'prompt': 'inspect', 'answer': 'first answer'}
        assert len(service.subagents.store.get(CHILD)['history']) == 19
        assert value['physical_drain_verified'] is False
        assert value['output']['globally_redacted'] is False


def test_history_scope_and_generation_cannot_be_adopted_silently(tmp_path):
    app, service = fixture_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        service.subagents.store.follow_up(CHILD, 'next', expected_generation=1)
        service.subagents.store.update(CHILD, 'completed', answer='next answer')
        stale = client.get(BASE + f'/{CHILD}/history', params={'expected_generation': 1, 'offset': 0})
        assert stale.status_code == 409
        assert stale.json() == {'detail': 'subagent_review_generation_changed'}
        wrong = client.get(f'/api/v1/subagent-review/runs/{OTHER}/{CHILD}/history',
                           params={'expected_generation': 2, 'offset': 0})
        assert wrong.status_code == 404
        beyond = client.get(BASE + f'/{CHILD}/history', params={'expected_generation': 2, 'offset': 2})
        assert beyond.status_code == 422
        assert service.subagents.store.get(CHILD)['generation'] == 2


@pytest.mark.parametrize('suffix,raw', [
    ('/spawn', '{}'), ('/spawn', '{"confirmed":1,"prompt":"secret_DO_NOT_ECHO"}'),
    ('/spawn', '{"confirmed":true,"prompt":"x","permissions":{"workspace_write":true}}'),
    ('/spawn', '{"confirmed":true,"prompt":"x","confirmed":true}'),
    ('/spawn', '{"confirmed":true,"prompt":NaN}'),
    ('/spawn', '{"confirmed":true,"prompt":"   "}'),
    (f'/{CHILD}/follow-ups', '{"confirmed":true,"prompt":"x"}'),
    (f'/{CHILD}/follow-ups', '{"confirmed":true,"prompt":"x","expected_generation":true}'),
    (f'/{CHILD}/cancel', '{"confirmed":true,"expected_generation":0}'),
    (f'/{CHILD}/cancel', '{"confirmed":true,"expected_generation":1,"prompt":"secret_DO_NOT_ECHO"}'),
])
def test_review_body_rejects_hidden_or_unconfirmed_authority(tmp_path, suffix, raw, monkeypatch):
    app, service = fixture_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        async def forbidden(*_args, **_kwargs):
            raise AssertionError('invalid body reached mutation')
        for name in ('spawn_subagent', 'follow_up_subagent', 'cancel_subagent'):
            monkeypatch.setattr(service, name, forbidden)
        response = client.post(BASE + suffix, content=raw, headers={'Content-Type': 'application/json'})
        assert response.status_code == 422
        assert response.json() == {'detail': 'invalid_subagent_review_request'}
        assert response.headers['cache-control'] == 'no-store'
        assert 'secret_DO_NOT_ECHO' not in response.text


@pytest.mark.parametrize('query', ['hidden=secret_DO_NOT_ECHO', 'offset=0&offset=1&expected_generation=1',
                                   'offset=-1&expected_generation=1', 'offset=0&expected_generation=1&limit=17',
                                   'offset=0&expected_generation=0', 'offset=0',
                                   'offset=0&expected_generation=1.0', 'offset=0&expected_generation=01'])
def test_history_query_has_no_hidden_authority(tmp_path, query):
    app, _service = fixture_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        response = client.get(BASE + f'/{CHILD}/history?' + query)
        assert response.status_code == 422 and 'secret_DO_NOT_ECHO' not in response.text
        assert response.headers['cache-control'] == 'no-store'


def test_old_generation_post_refuses_effect_in_original_manager(tmp_path):
    app, service = fixture_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        service.subagents.store.follow_up(CHILD, 'newer', expected_generation=1)
        service.subagents.store.update(CHILD, 'completed', answer='newer answer')
        before = service.subagents.store.get(CHILD)
        follow = client.post(BASE + f'/{CHILD}/follow-ups', json={
            'confirmed': True, 'prompt': 'stale prompt', 'expected_generation': 1,
        })
        cancel = client.post(BASE + f'/{CHILD}/cancel', json={'confirmed': True, 'expected_generation': 1})
        for response in (follow, cancel):
            assert response.status_code == 409
            assert response.json() == {'detail': 'subagent_review_generation_changed'}
            assert response.headers['cache-control'] == 'no-store'
        assert service.subagents.store.get(CHILD) == before
        assert service.subagents.scheduler.active_count == 0
        assert service.subagents.scheduler.queued_count == 0


def test_delegate_denial_and_private_record_errors_are_not_echoed(tmp_path):
    app, service = fixture_app(tmp_path)
    denied = 'd' * 32
    service.runs.create(denied, denied, 'graph', {'prompt': 'parent', 'permissions': {'delegate': False}}, None)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        denied_reply = client.post(f'/api/v1/subagent-review/runs/{denied}/spawn',
                                   json={'confirmed': True, 'prompt': 'inspect'})
        assert denied_reply.status_code == 403
        assert denied_reply.json() == {'detail': 'subagent_review_delegate_required'}
        service.subagents.store.update(CHILD, 'failed', error='PRIVATE_PATH_API_KEY_DO_NOT_ECHO')
        snapshot = client.get(BASE)
        assert snapshot.status_code == 200
        assert snapshot.json()['items'][0]['error_present'] is True
        assert 'PRIVATE_PATH_API_KEY_DO_NOT_ECHO' not in snapshot.text


def test_review_receipts_forward_exact_generation_and_never_claim_drain(tmp_path, monkeypatch):
    app, service = fixture_app(tmp_path)
    calls = []
    async def follow(parent, child, prompt, *, expected_generation):
        calls.append(('follow', parent, child, prompt, expected_generation))
        return service.subagents.store.follow_up(child, prompt, expected_generation=expected_generation)
    async def cancel(parent, child, *, expected_generation):
        calls.append(('cancel', parent, child, expected_generation))
        return True  # forwarding fixture, not an executed cancellation
    monkeypatch.setattr(service, 'follow_up_subagent', follow)
    monkeypatch.setattr(service, 'cancel_subagent', cancel)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        follow_reply = client.post(BASE + f'/{CHILD}/follow-ups', json={
            'confirmed': True, 'prompt': 'next prompt', 'expected_generation': 1,
        })
        assert follow_reply.status_code == 202
        assert follow_reply.json()['reviewed_generation'] == 1
        assert follow_reply.json()['record']['generation'] == 2
        assert follow_reply.json()['admission_acknowledged'] is True
        assert follow_reply.json()['completion_verified'] is False
        cancel_reply = client.post(BASE + f'/{CHILD}/cancel', json={'confirmed': True, 'expected_generation': 2})
        assert cancel_reply.status_code == 200
        assert cancel_reply.json() == {
            'parent_run_id': PARENT, 'subagent_id': CHILD, 'reviewed_generation': 2,
            'cancel_requested': True, 'physical_drain_verified': False,
        }
        assert calls == [('follow', PARENT, CHILD, 'next prompt', 1), ('cancel', PARENT, CHILD, 2)]


def test_request_budgets_and_origin_failures_are_fixed_and_no_store(tmp_path):
    app, _service = fixture_app(tmp_path)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        too_large = client.post(BASE + '/spawn', content=json.dumps({'confirmed': True, 'prompt': 'x' * 17000}))
        assert too_large.status_code == 413
        too_long_query = client.get(BASE + '?' + 'x' * 1025)
        assert too_long_query.status_code == 413
        bad_id = client.get('/api/v1/subagent-review/runs/secret_DO_NOT_ECHO')
        assert bad_id.status_code == 422 and 'secret_DO_NOT_ECHO' not in bad_id.text
        origin = client.get(BASE, headers={'Origin': 'https://example.invalid'})
        host = client.get(BASE, headers={'Host': 'example.invalid'})
        assert origin.status_code == host.status_code == 403
        for response in (too_large, too_long_query, bad_id, origin, host):
            assert response.headers['cache-control'] == 'no-store'
