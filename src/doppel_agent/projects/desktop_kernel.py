"""One owned desktop hybrid kernel; no WebView/window management here."""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import threading
import time
from pathlib import Path

from ..persistence.ownership import WorkspaceOwner
from ..persistence.tool_ledger import ToolLedgerPersistenceError
from ..conversations import ConversationPersistenceError
from ..settings import SettingsPersistenceError
from ..storage import LegacyStorageReadError
from ..tasks.manager import TaskPersistenceError
from ..web.server import LegacyServerCleanupError


class DesktopKernel:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        self.owner = WorkspaceOwner(self.workspace / '.doppel-agent' / 'local-mode.lock',
                                    failure=self._mark_legacy_uncertain,
                                    cleanup_failure=self._retain_owner_cleanup)
        self.legacy = None
        self.legacy_worker = None
        self.api_socket = None
        self._api_socket_close_attempted = False
        self._api_socket_close_returned = False
        self._api_socket_cleanup_uncertain = False
        self.api = None
        self.api_worker = None
        self.app = None
        self._legacy_started = False
        self._api_started = False
        self._legacy_closer = None
        self._legacy_closer_started = False
        self._worker_start_attempts: dict[str, str] = {}
        self._legacy_error = None
        self._api_error = None
        self._closed = False
        self._closing = threading.Event()
        self._startup_cleanup_sources: dict[int, object] = {}

    @property
    def ready(self) -> bool:
        return bool(not self._closed and not self._closing.is_set() and self.api and self.api.started
                    and not getattr(self.api, "should_exit", False))

    def _native_git_metadata(self, action: str, selected=None) -> dict:
        # Only WindowApi's origin-bound native controls call this, never HTTP.
        state = getattr(self.app, "state", None)
        loop = getattr(state, "runtime_loop", None)
        service = getattr(state, "run_service", None)
        if (not self.ready or not self.owner.held or loop is None or not loop.is_running()
                or service is None or getattr(state, "runtime_thread_id", None) == threading.get_ident()):
            raise RuntimeError("native_git_metadata_unavailable")
        methods = {"grant": service._grant_git_metadata, "revoke": service._revoke_git_metadata,
                   "read": service._get_git_metadata_grant}
        if action not in methods or action != "grant" and selected is not None:
            raise ValueError("invalid_native_git_metadata_operation")

        async def original_loop():
            if not self.ready or not self.owner.held:
                raise RuntimeError("native_git_metadata_unavailable")
            return await methods[action](selected) if action == "grant" else await methods[action]()

        operation = original_loop()
        try:
            future = asyncio.run_coroutine_threadsafe(operation, loop)
        except BaseException:
            operation.close()
            raise
        # No fake timeout/foreign asyncio.run or abandoned late grant. Join original
        # owner-loop admission/metadata workers. Close still fences/joins service.
        while True:
            try:
                return future.result(timeout=0.2)
            except FutureTimeoutError:
                if future.done():
                    # A completed operation can itself raise TimeoutError; do
                    # not mistake that exception for an unfinished poll forever.
                    return future.result()
                # This poll is NOT an operation deadline. Only actual terminal
                # owner-loop evidence ends a lost dispatch; never start a new loop
                # or another request merely because a metadata call is slow.
                if not loop.is_running():
                    self._closing.set()
                    future.cancel()
                    raise RuntimeError("native_git_metadata_owner_loop_ended") from None

    @property
    def url(self) -> str:
        if self.api_socket is None:
            raise RuntimeError("desktop kernel is not available")
        return f"http://127.0.0.1:{self.api_socket.getsockname()[1]}/"

    def _assert_legacy_admission(self) -> None:
        """Passive same-owner boundary guard callable by original HTTP/Core.

        No dispatch/new loop/recovery/provider lookup. The original fault's Event
        is monotonic; checks aren't OS drain or hidden SDK retry observations.
        This guard is bound at Legacy construction, not after HTTP exposure.
        """
        state = getattr(self.app, "state", None)
        service = getattr(state, "run_service", None)
        loop = getattr(state, "runtime_loop", None)
        if (not self.ready or not self.owner.held or service is None
                or loop is None or not loop.is_running()
                or not service._started or service._closed
                or service._close_task is not None or service.cleanup_complete):
            raise RuntimeError("provider_receipt_unavailable")
        service._assert_process_admission()
        service._assert_provider_admission()

    def _start_owned_worker(self, worker_name: str, started_flag: str) -> None:
        # Record BEFORE calling start: an interrupted/failed start may already
        # have launched this exact original thread. Never infer never-entered
        # lifespan or create another cleanup worker from the missing return.
        worker = getattr(self, worker_name)
        self._worker_start_attempts[worker_name] = started_flag
        try:
            worker.start()
        finally:
            if getattr(worker, 'ident', None) is not None or worker.is_alive():
                setattr(self, started_flag, True)
        setattr(self, started_flag, True)  # Successful original start return.

    def _mark_legacy_uncertain(self) -> None:
        # Same monotonic provider fault used by runtime/children/admission.
        # Original Legacy writer calls this after unknown failure, not a new
        # journal, loop dispatch, provider lookup or retry/reset authority.
        service = getattr(getattr(self.app, 'state', None), 'run_service', None)
        if service is None:
            self._closing.set()
            return
        service._provider_receipt_fault.mark_failed()

    def _retain_project_catalog_cleanup(self, source) -> None:
        self._startup_cleanup_sources[id(source)] = source  # Exact original SQL/failed constructor.
        self._closing.set()  # Fence native/Legacy admission before any owner release.
        service = getattr(getattr(self.app, 'state', None), 'run_service', None)
        if service is not None:
            service._provider_receipt_fault.retain_cleanup(source)

    def _retain_owner_cleanup(self, source) -> None:
        self._startup_cleanup_sources[id(source)] = source
        self._closing.set()
        service = getattr(getattr(self.app, 'state', None), 'run_service', None)
        if service is not None:
            service._provider_receipt_fault.retain_cleanup(source)

    def _require_known_worker_starts(self) -> None:
        for worker_name, started_flag in self._worker_start_attempts.items():
            if getattr(self, started_flag):
                continue
            worker = getattr(self, worker_name)
            if getattr(worker, 'ident', None) is not None or worker.is_alive():
                setattr(self, started_flag, True)
            else:
                # No registered identity is NOT proof an attempted launch can
                # never enter later. Retain socket/owner and same handle, no
                # speculative foreign-loop cleanup, restart or replacement.
                raise RuntimeError('desktop worker startup is unresolved; workspace owner retained')

    def start(self) -> None:
        import uvicorn
        from ..api import create_app
        from ..desktop import _bind_browser_api_socket, BrowserSocketCleanupError, ConsoleServer

        if self._closed or self._closing.is_set() or self.owner.held:
            raise RuntimeError("desktop kernel cannot start twice")
        self.owner.path.parent.mkdir(parents=True, exist_ok=True)
        self.owner.acquire()  # Before constructing either store/service/server.
        try:
            self.legacy = ConsoleServer(("127.0.0.1", 0), self.workspace,
                                        effect_admission=self._assert_legacy_admission,
                                        effect_failure=self._mark_legacy_uncertain)
            # ThreadingMixIn.server_close must join owned native HTTP handlers.
            self.legacy.daemon_threads = False
            self.legacy_worker = threading.Thread(target=self.legacy.serve_forever, name="doppel-legacy-ui", daemon=True)
            self._start_owned_worker('legacy_worker', '_legacy_started')
            self.app = create_app(self.workspace, legacy_base_url=f"http://127.0.0.1:{self.legacy.server_port}",
                                  workspace_owner=self.owner.borrow())
            self.api_socket = _bind_browser_api_socket()
            self.api = uvicorn.Server(uvicorn.Config(self.app, log_level="warning", access_log=False, lifespan="on"))
            self.api_worker = threading.Thread(target=self._serve_api, name="doppel-api", daemon=True)
            self._start_owned_worker('api_worker', '_api_started')
            for _ in range(100):
                if self.api.started or not self.api_worker.is_alive():
                    break
                time.sleep(0.02)
            if not self.api.started:
                raise RuntimeError("Doppel runtime API failed to start")
        except BaseException as exc:
            source = None
            if isinstance(exc, BrowserSocketCleanupError):
                source = exc.source  # Exact original pre-factory/failed-close candidate before unwind.
            elif isinstance(exc, (ToolLedgerPersistenceError, LegacyServerCleanupError)):
                if exc.source is not None and exc.source.cleanup_uncertain:
                    source = exc.source
            elif isinstance(exc, (ConversationPersistenceError, TaskPersistenceError,
                                  SettingsPersistenceError, LegacyStorageReadError)):
                if exc.source is not None and exc.source.resource_cleanup_uncertain:
                    source = exc.source
            if source is not None:
                # ConsoleServer/JobManager or create_app/RunService did not
                # return. Keep SAME original SQL/socket/constructor source BEFORE
                # cleanup; missing app/legacy/worker is NOT physical resource exit.
                self._startup_cleanup_sources[id(source)] = source
            self.close()
            raise

    def _serve_api(self) -> None:
        try:
            self.api.run(sockets=[self.api_socket])
        except BaseException as exc:
            self._api_error = exc

    def _close_legacy(self) -> None:
        try:
            manager = getattr(self.legacy, "manager", None)
            if manager is not None:
                manager.close_owned()
            self.legacy.server_close()
        except BaseException as exc:
            self._legacy_error = exc

    def close(self) -> None:
        if self._closed:
            return
        self._closing.set()
        if self._api_socket_cleanup_uncertain:
            raise RuntimeError('desktop listener cleanup is unresolved; workspace owner retained') from None
        service = getattr(getattr(self.app, "state", None), "run_service", None)
        stop_streams = getattr(service, "request_stream_shutdown", None)
        if stop_streams is not None:
            stop_streams()  # Pure same-lifetime signal; actual workers/SQL/owner still joined below.
        if self.api is not None:
            self.api.should_exit = True
        self._require_known_worker_starts()
        if self.legacy is not None and self._legacy_closer is None:
            if self._legacy_started:
                self.legacy.shutdown()
            self._legacy_closer = threading.Thread(target=self._close_legacy, name="doppel-legacy-drain", daemon=True)
            self._start_owned_worker('_legacy_closer', '_legacy_closer_started')
        for worker, started in ((self.api_worker, self._api_started), (self.legacy_worker, self._legacy_started),
                                (self._legacy_closer, self._legacy_closer_started)):
            if worker is not None and started:
                worker.join(timeout=15)
                if worker.is_alive():
                    raise RuntimeError("desktop cleanup is still draining; workspace owner retained")
        service = getattr(getattr(self.app, "state", None), "run_service", None)
        if service is not None:
            # A terminal API thread may have failed before entering lifespan.
            # Only that explicit never-entered state is safe to close on a fresh
            # event loop. Never retry a started service's failed async cleanup.
            never_entered = not getattr(self.app.state, "runtime_lifespan_entered", True)
            if (not self._api_started or never_entered) and not service.cleanup_complete:
                asyncio.run(service.close())
            if not service.cleanup_complete:
                raise RuntimeError("runtime cleanup failed; workspace owner retained")
        if self._legacy_error is not None:
            raise RuntimeError("Legacy cleanup failed; workspace owner retained") from None
        if self._startup_cleanup_sources:
            raise RuntimeError("desktop startup cleanup is unresolved; workspace owner retained") from None
        if self.api_socket is not None:
            if not self._api_socket_close_attempted:
                self._api_socket_close_attempted = True  # BEFORE original listener close.
                try:
                    self.api_socket.close()
                    self._api_socket_close_returned = True
                except BaseException:
                    self._api_socket_cleanup_uncertain = True
                    self._startup_cleanup_sources[id(self.api_socket)] = self.api_socket
                    raise RuntimeError('desktop listener cleanup is unresolved; workspace owner retained') from None
            if not self._api_socket_close_returned:
                raise RuntimeError('desktop listener cleanup is unresolved; workspace owner retained') from None
        self.owner.release()
        self._closed = True
