"""Loopback-only API and static web console. Secrets remain request-scoped."""

from __future__ import annotations

from collections.abc import Callable
import json
import mimetypes
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

from ..conversations import ConversationStore, ConversationPersistenceError
from ..core import Core
from ..provider import Message, MockProvider, OpenAICompatibleProvider
from ..settings import SettingsStore, SettingsPersistenceError
from ..storage import RunStore, LegacyStorageReadError
from ..tasks.manager import TaskManager, TaskPersistenceError
from ..workspace.process_supervisor import ProcessSupervisor
from .approvals import ApprovalBroker


ASSET_ROOT = Path(__file__).parent
RUNTIME_ASSET_ROOT = ASSET_ROOT / "frontend_dist"


class LegacyRequestClosing(RuntimeError):
    """Original request admission refused; not an entered operation failure."""


class LegacyOperationEvidenceError(RuntimeError):
    def __init__(self):
        super().__init__('legacy_operation_evidence_unavailable')


class LegacyServerCleanupError(RuntimeError):
    """Original bound/unreturned socket lifetime unknown; private original source."""

    def __init__(self, source: ConsoleServer | None = None):
        super().__init__('legacy_server_cleanup_unresolved')
        self.source = source


class _LegacyAdmittedProvider:
    """Guard the same original sync method, including inherited delegates.

    Not a recorder or transport wrapper. Entered calls return unchanged; no
    cached provider mutation, replacement worker, retry or hidden-attempt proof.
    """

    def __init__(self, original, check: Callable[[], None]):
        self.original, self.check = original, check

    def next_turn(self, messages, tools):
        self.check()
        return self.original.next_turn(messages, tools)


class JobManager:
    def __init__(self, workspace: Path, *, effect_admission: Callable[[], None] | None = None,
                 effect_failure: Callable[[], None] | None = None):
        # Native injects SAME kernel/service guard before the original HTTP worker
        # can start. Standalone Legacy has no borrowed runtime owner; unchanged.
        self._effect_admission = effect_admission
        self._effect_failure = effect_failure
        self._operation_fault = threading.Event()
        self._operation_cleanup_uncertain = threading.Event()
        self._submission_uncertain = threading.Event()
        self._metadata_lifetime_lock = threading.Lock()
        self._unresolved_metadata_sources: dict[int, ConversationStore | TaskManager | SettingsStore | RunStore] = {}
        self._unresolved_process_sources: dict[int, ProcessSupervisor] = {}
        self.workspace = workspace.resolve(strict=True)
        self.store = RunStore(self.workspace / ".doppel-agent", failure=self._mark_storage_unavailable,
                              cleanup_failure=self._retain_metadata_cleanup)
        self.conversations = ConversationStore(self.workspace / ".doppel-agent" / "conversations.sqlite3",
                                               failure=self._mark_metadata_uncertain,
                                               cleanup_failure=self._retain_metadata_cleanup)
        self.settings = SettingsStore(self.workspace / ".doppel-agent" / "provider-settings.json",
                                      failure=self._mark_settings_unavailable,
                                      cleanup_failure=self._retain_metadata_cleanup)
        self.jobs: dict[str, dict] = {}
        self._cores: dict[str, Core] = {}  # Original accepted cores, not an executor.
        self.brokers: dict[str, ApprovalBroker] = {}
        self.lock = threading.Lock()
        self._submissions = threading.Condition(self.lock)
        self._pending_submissions = 0
        self._pending_requests = 0
        self._request_scope = threading.local()
        self._closing = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="doppel-run")

    def close_owned(self) -> None:
        with self._submissions:
            self._closing = True
            self._submissions.notify_all()
            brokers = tuple(self.brokers.values())  # Original accepted broker identities before pool join.
        for broker in brokers:
            broker.close()  # Existing pending/queued approvals deny and wake, no new executor/effect.
        with self._submissions:
            self._submissions.wait_for(lambda: self._pending_submissions == 0 and self._pending_requests == 0)
        # Keep original accepted work, no forced future cancellation. Original
        # pool join alone cannot settle an uncertain submission/cleanup below.
        self.pool.shutdown(wait=True, cancel_futures=False)
        if self._submission_uncertain.is_set():
            raise RuntimeError('legacy_submission_unresolved')
        if (self._operation_cleanup_uncertain.is_set() or self.settings.cleanup_uncertain
                or self.conversations.cleanup_uncertain or self.store.cleanup_uncertain):
            raise RuntimeError('legacy_operation_cleanup_unresolved')

    @contextmanager
    def owned_request(self):
        """Original synchronous HTTP/manager request lease, not another executor.

        Nested same-thread calls inherit ONE original lease. Once admitted, keep
        payload/original IO until settlement; close refuses new requests and waits.
        No abort/deadline/retry or proof of unrelated SDK/native resources.
        """
        if getattr(self._request_scope, 'active', False):
            yield
            return
        with self._submissions:
            if self._closing:
                raise LegacyRequestClosing('Legacy workspace is closing')
            self._pending_requests += 1
        self._request_scope.active = True
        try:
            yield
        finally:
            self._request_scope.active = False
            with self._submissions:
                self._pending_requests -= 1
                self._submissions.notify_all()

    def _assert_effect_admission(self) -> None:
        if self._closing or self._operation_fault.is_set():
            raise LegacyOperationEvidenceError()
        if self._effect_admission is not None:
            self._effect_admission()

    def _mark_operation_uncertain(self, *, cleanup: bool = False) -> None:
        if self._effect_admission is None:
            return  # Original standalone contract, not borrowed runtime proof.
        self._operation_fault.set()
        if cleanup:
            self._operation_cleanup_uncertain.set()
        if self._effect_failure is not None:
            try:
                self._effect_failure()  # Original kernel/service monotonic latch, no IO.
            except BaseException:
                self._operation_cleanup_uncertain.set()
                raise LegacyOperationEvidenceError() from None

    def _mark_metadata_uncertain(self) -> None:
        # Original storage failures also quarantine standalone Legacy; unlike
        # ordinary standalone model errors, they cannot grant another write.
        self._mark_cleanup_uncertain()

    def _retain_metadata_cleanup(self, source: ConversationStore | TaskManager | SettingsStore | RunStore) -> None:
        # Original SQL/file/settings source can fail before assignment or reader
        # return. Retain SAME source, never a fresh store/SQL/file/iterator handle.
        with self._metadata_lifetime_lock:
            self._unresolved_metadata_sources[id(source)] = source
        self._operation_fault.set()
        self._operation_cleanup_uncertain.set()

    def _mark_settings_unavailable(self) -> None:
        self._operation_fault.set()  # Standalone owner also stops before another provider/key lookup.
        self._mark_operation_uncertain()

    def _retain_process_cleanup(self, source: ProcessSupervisor) -> None:
        with self._metadata_lifetime_lock:
            self._unresolved_process_sources[id(source)] = source
        self._mark_cleanup_uncertain()  # SAME native/kernel owner, including unpublished Core sources.

    def _mark_cleanup_uncertain(self) -> None:
        self._operation_fault.set()
        self._operation_cleanup_uncertain.set()
        self._mark_operation_uncertain(cleanup=True)

    def _mark_storage_unavailable(self) -> None:
        self._operation_fault.set()
        self._mark_operation_uncertain()

    def _mark_mcp_execution_unknown(self) -> None:
        self._operation_fault.set()  # Standalone original owner also refuses new effects.
        self._mark_operation_uncertain()  # SAME native/kernel/service fault; not cleanup failure.

    def _record_operation(self, identity: str, operation: str, *, outcome: str | None = None) -> None:
        if self._effect_admission is None:
            return
        try:
            # Finish is allowed for the SAME already-entered operation despite
            # later close/quarantine. It never grants a new request or clears fault.
            self.store.append_operation(identity, operation, outcome=outcome)
        except BaseException:
            self._mark_operation_uncertain(cleanup=True)
            raise LegacyOperationEvidenceError() from None

    @contextmanager
    def _original_probe_operation(self):
        if self._effect_admission is None:
            yield
            return
        identity = uuid4().hex  # Explicit original probe ID, not Core/SQL run.
        self._record_operation(identity, 'probe')
        try:
            yield
        except LegacyOperationEvidenceError:
            raise
        except BaseException:
            self._mark_operation_uncertain()
            self._record_operation(identity, 'probe', outcome='failed')
            raise
        else:
            self._record_operation(identity, 'probe', outcome='returned')

    def _admitted_provider(self, original):
        if self._effect_admission is None:
            return original
        return _LegacyAdmittedProvider(original, self._assert_effect_admission)

    def _provider(self, config: dict):
        self._assert_effect_admission()  # Before profile/key/client lookup.
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
        with self.owned_request():
            return self._probe_owned(config)

    def _probe_owned(self, config: dict) -> dict:
        self._assert_effect_admission()
        provider = self._provider(config)
        if isinstance(provider, MockProvider):
            return {"ok": True, "reply": "Mock provider is ready (offline only)."}
        try:
            with self._original_probe_operation():
                turn = self._admitted_provider(provider).next_turn(
                    [Message("user", "Reply briefly: Doppel Agent API connection OK")], [])
                content = turn.content
                if type(content) is not str:
                    raise TypeError('invalid original probe reply')
                reply = content[:1000]
        except LegacyOperationEvidenceError:
            raise
        except Exception:
            # Method entry failed/lost result isn't no-charge/no-effect. Never
            # echo arbitrary SDK/body/key errors or retry an explicit probe.
            raise RuntimeError('provider_probe_unavailable') from None
        return {"ok": True, "reply": reply}

    def submit(self, data: dict) -> dict:
        with self.owned_request():
            return self._submit_owned(data)

    def _submit_owned(self, data: dict) -> dict:
        self._assert_effect_admission()
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100_000:
            raise ValueError("prompt must contain 1 to 100000 characters")
        config = data.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        provider = self._admitted_provider(self._provider(config))
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
            if self._closing:
                raise RuntimeError("Legacy workspace is closing")
            self._assert_effect_admission()  # Before original job/conversation acceptance.
            active = sum(job["status"] in ("queued", "running") for job in self.jobs.values())
            if active >= 2:
                raise RuntimeError("two runs are already active")
            run_id = uuid4().hex
            self._record_operation(run_id, 'run')  # Before conversation/job/pool acceptance.
            try:
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
                self._pending_submissions += 1
            except BaseException:
                self._mark_operation_uncertain(cleanup=True)
                if self._effect_admission is not None:
                    raise LegacyOperationEvidenceError() from None
                raise

        def run() -> None:
            core = None
            with self.lock:
                self.jobs[run_id]["status"] = "running"
            try:
                try:
                    self._assert_effect_admission()  # Before queued original Core setup.
                    core = Core(
                        self.workspace, provider, allow_write=allow_write, allow_command=allow_command,
                        allow_mcp=allow_mcp, allow_delegate=allow_delegate,
                        approver=self.brokers[run_id].request,
                        max_steps=max_steps,
                        review_mode=mode == "review",
                        check_cancelled=self._assert_effect_admission,
                        mcp_cleanup_failure=self._mark_cleanup_uncertain,
                        mcp_execution_failure=self._mark_mcp_execution_unknown,
                        mcp_publication_failure=self._mark_mcp_execution_unknown,
                        task_failure=self._mark_metadata_uncertain,
                        task_cleanup_failure=self._retain_metadata_cleanup,
                        storage_failure=self._mark_storage_unavailable,
                        storage_cleanup_failure=self._retain_metadata_cleanup,
                        process_failure=self._mark_cleanup_uncertain,
                        process_cleanup_failure=self._retain_process_cleanup,
                    )
                    with self.lock:
                        self._cores[run_id] = core
                    result = core.run(prompt, run_id=run_id, history=history)
                except Exception as exc:
                    result = {"run_id": run_id, "status": "failed", "answer": f"{type(exc).__name__}: {exc}"}
                result["prompt"] = prompt
                result["conversation_id"] = conversation_id
                self.store.write_session(run_id, result)
                if conversation_id:
                    self.conversations.add_message(conversation_id, "assistant", result["answer"], run_id, model=model)
                outcome = 'returned' if result['status'] == 'completed' else 'failed'
                if outcome == 'failed':
                    self._mark_operation_uncertain()
                self._record_operation(run_id, 'run', outcome=outcome)
                with self.lock:
                    self.jobs[run_id] = result
            except BaseException:
                self._mark_operation_uncertain(cleanup=True)
                if self._effect_admission is not None:
                    with self.lock:
                        self.jobs[run_id] = {'run_id': run_id, 'status': 'failed',
                            'answer': 'legacy_operation_evidence_unavailable',
                            'prompt': prompt, 'conversation_id': conversation_id}
                # Original worker failure remains failure; no session/marker
                # retry or false completed publication after unknown store close.
                raise
            finally:
                if (core is not None and not core.mcp_cleanup_failed and not core.mcp_execution_failed
                        and not core.mcp_publication_failed and not core.task_persistence_failed
                        and not core.storage_persistence_failed and not core.process_cleanup_uncertain):
                    with self.lock:
                        self._cores.pop(run_id, None)
                # Failed original Core -> Bridge -> Manager/SDK task retained.
                # Original pool completion is NOT physical cleanup proof.

        try:
            self.pool.submit(run)
        except BaseException:
            if self._effect_admission is not None:
                # submit can throw after queueing the original work item. The
                # original pool still drains; missing returned handle is NOT
                # never-entered/no-effect or replacement submission authority.
                self._submission_uncertain.set()
                self._mark_operation_uncertain()
                raise LegacyOperationEvidenceError() from None
            raise
        finally:
            with self._submissions:
                self._pending_submissions -= 1
                self._submissions.notify_all()
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
        with self.owned_request():
            return self._save_settings_owned(data)

    def delete_settings(self, profile_id: str) -> dict:
        with self.owned_request():
            self._assert_effect_admission()
            return self.settings.delete_profile(profile_id)

    def _save_settings_owned(self, data: dict) -> dict:
        self._assert_effect_admission()  # Before private payload/key/store mutation.
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
        return self.store.read_events(run_id)

    def tasks(self, run_id: str) -> list[dict] | None:
        if self.status(run_id) is None:
            return None
        return TaskManager.read_existing(self.workspace / ".doppel-agent" / "tasks.sqlite3", run_id,
                                         failure=self._mark_metadata_uncertain,
                                         cleanup_failure=self._retain_metadata_cleanup)

    def approvals(self, run_id: str) -> list[dict] | None:
        broker = self.brokers.get(run_id)
        if broker:
            return broker.list_pending()
        return [] if self.status(run_id) is not None else None

    def decide(self, run_id: str, approval_id: str, allow: bool) -> bool:
        with self.owned_request():
            if allow:
                self._assert_effect_admission()
            # Explicit denial of SAME original pending broker remains available.
            broker = self.brokers.get(run_id)
            return bool(broker and broker.decide(approval_id, allow))


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], workspace: Path, *,
                 effect_admission: Callable[[], None] | None = None,
                 effect_failure: Callable[[], None] | None = None):
        self._effect_failure = effect_failure
        self._socket_cleanup_uncertain = threading.Event()
        self._socket_start_attempted = False
        self._socket_close_attempted = False
        self._socket_close_returned = False
        self._startup_cleanup_sources: dict[int, ConversationStore | TaskManager | SettingsStore | RunStore] = {}
        self._socket_start_attempted = True  # BEFORE original socket/server factory.
        try:
            # SAME original socket factory/bind/activate, expanded to prevent
            # TCPServer's constructor calling our manager-dependent override
            # before manager exists. No second socket/server or retry.
            super().__init__(address, ConsoleHandler, bind_and_activate=False)
            self.server_bind()
            self.server_activate()
            self.manager = JobManager(workspace, effect_admission=effect_admission, effect_failure=effect_failure)
        except BaseException as exc:
            if (isinstance(exc, (ConversationPersistenceError, TaskPersistenceError, SettingsPersistenceError,
                                 LegacyStorageReadError)) and exc.source is not None
                    and exc.source.resource_cleanup_uncertain):
                # Keep original failed source too if subsequent socket cleanup
                # masks constructor error. Socket exit isn't SQL/file/iterator exit.
                self._startup_cleanup_sources[id(exc.source)] = exc.source
            if getattr(self, 'socket', None) is None:
                self._mark_socket_uncertain()  # Unreturned opaque allocation isn't known closure.
                raise LegacyServerCleanupError(self) from None
            self._close_socket_original()
            raise

    @property
    def cleanup_uncertain(self) -> bool:
        return self._socket_cleanup_uncertain.is_set() or bool(self._startup_cleanup_sources)

    def _mark_socket_uncertain(self) -> None:
        self._socket_cleanup_uncertain.set()
        if self._effect_failure is not None:
            try:
                self._effect_failure()  # Same original owner, memory-only, no restart/IO.
            except BaseException:
                pass

    def _close_socket_original(self) -> None:
        if self._socket_close_returned:
            return
        if self._socket_close_attempted or self._socket_cleanup_uncertain.is_set():
            raise LegacyServerCleanupError(self)  # Never second close after unknown return.
        self._socket_close_attempted = True
        try:
            super().server_close()
            self._socket_close_returned = True
        except BaseException:
            self._mark_socket_uncertain()
            raise LegacyServerCleanupError(self) from None

    def server_close(self) -> None:
        # Same original accepted POST/probe/save/submission/pool futures must
        # settle. Native ThreadingMixIn then additionally joins non-daemon HTTP
        # handlers; standalone daemon GETs aren't native/physical drain proof.
        if self.cleanup_uncertain:
            raise LegacyServerCleanupError(self)
        self.manager.close_owned()
        self._close_socket_original()


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
        try:
            with self.server.manager.owned_request():
                try:
                    self._do_GET_owned()
                except ConversationPersistenceError:
                    self._json(503, {"error": "legacy_metadata_persistence_unavailable"})
                except LegacyStorageReadError:
                    self._json(503, {"error": "legacy_storage_evidence_unavailable"})
                except TaskPersistenceError:
                    self._json(503, {"error": "legacy_task_persistence_unavailable"})
        except LegacyRequestClosing:
            self._json(503, {"error": "Legacy workspace is closing"})

    def _do_GET_owned(self) -> None:
        # Same original read plus reply lease. Provider quarantine does not ban
        # metadata reads, and socket reply errors do not become SQLite failures.
        parsed = urlparse(self.path)
        path = parsed.path
        assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "application/javascript; charset=utf-8")}
        # Stable compatibility paths; never reinterpret or migrate Legacy data.
        assets.update({"/legacy": assets["/"], "/legacy/": assets["/"],
                       "/legacy/app.css": assets["/app.css"], "/legacy/app.js": assets["/app.js"]})
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
            try:
                candidate = (root / relative).resolve()
            except (OSError, ValueError):
                self._json(404, {"error": "not found"})
                return
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
            try:
                self._json(200, self.server.manager.public_settings())
            except SettingsPersistenceError:
                self._json(503, {"error": "provider_settings_persistence_unavailable"})
        elif path == "/api/workspace-selection":
            self._json(200, self.server.manager.conversations.selection())
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
            with self.server.manager.owned_request():
                self._do_POST_owned()
        except LegacyRequestClosing:
            self._reject_post(503, 'Legacy workspace is closing')

    def _do_POST_owned(self) -> None:
        # Original host/origin/JSON checks precede admission. Body read, settings
        # deletion, conversation mutations, approval decision and JSON reply
        # remain inside this SAME original HTTP request lease, including errors.
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 128 * 1024:
                raise ValueError("request body exceeds limit")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
            path = urlparse(self.path).path
            parts = path.split("/")
            original_decision = (len(parts) == 7 and parts[1:3] == ["api", "runs"]
                                 and parts[4] == "approvals" and parts[6] == "decision")
            if not original_decision:
                # Metadata GETs and exact old approval decisions keep their
                # original paths; all new POST effects use the borrowed owner.
                self.server.manager._assert_effect_admission()
            if path == "/api/probe":
                self._json(200, self.server.manager.probe(body.get("config", {})))
            elif path == "/api/settings":
                self._json(200, self.server.manager.save_settings(body))
            elif path == "/api/workspace-selection":
                if set(body) != {"conversation_id"}:
                    raise ValueError("conversation_id is required; extra fields are not allowed")
                self._json(200, self.server.manager.conversations.save_selection(body["conversation_id"]))
            elif path.startswith("/api/settings/profiles/") and path.endswith("/delete"):
                profile_id = path.split("/")[4]
                self._json(200, self.server.manager.delete_settings(profile_id))
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
        except SettingsPersistenceError:
            self._json(503, {"error": "provider_settings_persistence_unavailable"})
        except ConversationPersistenceError:
            self._json(503, {"error": "legacy_metadata_persistence_unavailable"})
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
