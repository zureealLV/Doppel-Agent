"""C2c FIRST definitions, ALL UNRUN: original manager/HTTP with offline gates.

No actual model/key/native owner/SDK/process/port drain proof. These retain the
same entered request/future; an observation timeout never starts a replacement.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from urllib.request import Request, urlopen

import pytest

from doppel_agent.provider import ModelTurn
from doppel_agent.web.server import ConsoleHandler, ConsoleServer, JobManager


def test_original_probe_remains_owned_until_exact_provider_return_and_close_refuses_new_lookup(
    tmp_path, monkeypatch
):
    manager = JobManager(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("entered")
            entered.set()
            release.wait()
            return ModelTurn("offline late reply")

    lookup = []

    def provider(config):
        lookup.append(dict(config))
        return Provider()

    monkeypatch.setattr(manager, "_provider", provider)
    with ThreadPoolExecutor(max_workers=2) as fixture:
        probe = fixture.submit(manager.probe, {"provider": "offline_fixture"})
        close = None
        try:
            assert entered.wait(2)
            close = fixture.submit(manager.close_owned)
            with pytest.raises(FutureTimeoutError):
                close.result(timeout=0.05)
            assert manager._closing and manager._pending_requests == 1
            with pytest.raises(RuntimeError, match="closing"):
                manager.probe({"private": "NEW_LOOKUP_FORBIDDEN"})
            assert len(lookup) == 1 and calls == ["entered"]
        finally:
            release.set()
        assert probe.result(timeout=3) == {"ok": True, "reply": "offline late reply"}
        assert close is not None
        close.result(timeout=3)
        assert manager._pending_requests == 0
    manager.close_owned()


def test_original_save_worker_is_joined_and_nested_request_uses_same_lease(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    entered, release = threading.Event(), threading.Event()
    counts = []

    def save(*args, **kwargs):
        counts.append(manager._pending_requests)
        entered.set()
        release.wait()
        return {"fixture": "public metadata only"}

    monkeypatch.setattr(manager.settings, "save_profile", save)

    def request():
        with manager.owned_request():
            return manager.save_settings(
                {"config": {"provider": "mock", "api_key": "OFFLINE_PRIVATE_PAYLOAD"}}
            )

    with ThreadPoolExecutor(max_workers=2) as fixture:
        work = fixture.submit(request)
        close = None
        try:
            assert entered.wait(2)
            close = fixture.submit(manager.close_owned)
            with pytest.raises(FutureTimeoutError):
                close.result(timeout=0.05)
            assert counts == [1] and manager._pending_requests == 1
        finally:
            release.set()
        assert work.result(timeout=3) == {"fixture": "public metadata only"}
        assert close is not None
        close.result(timeout=3)
    assert manager._pending_requests == 0


def test_provider_probe_failure_is_fixed_unknown_not_private_exception_or_automatic_retry(
    tmp_path, monkeypatch
):
    manager = JobManager(tmp_path)
    calls = []

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("one original call")
            raise ValueError("PRIVATE_API_KEY_OR_PROVIDER_BODY")

    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    try:
        with pytest.raises(RuntimeError, match="^provider_probe_unavailable$") as failure:
            manager.probe({})
        assert "PRIVATE" not in str(failure.value) and manager._pending_requests == 0 and len(calls) == 1
    finally:
        manager.close_owned()


@pytest.mark.parametrize("content", [None, {}, 42])
def test_probe_malformed_original_reply_is_fixed_unknown_and_never_retried(tmp_path, monkeypatch, content):
    manager = JobManager(tmp_path)
    calls = []

    class Provider:
        def next_turn(self, messages, tools):
            calls.append("original entered")
            return ModelTurn(content)

    monkeypatch.setattr(manager, "_provider", lambda config: Provider())
    try:
        with pytest.raises(RuntimeError, match="^provider_probe_unavailable$"):
            manager.probe({})
        assert calls == ["original entered"] and manager._pending_requests == 0
    finally:
        manager.close_owned()


def test_entered_http_response_failure_is_not_relabelled_as_closing(tmp_path):
    manager = JobManager(tmp_path)

    class Server:
        server_port = 12345

    server = Server()
    server.manager = manager
    handler = object.__new__(ConsoleHandler)
    handler.server = server
    handler.headers = {"Host": "127.0.0.1:12345", "Content-Type": "application/json", "X-Doppel-UI": "1"}
    rejects = []
    handler._reject_post = lambda *args: rejects.append(args)

    def original_response_failure():
        assert manager._pending_requests == 1
        raise RuntimeError("fixture original response failure")

    handler._do_POST_owned = original_response_failure
    try:
        with pytest.raises(RuntimeError, match="fixture original response failure"):
            handler.do_POST()
        assert rejects == [] and manager._pending_requests == 0
        manager.close_owned()
        handler.do_POST()
        assert rejects == [(503, "Legacy workspace is closing")]
    finally:
        manager.close_owned()


def test_closed_manager_refuses_save_submit_before_keys_profile_or_acceptance(tmp_path, monkeypatch):
    manager = JobManager(tmp_path)
    manager.close_owned()

    def forbidden(*args, **kwargs):
        raise AssertionError("new private lookup or mutation")

    monkeypatch.setattr(manager, "_provider", forbidden)
    monkeypatch.setattr(manager.settings, "save_profile", forbidden)
    with pytest.raises(RuntimeError, match="closing"):
        manager.submit({"prompt": "fixture", "config": {}})
    with pytest.raises(RuntimeError, match="closing"):
        manager.save_settings({"config": {}})
    assert not manager.jobs and manager._pending_requests == 0


def test_original_console_server_close_does_not_cancel_accepted_pool_futures(tmp_path):
    server = ConsoleServer(("127.0.0.1", 0), tmp_path)
    entered, release = threading.Event(), threading.Event()

    def run():
        entered.set()
        release.wait()
        return "original worker complete"

    first = server.manager.pool.submit(run)
    second = server.manager.pool.submit(run)
    try:
        assert entered.wait(2)
        queued = server.manager.pool.submit(lambda: "accepted queued work retained")
        with ThreadPoolExecutor(max_workers=1) as fixture:
            close = fixture.submit(server.server_close)
            try:
                with pytest.raises(FutureTimeoutError):
                    close.result(timeout=0.05)
                assert not queued.cancelled()
            finally:
                release.set()
            close.result(timeout=3)
        assert first.result() == second.result() == "original worker complete"
        assert queued.result() == "accepted queued work retained"
    finally:
        release.set()
        server.server_close()


def test_original_http_post_keeps_one_request_through_original_save_response_and_server_close(
    tmp_path, monkeypatch
):
    server = ConsoleServer(("127.0.0.1", 0), tmp_path)
    server.daemon_threads = False  # Exact native original join configuration, not native execution.
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    entered, release = threading.Event(), threading.Event()
    counts = []

    def save(data):
        counts.append(server.manager._pending_requests)
        entered.set()
        release.wait()
        return {"fixture": True}

    monkeypatch.setattr(server.manager, "save_settings", save)
    base = f"http://127.0.0.1:{server.server_port}"

    def post():
        request = Request(
            base + "/api/settings",
            data=json.dumps({"config": {}}).encode(),
            headers={"Content-Type": "application/json", "X-Doppel-UI": "1"},
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read())

    try:
        with ThreadPoolExecutor(max_workers=2) as fixture:
            original = fixture.submit(post)
            close = None
            try:
                assert entered.wait(2)
                server.shutdown()
                close = fixture.submit(server.server_close)
                with pytest.raises(FutureTimeoutError):
                    close.result(timeout=0.05)
                assert counts == [1] and not original.done()
            finally:
                release.set()
                server.shutdown()
            assert original.result(timeout=3) == {"fixture": True}
            assert close is not None
            close.result(timeout=3)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
    assert not worker.is_alive() and server.manager._pending_requests == 0
