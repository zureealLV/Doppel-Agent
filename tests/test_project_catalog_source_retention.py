"""C FIRST original catalog SQL/host/kernel source definitions, ALL UNRUN.

Disposable actual SQLite + original API proxies only; no user catalog/native proof.
"""

import sqlite3
import sys
from types import SimpleNamespace

import pytest

import doppel_agent.projects.catalog as module
from doppel_agent.desktop import WindowApi
from doppel_agent.projects.desktop_kernel import DesktopKernel


def dispose(connections, cursors):
    # Original fixture observations only; no source recovery or production reset.
    for cursor in cursors:
        try:
            cursor.raw.close()
        except sqlite3.Error:
            pass  # Connection may already have returned its original close.
    for connection in connections:
        connection.raw.close()


def observe(monkeypatch, database, phase, events):
    original, connections, cursors = sqlite3.connect, [], []

    class Cursor:
        def __init__(self, raw):
            self.raw, self.closes = raw, 0
            cursors.append(self)

        def fetchall(self):
            if phase == "fetch":
                raise sqlite3.DatabaseError("PRIVATE_FETCH")
            return self.raw.fetchall()

        def fetchone(self):
            return self.raw.fetchone()

        def close(self):
            self.closes += 1
            events.append("cursor close")
            if phase == "cursor_close":
                raise OSError("PRIVATE_CURSOR_CLOSE")
            result = self.raw.close()
            if phase == "cursor_closed_then_throw":
                raise OSError("PRIVATE_CURSOR_ALREADY_CLOSED")
            return result

    class Connection:
        def __init__(self, raw):
            self.raw, self.closes = raw, 0

        @property
        def row_factory(self):
            return self.raw.row_factory

        @row_factory.setter
        def row_factory(self, value):
            if phase in {"setup", "setup_close"}:
                raise OSError("PRIVATE_ROW_FACTORY")
            self.raw.row_factory = value

        def __enter__(self):
            if phase == "enter":
                raise sqlite3.DatabaseError("PRIVATE_ENTER")
            self.raw.__enter__()
            return self

        def __exit__(self, *details):
            events.append("original transaction exit")
            if phase == "exit":
                raise sqlite3.DatabaseError("PRIVATE_EXIT")
            return self.raw.__exit__(*details)

        def execute(self, *args):
            cursor = Cursor(self.raw.execute(*args))
            if phase == "cursor_opaque":
                raise OSError("PRIVATE_CURSOR_LOST_RETURN")
            return None if phase == "cursor_none" else cursor

        def close(self):
            self.closes += 1
            events.append("connection close")
            if phase in {"connection_close", "setup_close"}:
                raise OSError("PRIVATE_CONNECTION_CLOSE")
            result = self.raw.close()
            if phase == "connection_closed_then_throw":
                raise OSError("PRIVATE_CONNECTION_ALREADY_CLOSED")
            return result

    def connect(path, *args, **kwargs):
        if str(path) != str(database):
            return original(path, *args, **kwargs)
        connection = Connection(original(path, *args, **kwargs))
        connections.append(connection)
        if phase == "connect_opaque":
            raise OSError("PRIVATE_CONNECT_LOST_RETURN")
        return None if phase == "connect_none" else connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    return connections, cursors


@pytest.mark.parametrize("phase", ["setup", "enter", "exit", "fetch"])
def test_original_catalog_known_closes_fence_metadata_without_inventing_resource_failure(
    tmp_path, monkeypatch, phase
):
    events, retained = [], []
    path = tmp_path / "catalog.sqlite3"
    catalog = module.ProjectCatalog(
        path, failure=lambda: events.append("fault"), cleanup_failure=retained.append
    )
    connections, cursors = observe(monkeypatch, path, phase, events)
    try:
        with pytest.raises(module.ProjectCatalogPersistenceError) as error:
            catalog.list()
        assert error.value.source is catalog and "PRIVATE_" not in str(error.value)
        assert catalog.failed and not catalog.resource_cleanup_uncertain and retained == []
        assert events.index("fault") < events.index("connection close")
        assert len(connections) == 1 and connections[0].closes == 1
        assert all(cursor.closes == 1 for cursor in cursors)
        with pytest.raises(module.ProjectCatalogPersistenceError):
            catalog.list()
        assert len(connections) == 1 and events.count("fault") == 1
        catalog.check_resource_cleanup()
    finally:
        dispose(connections, cursors)


@pytest.mark.parametrize(
    "phase",
    [
        "connect_opaque",
        "connect_none",
        "cursor_opaque",
        "cursor_none",
        "cursor_close",
        "cursor_closed_then_throw",
        "connection_close",
        "connection_closed_then_throw",
        "setup_close",
    ],
)
def test_original_catalog_unknown_retains_all_exact_sources_and_never_reopens_or_recloses(
    tmp_path, monkeypatch, phase
):
    path, events, retained = tmp_path / "catalog.sqlite3", [], []
    catalog = module.ProjectCatalog(
        path, failure=lambda: events.append("fault"), cleanup_failure=retained.append
    )
    connections, cursors = observe(monkeypatch, path, phase, events)
    try:
        with pytest.raises(module.ProjectCatalogPersistenceError) as error:
            catalog.list()
        frame = next(iter(catalog._unresolved_connections.values()))
        assert error.value.source is catalog and catalog.failed and catalog.resource_cleanup_uncertain
        assert retained and all(source is catalog for source in retained)
        assert frame.connect_attempted and frame.connect_returned is (phase != "connect_opaque")
        if phase in {"connect_opaque", "connect_none"}:
            assert frame.connection is None and not frame.close_attempted and not cursors
        else:
            assert frame.connection is connections[0] and frame.close_attempted
            if cursors:
                original = frame.cursors[0]
                assert original.execute_attempted and original.execute_returned is (phase != "cursor_opaque")
                if phase in {"cursor_opaque", "cursor_none"}:
                    assert original.cursor is None and not original.close_attempted
                else:
                    assert original.cursor is cursors[0] and original.close_attempted
        before = list(events)
        for action in (catalog.list, catalog.check_resource_cleanup):
            with pytest.raises(module.ProjectCatalogPersistenceError):
                action()
        assert events == before and len(connections) == 1
    finally:
        dispose(connections, cursors)


def test_original_catalog_failed_constructor_keeps_same_source_before_host_assignment(tmp_path, monkeypatch):
    events, calls = [], []
    database = tmp_path / "new-catalog.sqlite3"
    connections, cursors = observe(monkeypatch, database, "connection_close", events)
    kernel = SimpleNamespace(
        workspace=tmp_path,
        url="http://127.0.0.1:12345/",
        _mark_legacy_uncertain=lambda: calls.append("fault"),
        _retain_project_catalog_cleanup=lambda source: calls.append(source),
        close=lambda: calls.append("close"),
    )
    host = WindowApi(kernel, catalog_path=database)
    view = SimpleNamespace(windows=[SimpleNamespace(get_current_url=lambda: kernel.url)])
    monkeypatch.setitem(sys.modules, "webview", view)
    try:
        assert not host.project_list()["ok"]
        source = next(iter(host._unresolved_catalog_sources.values()))
        assert source.resource_cleanup_uncertain and host._catalog is None and host._switcher is None
        assert calls == ["fault", source] and connections[0].closes == 1
        assert not host.project_list()["ok"] and len(connections) == 1
    finally:
        dispose(connections, cursors)


def test_actual_original_kernel_retains_catalog_source_and_outer_owner_before_release(tmp_path):
    kernel = DesktopKernel(tmp_path)
    kernel.owner.path.parent.mkdir()
    kernel.owner.acquire()
    source = object()
    try:
        kernel._retain_project_catalog_cleanup(source)
        assert kernel._startup_cleanup_sources[id(source)] is source and kernel._closing.is_set()
        with pytest.raises(RuntimeError, match="startup cleanup is unresolved"):
            kernel.close()
        assert kernel.owner.held and not kernel._closed
    finally:
        kernel.owner.release()


def test_original_catalog_domain_refusals_keep_healthy_sql_contract(tmp_path):
    catalog = module.ProjectCatalog(tmp_path / "catalog.sqlite3")
    with pytest.raises(ValueError):
        catalog.get("a" * 32)
    with pytest.raises(ValueError):
        catalog.register("relative")
    assert not catalog.failed and not catalog.resource_cleanup_uncertain
    assert catalog.list() == []


def test_original_project_mark_opened_sql_failure_is_not_reported_as_success_or_old_restore(
    tmp_path, monkeypatch
):
    from doppel_agent.projects.switching import ProjectSwitcher

    old, target = tmp_path / "old", tmp_path / "target"
    old.mkdir()
    target.mkdir()
    events, kernels, resources = [], [], []
    catalog = module.ProjectCatalog(tmp_path / "catalog.sqlite3")
    key = catalog.register(target)["project_id"]

    class Kernel:
        ready, url = True, "http://127.0.0.1:12345/"

        def __init__(self, path):
            self.workspace = path
            kernels.append(self)

        def close(self):
            events.append(("close", self.workspace))
            self.ready = False

        def start(self):
            events.append(("start", self.workspace))
            resources.append(observe(monkeypatch, catalog.database, "setup", []))

    switcher = ProjectSwitcher(catalog, Kernel(old), Kernel)
    try:
        result = switcher.switch(key)
        assert not result["ok"] and result["url"] == kernels[1].url and "PRIVATE_" not in str(result)
        assert switcher.kernel is kernels[1] and catalog.failed and len(kernels) == 2
        assert events == [("close", old), ("start", target)]
    finally:
        for connections, cursors in resources:
            dispose(connections, cursors)
