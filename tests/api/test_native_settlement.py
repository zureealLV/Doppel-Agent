import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from api.test_native_conversations_api import settled
from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider


def test_terminal_status_is_not_a_drained_native_turn(tmp_path, monkeypatch):
    app = create_app(tmp_path, provider=MockProvider())
    service = app.state.run_service
    entered, release = threading.Event(), threading.Event()
    original = service._release_turn

    async def hold_release(run_id):
        entered.set()
        assert await asyncio.to_thread(release.wait, 30)
        await original(run_id)

    monkeypatch.setattr(service, '_release_turn', hold_release)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        with ThreadPoolExecutor() as executor:
            try:
                cid = client.post('/api/v1/conversations', json={'mode': 'legacy'}).json()['id']
                endpoint = f'/api/v1/conversations/{cid}/runs'
                record = client.post(endpoint, json={'mode': 'legacy', 'prompt': 'first'}).json()
                rid = record['run_id']
                assert entered.wait(30)
                visible = client.get(f'/api/v1/runs/{rid}').json()
                assert visible['status'] == 'completed'
                assert visible['lease_active'] is True
                assert client.get(f'/api/v1/conversations/{cid}').json()['active_run_id'] == rid
                assert client.post(endpoint, json={'mode': 'legacy', 'prompt': 'too early'}).status_code == 409
                polling = executor.submit(settled, client, rid)
                time.sleep(.05)
                assert not polling.done(), 'status-only polling mistook a held lease for a drained turn'
                release.set()
                assert polling.result(timeout=30)['lease_active'] is False
                assert client.get(f'/api/v1/conversations/{cid}').json()['active_run_id'] is None
                next_run = client.post(endpoint, json={'mode': 'legacy', 'prompt': 'second'})
                assert next_run.status_code == 202
                assert settled(client, next_run.json()['run_id'])['status'] == 'completed'
            finally:
                release.set()
