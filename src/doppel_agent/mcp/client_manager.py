"""MCP SDK session lifecycle, health checks, reconnects and concurrency."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from .config import MCPConfig, MCPServerConfig
from .types import MCPServerMetadata, ManagedMCPSession


Connector = Callable[[MCPServerConfig], Any]


class MCPCleanupError(RuntimeError):
    def __init__(self):
        super().__init__("mcp_cleanup_unresolved")


class MCPAdmissionError(RuntimeError):
    """Known refused entry/self-wait; not evidence of failed SDK cleanup."""

    def __init__(self):
        super().__init__("mcp_admission_closed")


class MCPInvocationError(RuntimeError):
    """Entered SDK tool failed without a usable reply; not SDK exit proof."""

    def __init__(self):
        super().__init__("mcp_execution_unresolved")


class MCPPublicationError(RuntimeError):
    """Local result/receipt publication unresolved; not remote or SDK exit proof."""

    def __init__(self):
        super().__init__("mcp_publication_unresolved")


@dataclass(frozen=True)
class _MCPSessionSource:
    """Ephemeral SAME original manager/session observation, not a receipt.

    Does not certify remote outcome, provider usage, schema immutability or
    physical SDK/native closure. Never relabel a result using a later get().
    """

    manager: MCPClientManager
    name: str
    managed: ManagedMCPSession
    metadata: MCPServerMetadata
    cache_key: str
    generation: int


@dataclass(frozen=True)
class _MCPCallObservation:
    value: Any
    source: _MCPSessionSource


async def _drain_owner(task: asyncio.Task[None]) -> None:
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


@dataclass
class _ConnectionLifetime:
    """Private observations made on the SAME original connector/SDK stack.

    A successful owner task can have swallowed a pre-ready exception. It is
    not closure evidence. An opaque unreturned enter is never cleared by a
    surrounding empty stack exit. These are source-level returns, not native
    process/port or physical SDK transport drain proof.
    """

    enter_returned: bool = False
    exit_returned: bool = False
    builtin_started: bool = False
    sdk_enter_attempted: bool = False
    sdk_enter_pending: bool = False
    sdk_exit_returned: bool = False
    sdk_exit_failed: bool = False

    @property
    def known_closed(self) -> bool:
        return (
            self.exit_returned
            and not self.sdk_exit_failed
            and (
                self.enter_returned
                or (
                    self.builtin_started
                    and (
                        not self.sdk_enter_attempted
                        or (self.sdk_exit_returned and not self.sdk_enter_pending)
                    )
                )
            )
        )


class _ObservedExitStack(AsyncExitStack):
    """Observe original enter/exit returns without replacing SDK ownership."""

    def __init__(self, lifetime: _ConnectionLifetime, *, sdk: bool = False):
        super().__init__()
        self._lifetime = lifetime
        self._sdk = sdk

    async def enter_async_context(self, cm):
        if self._sdk:
            self._lifetime.sdk_enter_attempted = True
            self._lifetime.sdk_enter_pending = True
        value = await super().enter_async_context(cm)
        if self._sdk:
            self._lifetime.sdk_enter_pending = False
        else:
            self._lifetime.enter_returned = True
        return value

    async def enter_sdk_context(self, factory: Callable[[], Any]):
        # Observe before argument evaluation: an opaque resource constructor
        # can fail after an allocation too, not only during __aenter__.
        self._lifetime.sdk_enter_attempted = True
        self._lifetime.sdk_enter_pending = True
        return await self.enter_async_context(factory())

    async def __aexit__(self, *exc_details):
        result = await super().__aexit__(*exc_details)
        if self._sdk:
            self._lifetime.sdk_exit_returned = True
        else:
            self._lifetime.exit_returned = True
        return result

    def _push_async_cm_exit(self, cm, cm_exit):
        if not self._sdk:
            return super()._push_async_cm_exit(cm, cm_exit)

        async def observed_exit(*exc_details):
            try:
                return await cm_exit(cm, *exc_details)
            except BaseException:
                # A later SDK exit may suppress this exception. Preserve the
                # actual earlier failed exit, not only aggregate stack return.
                self._lifetime.sdk_exit_failed = True
                raise

        self.push_async_exit(observed_exit)


@dataclass
class _OwnedConnection:
    """SDK cancel scopes must enter and exit in the same lifetime task."""

    ready: asyncio.Future[tuple[Any, Any]]
    release: asyncio.Event
    task: asyncio.Task[None]
    lifetime: _ConnectionLifetime
    operations_idle: asyncio.Event
    borrowers: dict[asyncio.Task[Any], int] = field(default_factory=dict)

    def begin_operation(self) -> asyncio.Task[Any]:
        task = asyncio.current_task()
        if task is None:
            raise MCPAdmissionError()
        self.borrowers[task] = self.borrowers.get(task, 0) + 1
        self.operations_idle.clear()
        return task

    def finish_operation(self, task: asyncio.Task[Any]) -> None:
        remaining = self.borrowers[task] - 1
        if remaining:
            self.borrowers[task] = remaining
        else:
            self.borrowers.pop(task)
        if not self.borrowers:
            self.operations_idle.set()

    async def close(self) -> None:
        self.release.set()
        await _drain_owner(self.task)
        if not self.lifetime.known_closed:
            raise MCPCleanupError()


class MCPClientManager:
    def __init__(
        self,
        config: MCPConfig,
        *,
        connector: Connector | None = None,
        connection_timeout_seconds: float = 10,
        failure: Callable[[], None] | None = None,
        execution_failure: Callable[[], None] | None = None,
        publication_failure: Callable[[], None] | None = None,
    ) -> None:
        if connection_timeout_seconds <= 0:
            raise ValueError("MCP connection timeout must be positive")
        self.connection_timeout_seconds = connection_timeout_seconds
        self.config = config
        self._builtin_connector = self._default_connector
        self.connector = connector or self._builtin_connector
        self._clients: dict[str, ManagedMCPSession] = {}
        self._connections: dict[str, _OwnedConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._semaphores = {
            name: asyncio.Semaphore(server.max_concurrency) for name, server in config.servers.items()
        }
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._cleanup_failed = Event()
        self._failure = failure
        self._execution_failed = Event()
        self._execution_failure = execution_failure
        self._publication_failed = Event()
        self._publication_failure = publication_failure

    @property
    def cleanup_failed(self) -> bool:
        return self._cleanup_failed.is_set()

    @property
    def execution_failed(self) -> bool:
        return self._execution_failed.is_set()

    @property
    def publication_failed(self) -> bool:
        return self._publication_failed.is_set()

    def _mark_publication_failed(self) -> None:
        if self._publication_failed.is_set():
            return
        self._publication_failed.set()
        if self._publication_failure is not None:
            try:
                self._publication_failure()  # Same owner fence; no IO/reset/remote claim.
            except BaseException:
                pass

    def _mark_execution_failed(self) -> None:
        if self._execution_failed.is_set():
            return
        self._execution_failed.set()
        if self._execution_failure is not None:
            try:
                self._execution_failure()  # SAME owner stop before result/cleanup; no IO/retry.
            except BaseException:
                pass

    def _mark_cleanup_failed(self) -> None:
        self._cleanup_failed.set()
        if self._failure is not None:
            try:
                self._failure()  # Same original owner latch, no lookup/IO/retry.
            except BaseException:
                pass

    def _check_cleanup(self) -> None:
        if self.cleanup_failed:
            raise MCPCleanupError()

    def _check_admission(self) -> None:
        self._check_cleanup()
        if self.execution_failed:
            raise MCPInvocationError()
        if self.publication_failed:
            raise MCPPublicationError()

    def _check_reentrant(self, name: str | None = None) -> None:
        # Do not seal or wait for an SDK lifetime owned by this very caller's
        # still-entered session. Such a wait cannot finish until we return.
        task = asyncio.current_task()
        connections = self._connections.values() if name is None else (self._connections.get(name),)
        if any(connection is not None and task in connection.borrowers for connection in connections):
            raise MCPAdmissionError()

    @asynccontextmanager
    async def _default_connector(
        self, server: MCPServerConfig, *, lifetime: _ConnectionLifetime | None = None
    ) -> AsyncIterator[tuple[Any, Any]]:
        lifetime = lifetime if lifetime is not None else _ConnectionLifetime()
        lifetime.builtin_started = True
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
        from mcp.client.streamable_http import streamable_http_client

        async with _ObservedExitStack(lifetime, sdk=True) as stack:
            if server.transport == "stdio":
                missing = [name for name in server.env_names if name not in os.environ]
                if missing:
                    raise ValueError(f"missing MCP environment variables: {', '.join(missing)}")
                environment = get_default_environment()
                environment.update({name: os.environ[name] for name in server.env_names})
                read_stream, write_stream = await stack.enter_sdk_context(
                    lambda: stdio_client(
                        StdioServerParameters(
                            command=server.command or "",
                            args=list(server.args),
                            env=environment,
                            cwd=server.cwd,
                        )
                    )
                )
            else:
                http_client = None
                if server.auth_profile:
                    import httpx2

                    variable = "DOPPEL_MCP_AUTH_" + server.auth_profile.upper().replace("-", "_") + "_TOKEN"
                    token = os.environ.get(variable)
                    if not token:
                        raise ValueError(f"missing MCP auth profile environment: {variable}")
                    http_client = await stack.enter_sdk_context(
                        lambda: httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
                    )
                streams = await stack.enter_sdk_context(
                    lambda: streamable_http_client(server.url or "", http_client=http_client)
                )
                read_stream, write_stream = streams[0], streams[1]
            session = await stack.enter_sdk_context(lambda: ClientSession(read_stream, write_stream))
            initialized = await session.initialize()
            yield session, initialized

    @staticmethod
    def _metadata(server: MCPServerConfig, initialized: Any) -> MCPServerMetadata:
        info = getattr(initialized, "server_info", None)
        capabilities = getattr(initialized, "capabilities", None)
        if hasattr(capabilities, "model_dump"):
            capabilities = capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
        return MCPServerMetadata(
            name=server.name,
            protocol_version=str(getattr(initialized, "protocol_version", "unknown")),
            server_name=str(getattr(info, "name", server.name)),
            server_version=str(getattr(info, "version", "unknown")),
            capabilities=dict(capabilities or {}),
            config_hash=server.identity_hash(),
        )

    async def get(self, name: str) -> ManagedMCPSession:
        self._check_admission()
        self._check_reentrant(name)
        if self._closed:
            raise MCPAdmissionError()
        if name not in self.config.servers:
            raise KeyError(f"unknown MCP server: {name}")
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            self._check_admission()
            if self._closed:
                raise MCPAdmissionError()
            existing = self._clients.get(name)
            if existing is not None and existing.valid:
                return existing
            await self._discard(name)
            # A discard may have awaited another original operation. Do not
            # allocate a replacement after global close/fault sealed admission.
            self._check_admission()
            if self._closed:
                raise MCPAdmissionError()
            ready = asyncio.get_running_loop().create_future()
            release = asyncio.Event()
            lifetime = _ConnectionLifetime()
            operations_idle = asyncio.Event()
            operations_idle.set()

            async def own_connection() -> None:
                try:
                    async with _ObservedExitStack(lifetime) as stack:
                        # Do not time-limit a healthy session's lifetime. Only
                        # connection/initialize is bounded; SDK enter/exit remain
                        # on this single owned task even when a caller cancels.
                        async with asyncio.timeout(self.connection_timeout_seconds):
                            if self.connector is self._builtin_connector:
                                context = self._builtin_connector(
                                    self.config.servers[name], lifetime=lifetime
                                )
                            else:
                                # Custom/native fixtures cannot assert SDK closure
                                # by returning private bookkeeping of their own.
                                context = self.connector(self.config.servers[name])
                            opened = await stack.enter_async_context(context)
                        ready.set_result(opened)
                        await release.wait()
                        # SAME SDK owner task exits only after SAME original
                        # managed call/session bodies have actually returned.
                        # No cancellation of borrowed calls/second executor.
                        await operations_idle.wait()
                    if not lifetime.known_closed:
                        # Even a suppressed inner SDK exit failure is unknown.
                        raise MCPCleanupError()
                except BaseException as original_error:
                    if not lifetime.known_closed:
                        self._mark_cleanup_failed()  # Before ready/caller/result publication.
                    failure = MCPCleanupError() if not lifetime.known_closed else original_error
                    if not ready.done():
                        ready.set_exception(failure)
                    else:
                        if not lifetime.known_closed:
                            raise failure from None
                        raise

            connection = _OwnedConnection(
                ready, release, asyncio.create_task(own_connection()), lifetime, operations_idle
            )
            self._connections[name] = connection
            try:
                session, initialized = await asyncio.shield(ready)
                # The original startup may have been admitted before a close/
                # sibling fault. Drain that SAME owner in the exception path,
                # but do not publish a newly usable session after the fence.
                self._check_admission()
                if self._closed:
                    raise MCPAdmissionError()
            except BaseException:
                try:
                    await self._discard(name)
                finally:
                    # Even a repeatedly cancelled caller must consume startup errors.
                    if ready.done() and not ready.cancelled():
                        ready.exception()
                raise
            managed = ManagedMCPSession(
                session=session,
                metadata=self._metadata(self.config.servers[name], initialized),
                semaphore=self._semaphores[name],
            )
            self._clients[name] = managed
            self._generations[name] = self._generations.get(name, 0) + 1
            return managed

    def generation(self, name: str) -> int:
        """Monotonic in-process session generation used by schema caches."""
        return self._generations.get(name, 0)

    def check_source(self, source: _MCPSessionSource) -> None:
        """Memory-only original identity check; no get/reconnect/probe/IO."""
        self._check_admission()
        connection = self._connections.get(source.name)
        if (
            source.manager is not self
            or self._closed
            or connection is None
            or connection.release.is_set()
            or not source.managed.valid
            or self._clients.get(source.name) is not source.managed
            or source.managed.metadata is not source.metadata
            or self.generation(source.name) != source.generation
        ):
            raise MCPAdmissionError()

    def _capture_source(self, name: str, managed: ManagedMCPSession) -> _MCPSessionSource:
        source = _MCPSessionSource(
            self, name, managed, managed.metadata, managed.metadata.cache_key, self.generation(name)
        )
        self.check_source(source)
        return source

    async def _discard(self, name: str) -> None:
        client = self._clients.pop(name, None)
        if client is not None:
            client.valid = False
        connection = self._connections.get(name)
        if connection is not None:
            try:
                await connection.close()
            except BaseException:
                # Caller cancellation AFTER a known successful exact SDK owner
                # exit remains cancellation, not invented cleanup failure.
                if (
                    connection.task.done()
                    and not connection.task.cancelled()
                    and connection.task.exception() is None
                    and connection.lifetime.known_closed
                ):
                    if self._connections.get(name) is connection:
                        self._connections.pop(name)
                    raise
                self._mark_cleanup_failed()
                # Retain SAME original connection/task; never reconnect/release
                # on opaque failed exit or an observation deadline.
                raise MCPCleanupError() from None
            else:
                if self._connections.get(name) is connection:
                    self._connections.pop(name)

    async def invalidate(self, name: str, *, expected: ManagedMCPSession | None = None) -> None:
        self._check_reentrant(name)
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            # An old failed call must never invalidate a replacement admitted
            # while that caller was awaiting its original cleanup boundary.
            if expected is not None and self._clients.get(name) is not expected:
                return
            await self._discard(name)

    @asynccontextmanager
    async def session(self, name: str) -> AsyncIterator[ManagedMCPSession]:
        managed = await self.get(name)
        connection = self._connections.get(name)
        async with managed.semaphore:
            self._check_admission()
            # Pending semaphore waiters borrowed nothing yet. Reject stale
            # sessions, sealed/global close and replaced connection identities
            # BEFORE original SDK invocation, without invalidating a new owner.
            if (
                self._closed
                or connection is None
                or connection.release.is_set()
                or not managed.valid
                or self._clients.get(name) is not managed
                or self._connections.get(name) is not connection
            ):
                raise MCPAdmissionError()
            task = connection.begin_operation()  # No await between check/lease.
            try:
                yield managed
                self._check_admission()
            finally:
                # Synchronous release: caller's body/finally has returned, not
                # hidden SDK/background/native/cancelled effect closure proof.
                connection.finish_operation(task)

    async def call(
        self,
        name: str,
        operation: Callable[[Any], Any],
        *,
        reconnect: bool = True,
        with_source: bool = False,
        expected_source: _MCPSessionSource | None = None,
    ) -> Any:
        if expected_source is not None:
            if expected_source.name != name:
                raise MCPAdmissionError()
            self.check_source(expected_source)  # BEFORE any get/transport allocation.
        attempts = 2 if reconnect else 1
        for attempt in range(attempts):
            managed: ManagedMCPSession | None = None
            try:
                if expected_source is not None:
                    self.check_source(expected_source)  # Every attempt; never allocate a newer bound source.
                async with self.session(name) as managed:
                    if expected_source is not None:
                        self.check_source(expected_source)  # AFTER semaphore, BEFORE actual invoke.
                    source = self._capture_source(name, managed) if with_source else None
                    result = await operation(managed.session)
                    # Do not abort/reinvoke entered work. After its original
                    # return, a shared cleanup fault forbids late success.
                    self._check_admission()
                    if source is not None:
                        return _MCPCallObservation(result, source)
                    return result
            except asyncio.CancelledError:
                raise
            except MCPCleanupError:
                raise  # No generic invalidation/reconnect of failed SDK exit.
            except MCPAdmissionError:
                raise  # Stale/closing entry is NOT a transport retry request.
            except MCPPublicationError:
                raise  # Local receipt uncertainty is not a read reconnect request.
            except MCPInvocationError:
                # Original lease has exited. Preserve the original same-session
                # cleanup boundary, but NEVER retry the unknown tool outcome.
                # Actual failed SDK exit remains cleanup uncertainty and dominates;
                # known successful exit does not reset the execution latch.
                if managed is not None:
                    await self.invalidate(name, expected=managed)
                raise
            except Exception:
                self._check_admission()
                if self._closed:
                    raise  # Preserve entered failure, no new cleanup/retry path.
                if managed is not None:
                    await self.invalidate(name, expected=managed)
                if attempt + 1 >= attempts:
                    raise
        raise RuntimeError("unreachable MCP retry state")

    async def health(self, name: str) -> MCPServerMetadata:
        async def probe(session: Any) -> Any:
            await session.list_tools()
            return None

        observed = await self.call(name, probe, reconnect=True, with_source=True)
        self.check_source(observed.source)
        return observed.source.metadata

    async def close(self) -> None:
        self._check_reentrant()
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_all())
        try:
            await _drain_owner(self._close_task)
        except asyncio.CancelledError:
            if (
                self._close_task.done()
                and not self._close_task.cancelled()
                and self._close_task.exception() is None
                and not self.cleanup_failed
            ):
                raise
            self._mark_cleanup_failed()
            raise MCPCleanupError() from None
        except BaseException:
            self._mark_cleanup_failed()
            raise MCPCleanupError() from None
        self._check_cleanup()  # Empty/healthy later close cannot reset prior unknown.

    async def _close_all(self) -> None:
        errors: list[BaseException] = []
        for name in self.config.servers:
            lock = self._locks.setdefault(name, asyncio.Lock())
            async with lock:
                try:
                    await self._discard(name)
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise BaseExceptionGroup("MCP connector shutdown failed", errors)
