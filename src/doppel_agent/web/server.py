"""Loopback-only API and static web console. Secrets remain request-scoped."""

from __future__ import annotations

import json
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from ..conversations import ConversationStore
from ..core import Core
from ..provider import Message, MockProvider, OpenAICompatibleProvider
from ..settings import SettingsStore
from ..storage import RunStore
from ..tasks.manager import TaskManager
from .approvals import ApprovalBroker


ASSET_ROOT = Path(__file__).parent
RUNTIME_ASSET_ROOT = ASSET_ROOT / "frontend_dist"


class JobManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        self.store = RunStore(self.workspace / ".doppel-agent")
        self.conversations = ConversationStore(self.workspace / ".doppel-agent" / "conversations.sqlite3")
        self.settings = SettingsStore(self.workspace / ".doppel-agent" / "provider-settings.json")
        self.jobs: dict[str, dict] = {}
        self.brokers: dict[str, ApprovalBroker] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="doppel-run")

    def _provider(self, config: dict):
        requested = dict(config)
        profile_id = config.get("profile_id")
        profile_key = ""
        allow_saved_key = False
        if profile_id is not None:
            if not isinstance(profile_id, str):
                raise ValueError("profile_id must be a string")
            stored = self.settings.profile(profile_id)
            profile_key = stored.pop("api_key", "")
            requested_base = requested.get("base_url")
            allow_saved_key = not requested_base or requested_base == stored.get("base_url")
            config = {**stored, **{key: value for key, value in config.items() if value not in (None, "")}}
        if config.get("provider") == "mock":
            return MockProvider()
        if config.get("provider") != "openai":
            raise ValueError("provider must be 'openai' or 'mock'")
        base_url = config.get("base_url")
        model = config.get("model")
        key = config.get("api_key", "")
        if not key and allow_saved_key:
            key = profile_key
        if not isinstance(base_url, str) or not isinstance(model, str) or not isinstance(key, str):
            raise ValueError("base_url, model and api_key must be strings")
        return OpenAICompatibleProvider(base_url, model, key)

    def probe(self, config: dict) -> dict:
        provider = self._provider(config)
        if isinstance(provider, MockProvider):
            return {"ok": True, "reply": "Mock provider is ready (offline only)."}
        turn = provider.next_turn([Message("user", "Reply briefly: Doppel Agent API connection OK")], [])
        return {"ok": True, "reply": turn.content[:1000]}

    def submit(self, data: dict) -> dict:
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
            raise ValueError("prompt must contain 1 to 100000 characters")
        config = data.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        provider = self._provider(config)
        mode = data.get("mode", "agent")
        if mode not in {"agent", "review"}:
            raise ValueError("mode must be agent or review")
        effort = data.get("effort", "balanced")
        if effort not in {"quick", "balanced", "deep"}:
            raise ValueError("effort must be quick, balanced or deep")
        max_steps = {"quick": 6, "balanced": 8, "deep": 12}[effort]
        profile_id = config.get("profile_id")
        model = config.get("model", "mock")
        if isinstance(profile_id, str):
            profile = self.settings.profile(profile_id)
            model = profile.get("model", "mock")
        allow_write = data.get("allow_write", False)
        allow_command = data.get("allow_command", False)
        allow_mcp = data.get("allow_mcp", False)
        allow_delegate = data.get("allow_delegate", False)
        conversation_id = data.get("conversation_id")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise ValueError("conversation_id must be a string")
        if not all(isinstance(value, bool) for value in (allow_write, allow_command, allow_mcp, allow_delegate)):
            raise ValueError("permission grants must be booleans")
        with self.lock:
            active = sum(job["status"] in ("queued", "running") for job in self.jobs.values())
            if active >= 2:
                raise RuntimeError("two runs are already active")
            run_id = uuid4().hex
            history: list[Message] = []
            if conversation_id:
                self.conversations.set_title_from_prompt(conversation_id, prompt)
                history = [Message(item["role"], item["content"]) for item in self.conversations.history(conversation_id)]
                if isinstance(profile_id, str):
                    self.conversations.set_profile(conversation_id, profile_id)
                self.conversations.add_message(conversation_id, "user", prompt, run_id, model=model)
            self.jobs[run_id] = {
                "run_id": run_id, "status": "queued", "answer": "", "prompt": prompt,
                "conversation_id": conversation_id,
            }
            self.brokers[run_id] = ApprovalBroker()

        def run() -> None:
            with self.lock:
                self.jobs[run_id]["status"] = "running"
            try:
                result = Core(
                    self.workspace, provider, allow_write=allow_write, allow_command=allow_command,
                    allow_mcp=allow_mcp, allow_delegate=allow_delegate,
                    approver=self.brokers[run_id].request,
                    max_steps=max_steps,
                    review_mode=mode == "review",
                ).run(prompt, run_id=run_id, history=history)
            except Exception as exc:
                result = {"run_id": run_id, "status": "failed", "answer": f"{type(exc).__name__}: {exc}"}
            result["prompt"] = prompt
            result["conversation_id"] = conversation_id
            self.store.write_session(run_id, result)
            if conversation_id:
                self.conversations.add_message(conversation_id, "assistant", result["answer"], run_id, model=model)
            with self.lock:
                self.jobs[run_id] = result

        self.pool.submit(run)
        return {"run_id": run_id, "conversation_id": conversation_id}

    def status(self, run_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(run_id)
            return dict(job) if job else self.store.read_session(run_id)

    def recent(self) -> list[dict]:
        with self.lock:
            live = [dict(job) for job in self.jobs.values()]
        saved = self.store.list_sessions()
        known = {job["run_id"] for job in live}
        return [
            {"run_id": job["run_id"], "status": job["status"], "prompt": job.get("prompt", ""),
             "conversation_id": job.get("conversation_id")}
            for job in live + [job for job in saved if job["run_id"] not in known]
        ]

    def public_settings(self) -> dict:
        return self.settings.public()

    def save_settings(self, data: dict) -> dict:
        config = data.get("config")
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        key = config.pop("api_key", "")
        forget = data.get("forget_key", False)
        if not isinstance(key, str) or not isinstance(forget, bool):
            raise ValueError("invalid key settings")
        return self.settings.save_profile(
            config, profile_id=data.get("profile_id"), api_key=key, forget_key=forget,
        )

    def events(self, run_id: str) -> list[dict] | None:
        if self.status(run_id) is None:
            return None
        path = self.workspace / ".doppel-agent" / "runs" / run_id / "events.jsonl"
        if not path.is_file():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines()[-300:]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # Another thread may be appending the final line.
        return result

    def tasks(self, run_id: str) -> list[dict] | None:
        if self.status(run_id) is None:
            return None
        manager = TaskManager(self.workspace / ".doppel-agent" / "tasks.sqlite3")
        return manager.list(run_id)

    def approvals(self, run_id: str) -> list[dict] | None:
        broker = self.brokers.get(run_id)
        if broker:
            return broker.list_pending()
        return [] if self.status(run_id) is not None else None

    def decide(self, run_id: str, approval_id: str, allow: bool) -> bool:
        broker = self.brokers.get(run_id)
        return bool(broker and broker.decide(approval_id, allow))


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], workspace: Path):
        super().__init__(address, ConsoleHandler)
        self.manager = JobManager(workspace)

    def server_close(self) -> None:
        self.manager.pool.shutdown(wait=False, cancel_futures=True)
        super().server_close()


class ConsoleHandler(BaseHTTPRequestHandler):
    server: ConsoleServer

    def log_message(self, format: str, *args) -> None:
        pass  # Do not log API keys, prompts or request bodies.

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        return host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def _send(self, code: int, content: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, code: int, payload: dict | list) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _reject_post(self, code: int, reason: str) -> None:
        # On Windows, replying while a small POST body is unread can reset the
        # connection before the browser receives the 403/415 response.
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if 0 < size <= 128 * 1024:
                previous_timeout = self.connection.gettimeout()
                try:
                    self.connection.settimeout(1)
                    self.rfile.read(size)
                finally:
                    self.connection.settimeout(previous_timeout)
        except (OSError, ValueError):
            pass
        self._json(code, {"error": reason})

    def do_GET(self) -> None:
        if not self._allowed_host():
            self._json(403, {"error": "invalid host"})
            return
        parsed = urlparse(self.path)
        path = parsed.path
        assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8")}
        if path in assets:
            name, content_type = assets[path]
            self._send(200, (ASSET_ROOT / name).read_bytes(), content_type)
        elif path in {"/runtime", "/runtime/"}:
            index = RUNTIME_ASSET_ROOT / "index.html"
            if index.is_file():
                self._send(200, index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "runtime workbench has not been built"})
        elif path.startswith("/runtime/assets/"):
            relative = unquote(path.removeprefix("/runtime/assets/"))
            root = (RUNTIME_ASSET_ROOT / "assets").resolve()
            candidate = (root / relative).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                if content_type in {"text/javascript", "application/javascript"}:
                    content_type = "application/javascript"
                self._send(200, candidate.read_bytes(), f"{content_type}; charset=utf-8")
            else:
                self._json(404, {"error": "not found"})
        elif path == "/api/health":
            self._json(200, {"status": "ok", "workspace": str(self.server.manager.workspace)})
        elif path == "/api/runs":
            self._json(200, self.server.manager.recent())
        elif path == "/api/settings":
            self._json(200, self.server.manager.public_settings())
        elif path == "/api/groups":
            self._json(200, self.server.manager.conversations.list_groups())
        elif path == "/api/conversations":
            archived = parse_qs(parsed.query).get("archived", ["0"])[0] == "1"
            self._json(200, self.server.manager.conversations.list(archived=archived))
        elif path == "/api/conversations/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self._json(200, self.server.manager.conversations.search(query))
        elif path.startswith("/api/conversations/"):
            parts = path.split("/")
            if len(parts) == 4:
                conversation = self.server.manager.conversations.get(parts[3])
                self._json(200, conversation) if conversation else self._json(404, {"error": "conversation not found"})
            else:
                self._json(404, {"error": "not found"})
        elif path.startswith("/api/runs/"):
            parts = path.split("/")
            if len(parts) not in (4, 5):
                self._json(404, {"error": "not found"})
                return
            run_id = parts[3]
            if len(parts) == 5 and parts[4] == "events":
                events = self.server.manager.events(run_id)
                self._json(200, events) if events is not None else self._json(404, {"error": "run not found"})
            elif len(parts) == 5 and parts[4] == "tasks":
                tasks = self.server.manager.tasks(run_id)
                self._json(200, tasks) if tasks is not None else self._json(404, {"error": "run not found"})
            elif len(parts) == 5 and parts[4] == "approvals":
                approvals = self.server.manager.approvals(run_id)
                self._json(200, approvals) if approvals is not None else self._json(404, {"error": "run not found"})
            elif len(parts) == 4:
                status = self.server.manager.status(run_id)
                self._json(200, status) if status is not None else self._json(404, {"error": "run not found"})
            else:
                self._json(404, {"error": "not found"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._allowed_host():
            self._reject_post(403, "invalid host")
            return
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            self._reject_post(403, "invalid origin")
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or self.headers.get("X-Doppel-UI") != "1":
            self._reject_post(415, "JSON UI request required")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 128 * 1024:
                raise ValueError("request body exceeds limit")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            path = urlparse(self.path).path
            if path == "/api/probe":
                self._json(200, self.server.manager.probe(body.get("config", {})))
            elif path == "/api/settings":
                self._json(200, self.server.manager.save_settings(body))
            elif path.startswith("/api/settings/profiles/") and path.endswith("/delete"):
                profile_id = path.split("/")[4]
                self._json(200, self.server.manager.settings.delete_profile(profile_id))
            elif path == "/api/groups":
                name = body.get("name")
                if not isinstance(name, str):
                    raise ValueError("name must be a string")
                self._json(201, self.server.manager.conversations.create_group(name))
            elif path.startswith("/api/groups/"):
                parts = path.split("/")
                if len(parts) != 5:
                    self._json(404, {"error": "not found"})
                elif parts[4] == "rename":
                    self._json(200, self.server.manager.conversations.rename_group(parts[3], body.get("name", "")))
                elif parts[4] == "delete":
                    self._json(200, {"ok": self.server.manager.conversations.delete_group(parts[3])})
                else:
                    self._json(404, {"error": "not found"})
            elif path == "/api/conversations":
                title = body.get("title", "新对话")
                if not isinstance(title, str):
                    raise ValueError("title must be a string")
                self._json(201, self.server.manager.conversations.create(title))
            elif path == "/api/conversations/review-draft":
                self._json(200, self.server.manager.conversations.get_or_create_empty("代码审查"))
            elif path == "/api/conversations/new-draft":
                self._json(200, self.server.manager.conversations.get_or_create_empty("新对话"))
            elif path.startswith("/api/conversations/"):
                parts = path.split("/")
                if len(parts) != 5:
                    self._json(404, {"error": "not found"})
                elif parts[4] == "rename":
                    title = body.get("title")
                    if not isinstance(title, str):
                        raise ValueError("title must be a string")
                    self._json(200, self.server.manager.conversations.rename(parts[3], title))
                elif parts[4] == "delete":
                    if self.server.manager.conversations.delete(parts[3]):
                        self._json(200, {"ok": True})
                    else:
                        self._json(404, {"error": "conversation not found"})
                elif parts[4] == "archive":
                    archived = body.get("archived")
                    if not isinstance(archived, bool):
                        raise ValueError("archived must be a boolean")
                    self._json(200, self.server.manager.conversations.archive(parts[3], archived))
                elif parts[4] == "group":
                    group_id = body.get("group_id")
                    if group_id is not None and not isinstance(group_id, str):
                        raise ValueError("group_id must be a string or null")
                    self._json(200, self.server.manager.conversations.set_group(parts[3], group_id))
                elif parts[4] == "profile":
                    profile_id = body.get("profile_id")
                    if profile_id is not None and not isinstance(profile_id, str):
                        raise ValueError("profile_id must be a string or null")
                    self._json(200, self.server.manager.conversations.set_profile(parts[3], profile_id))
                else:
                    self._json(404, {"error": "not found"})
            elif path == "/api/runs":
                self._json(202, self.server.manager.submit(body))
            elif path.startswith("/api/runs/") and path.endswith("/decision"):
                parts = path.split("/")
                if len(parts) != 7 or parts[4] != "approvals" or not isinstance(body.get("allow"), bool):
                    raise ValueError("invalid approval decision")
                if self.server.manager.decide(parts[3], parts[5], body["allow"]):
                    self._json(200, {"ok": True})
                else:
                    self._json(404, {"error": "approval not found or already decided"})
            else:
                self._json(404, {"error": "not found"})
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(503, {"error": str(exc)})
        except Exception as exc:
            self._json(502, {"error": f"{type(exc).__name__}: {exc}"})


def serve_ui(workspace: Path, port: int = 8766) -> None:
    server = ConsoleServer(("127.0.0.1", port), workspace)
    try:
        print(f"Doppel Agent UI: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()
    finally:
        server.server_close()
