"""C2c2b FIRST constructor cleanup definitions, ALL UNRUN.

Synthetic initialization failure and future disposable localhost socket only;
not native/SQLite close-failure/physical process or owner-release proof.
"""

from http.server import ThreadingHTTPServer

import pytest

from doppel_agent.conversations import ConversationPersistenceError, ConversationStore
from doppel_agent.web.server import ConsoleServer


def test_original_legacy_connection_initialization_failure_closes_exact_connection(tmp_path, monkeypatch):
    import doppel_agent.conversations as conversations_module

    calls = []

    class Connection:
        def execute(self, sql):
            calls.append(sql)
            raise RuntimeError("offline initialization failure")

        def close(self):
            calls.append("close exact connection")

    connection = Connection()
    monkeypatch.setattr(conversations_module.sqlite3, "connect", lambda *args, **kwargs: connection)
    # Same original initialization failure input, now actual constructor state;
    # no uninitialized __new__ bypass of original failure/source retention hooks.
    with pytest.raises(
        ConversationPersistenceError, match="^legacy_metadata_persistence_unavailable$"
    ) as error:
        ConversationStore(tmp_path / "fixture.sqlite3")
    assert calls == ["PRAGMA foreign_keys = ON", "close exact connection"]
    assert error.value.source.failed and not error.value.source.resource_cleanup_uncertain


def test_original_legacy_manager_constructor_failure_closes_bound_socket_once(tmp_path, monkeypatch):
    import doppel_agent.web.server as server_module

    closed = []
    original_close = ThreadingHTTPServer.server_close

    def inspect_close(server):
        sock = server.socket
        assert sock.fileno() != -1
        original_close(server)
        closed.append(sock)

    def unavailable_manager(*args, **kwargs):
        raise RuntimeError("offline constructor failure")

    monkeypatch.setattr(ThreadingHTTPServer, "server_close", inspect_close)
    monkeypatch.setattr(server_module, "JobManager", unavailable_manager)
    with pytest.raises(RuntimeError, match="offline constructor failure"):
        ConsoleServer(("127.0.0.1", 0), tmp_path)
    assert len(closed) == 1 and closed[0].fileno() == -1
