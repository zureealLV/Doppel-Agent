"""Explicit metadata discovery; all requests use local HTTP fixtures."""

import importlib
import json
import multiprocessing
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def discover(*args, **kwargs):
    return importlib.import_module("doppel_agent.model_discovery").discover_models(*args, **kwargs)


def test_manual_does_not_contact_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("manual discovery must never contact a service")

    monkeypatch.setattr(multiprocessing, "get_context", forbidden)
    result = discover("https://fixture.invalid/v1", "fixture-secret", "manual")
    assert result["status"] == "manual"
    assert result["models"] == []
    assert result["metadata_only"] is True


def test_cursor_pagination_stays_at_configured_origin_and_path():
    def page(handler):
        if "after=" not in handler.path:
            respond(handler, {"data": [{"id": "org/one"}], "has_more": True, "last_id": "org/one"})
        else:
            respond(handler, {"data": [{"id": "two"}], "has_more": False})

    with fixture_server(page) as (url, requests):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["models"] == [{"id": "org/one"}, {"id": "two"}]
    assert result["pages"] == 2
    assert requests == [("/v1/models", "Bearer fixture-secret"),
                        ("/v1/models?after=org%2Fone", "Bearer fixture-secret")]


@pytest.mark.parametrize("kind", ["page_limit", "repeated_cursor", "malicious_cursor", "remote_next"])
def test_invalid_or_unbounded_pagination_fails_closed(kind):
    count = [0]

    def page(handler):
        count[0] += 1
        cursor = "same" if kind == "repeated_cursor" else f"m{count[0]}"
        if kind == "malicious_cursor":
            cursor = "https://evil.invalid/steal"
        payload = {"data": [{"id": f"m{count[0]}"}], "has_more": True, "last_id": cursor}
        if kind == "remote_next":
            payload["next"] = "https://evil.invalid/steal"
        respond(handler, payload)

    with fixture_server(page) as (url, requests):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["status"] == "error"
    assert result["models"] == []
    assert result["error_code"] == ("limit_exceeded" if kind == "page_limit" else "invalid_response")
    assert len(requests) <= 5
    assert result["metadata_only"] is True


@contextmanager
def fixture_server(responder):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            responder(self)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def respond(handler, payload, status=200, headers=None):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@pytest.mark.parametrize("strategy, suffix, payload, path, expected", [
    ("openai-list", "/v1", {"data": [{"id": "org/gpt-test"}, {"id": "org/gpt-test"}]},
     "/v1/models", "org/gpt-test"),
    ("ollama-tags", "/v1", {"models": [{"name": "gemma4:latest", "size": 4}]},
     "/api/tags", "gemma4:latest"),
])
def test_explicit_strategy_returns_only_deduplicated_ids(strategy, suffix, payload, path, expected):
    children_before = {p.pid for p in multiprocessing.active_children()}
    with fixture_server(lambda h: respond(h, payload)) as (url, requests):
        result = discover(url + suffix, "fixture-secret", strategy)
    assert result["status"] == "ok"
    assert result["models"] == [{"id": expected}]
    assert result["metadata_only"] is True
    assert result["pages"] == 1
    assert requests == [(path, "Bearer fixture-secret")]
    assert "fixture-secret" not in repr(result)
    assert {p.pid for p in multiprocessing.active_children()} == children_before


@pytest.mark.parametrize("status, code", [(401, "authentication"), (403, "authentication"),
                                        (404, "unsupported"), (429, "rate_limited"), (500, "unavailable")])
def test_http_failures_are_categorized_without_response_body_or_retry(status, code):
    with fixture_server(lambda h: respond(h, {"error": "fixture-secret private"}, status)) as (url, requests):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["status"] == "error"
    assert result["error_code"] == code
    assert result["models"] == []
    assert len(requests) == 1
    assert "fixture-secret" not in repr(result)
    assert "private" not in repr(result)


def test_total_deadline_reaps_transport_process_before_returning():
    def slow_headers(handler):
        time.sleep(2)
        try:
            respond(handler, {"data": []})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    children_before = {p.pid for p in multiprocessing.active_children()}
    with fixture_server(slow_headers) as (url, _):
        start = time.monotonic()
        result = discover(url + "/v1", "fixture-secret", "openai-list", deadline_seconds=0.6)
        elapsed = time.monotonic() - start
        assert elapsed < 1.8
        assert result["error_code"] == "timeout"
        assert {p.pid for p in multiprocessing.active_children()} == children_before


@pytest.mark.parametrize("model_id", ["<img src=x onerror=alert(1)>", "abc\nsecret", "../model",
                                     "x\\y", "https://evil.invalid/", "", "a" * 257, 123, None,
                                     "foo\u202emodel", "space model"])
def test_malicious_model_ids_fail_closed_without_echo(model_id):
    with fixture_server(lambda h: respond(h, {"data": [{"id": "safe"}, {"id": model_id}]})) as (url, _):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_response"
    assert result["models"] == []


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": ["unsafe"]}, {"data": [{}]}])
def test_invalid_schema_is_safe_error(payload):
    with fixture_server(lambda h: respond(h, payload)) as (url, _):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["error_code"] == "invalid_response"


def test_redirect_never_forwards_credentials_even_to_same_origin():
    def redirect(handler):
        if handler.path.endswith("/models"):
            respond(handler, {}, 302, {"Location": "/stolen"})
        else:
            respond(handler, {"data": [{"id": "redirected"}]})

    with fixture_server(redirect) as (url, requests):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["error_code"] == "redirect_blocked"
    assert requests == [("/v1/models", "Bearer fixture-secret")]


@pytest.mark.parametrize("kind", ["bytes", "items"])
def test_response_limits_fail_closed(kind):
    payload = ({"data": [], "padding": "x" * (1024 * 1024)} if kind == "bytes" else
               {"data": [{"id": f"m{i}"} for i in range(1001)]})
    with fixture_server(lambda h: respond(h, payload)) as (url, _):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["error_code"] == "limit_exceeded"
    assert result["models"] == []


@pytest.mark.parametrize("url, strategy, key", [
    ("https://fixture.invalid/v1", "unknown", "fixture-secret"),
    ("file:///private", "openai-list", "fixture-secret"),
    ("https://user:password@fixture.invalid/v1", "openai-list", "fixture-secret"),
    ("https://fixture.invalid/v1?key=private", "openai-list", "fixture-secret"),
    ("https://fixture.invalid/v1#private", "openai-list", "fixture-secret"),
    ("https://fixture.invalid/v1", "openai-list", "fixture\r\nsecret"),
])
def test_invalid_config_never_spawns_network_process(monkeypatch, url, strategy, key):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid config must fail before starting a transport")

    monkeypatch.setattr(multiprocessing, "get_context", forbidden)
    result = discover(url, key, strategy)
    assert result["error_code"] in {"invalid_config", "unsupported_strategy"}
    assert "private" not in repr(result)
    assert "secret" not in repr(result)


def test_key_echo_in_model_ids_is_never_returned():
    with fixture_server(lambda h: respond(h, {"data": [{"id": "fixture-secret"}]})) as (url, _):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["error_code"] == "invalid_response"
    assert "fixture-secret" not in repr(result)


def test_ollama_without_key_sends_no_authorization_header():
    with fixture_server(lambda h: respond(h, {"models": [{"name": "local:latest"}]})) as (url, requests):
        result = discover(url + "/v1", "", "ollama-tags")
    assert result["status"] == "ok"
    assert requests == [("/api/tags", None)]


@pytest.mark.parametrize("kind", ["bytes", "items"])
def test_limits_are_total_across_pages_not_per_page(kind):
    count = [0]

    def page(handler):
        count[0] += 1
        payload = {"data": [{"id": f"m{count[0]}"}], "has_more": count[0] == 1,
                   "last_id": f"m{count[0]}"}
        if kind == "bytes":
            payload["padding"] = "x" * 600_000
        else:
            payload["data"] = [{"id": f"m{count[0]}-{i}"} for i in range(600)]
        respond(handler, payload)

    with fixture_server(page) as (url, requests):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["error_code"] == "limit_exceeded"
    assert result["models"] == []
    assert len(requests) == 2


def test_slow_trickle_body_cannot_reset_total_deadline():
    def trickle(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "1000")
        handler.end_headers()
        try:
            for _ in range(20):
                handler.wfile.write(b" ")
                handler.wfile.flush()
                time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    before = {p.pid for p in multiprocessing.active_children()}
    with fixture_server(trickle) as (url, requests):
        start = time.monotonic()
        result = discover(url + "/v1", "fixture-secret", "openai-list", deadline_seconds=0.6)
        assert time.monotonic() - start < 1.8
        assert result["error_code"] == "timeout"
        assert {p.pid for p in multiprocessing.active_children()} == before
        assert len(requests) == 1


def test_malformed_http_status_does_not_leak_server_text_to_stderr(capfd):
    def malformed(handler):
        handler.wfile.write(b"garbage fixture-secret private\r\n\r\n")
        handler.wfile.flush()

    with fixture_server(malformed) as (url, _):
        result = discover(url + "/v1", "fixture-secret", "openai-list")
    assert result["error_code"] == "invalid_response"
    captured = capfd.readouterr()
    assert "fixture-secret" not in captured.err
    assert "private" not in captured.err
