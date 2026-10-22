"""Explicit model metadata discovery, separate from generation/capability probes."""

import json
import multiprocessing
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 1024 * 1024
MAX_MODELS = 1000
MAX_PAGES = 5


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _result(strategy, status="manual", error_code=None):
    result = {"status": "manual", "models": [], "metadata_only": True, "strategy": strategy, "pages": 0}
    result["status"] = status
    if error_code:
        result.update(error_code=error_code, message="Model metadata request failed.")
    return result


def discover_models(base_url: str, api_key: str, strategy: str, *, deadline_seconds: float = 10.0) -> dict:
    """Return sanitized metadata only, after the owned network process has exited.

    The deadline includes process startup, DNS, headers, body and all pages. A
    timed-out process is terminated and joined, never abandoned in a thread.
    Reaping may add OS scheduling overhead after the deadline. Frozen entry
    points must call multiprocessing.freeze_support() before launching the app.
    """
    if strategy == "manual":
        return _result(strategy)
    if strategy not in {"openai-list", "ollama-tags"}:
        return _result("unsupported", "error", "unsupported_strategy")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or port == 0
                or any(ord(c) < 33 for c in base_url) or "\\" in base_url
                or not isinstance(api_key, str) or any(ord(c) < 32 or ord(c) > 126 for c in api_key)):
            return _result(strategy, "error", "invalid_config")
    except (ValueError, TypeError, AttributeError):
        return _result(strategy, "error", "invalid_config")
    end = time.monotonic() + min(10.0, max(0.01, deadline_seconds))
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(writer, base_url, api_key, strategy, end), daemon=True)
    try:
        process.start()
        writer.close()
        if not reader.poll(max(0, end - time.monotonic())):
            return _result(strategy, "error", "timeout")
        result = reader.recv()
        process.join(max(0, end - time.monotonic()))
        if process.is_alive() or time.monotonic() >= end:
            return _result(strategy, "error", "timeout")
        return result
    except (EOFError, OSError):
        return _result(strategy, "error", "network")
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join()
            process.close()


def _worker(writer, base_url, api_key, strategy, end):
    try:
        try:
            result = _discover(base_url, api_key, strategy, end)
        except (TimeoutError, URLError, OSError):
            result = _result(strategy, "error", "network")
        except (ValueError, KeyError, TypeError, RecursionError, HTTPException):
            result = _result(strategy, "error", "invalid_response")
        writer.send(result)
    finally:
        writer.close()


def _discover(base_url, api_key, strategy, end):
    if strategy == "ollama-tags":
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        url = root + "/api/tags"
    else:
        url = base_url.rstrip("/") + "/models"
    endpoint, cursor = url, None
    ids, cursors = {}, set()
    total_bytes = total_items = 0
    for page in range(1, MAX_PAGES + 1):
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = Request(url, headers=headers)
        try:
            with build_opener(_RejectRedirects).open(request, timeout=max(0.001, end - time.monotonic())) as response:
                body = response.read(MAX_BYTES - total_bytes + 1)
                total_bytes += len(body)
                if total_bytes > MAX_BYTES:
                    return _result(strategy, "error", "limit_exceeded")
                payload = json.loads(body)
        except HTTPError as exc:
            code = ("redirect_blocked" if 300 <= exc.code < 400 else
                    {401: "authentication", 403: "authentication", 404: "unsupported", 429: "rate_limited"}.get(
                        exc.code, "unavailable"))
            exc.close()
            return _result(strategy, "error", code)
        models = payload["models" if strategy == "ollama-tags" else "data"]
        key = "name" if strategy == "ollama-tags" else "id"
        if not isinstance(models, list):
            raise ValueError("invalid metadata")
        total_items += len(models)
        if total_items > MAX_MODELS:
            return _result(strategy, "error", "limit_exceeded")
        for model in models:
            value = model[key] if isinstance(model, dict) else None
            if not _safe_id(value) or (api_key and api_key in value):
                raise ValueError("invalid model identifier")
            ids[value] = None
        # OpenAI and Ollama currently document a single list, not URL pagination.
        # Compatible servers may opt into this bounded cursor-only extension.
        if payload.get("next") or payload.get("next_url"):
            raise ValueError("URL pagination is not supported")
        has_more = payload.get("has_more", False)
        if not isinstance(has_more, bool):
            raise ValueError("invalid pagination")
        if not has_more:
            result = _result(strategy, "ok")
            result.update(models=[{"id": value} for value in ids], pages=page)
            return result
        cursor = payload.get("last_id")
        if strategy != "openai-list" or not _safe_id(cursor) or cursor in cursors:
            raise ValueError("invalid pagination cursor")
        cursors.add(cursor)
        url = endpoint + "?" + urlencode({"after": cursor})
    return _result(strategy, "error", "limit_exceeded")


def _safe_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 256
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@+\-]*", value) is not None
            and ".." not in value and "://" not in value and "//" not in value)
