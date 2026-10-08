import threading

from doppel_agent.projects.catalog import ProjectCatalog
from doppel_agent.projects.switching import ProjectSwitcher


class Kernel:
    def __init__(self, workspace, events, *, fail_start=False, fail_close=False):
        self.workspace = workspace
        self.events = events
        self.ready = True
        self.url = "http://127.0.0.1:12345/"
        self.fail_start = fail_start
        self.fail_close = fail_close

    def start(self):
        self.events.append(("start", self.workspace.name))
        if self.fail_start:
            raise RuntimeError("PRIVATE_START_DETAIL")

    def close(self):
        self.events.append(("close", self.workspace.name))
        if self.fail_close:
            raise RuntimeError("PRIVATE_DRAIN_DETAIL")
        self.ready = False


def setup_switcher(tmp_path, **old_options):
    old, target = tmp_path / "old", tmp_path / "target"
    old.mkdir()
    target.mkdir()
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    key = catalog.register(target)["project_id"]
    events = []
    switcher = ProjectSwitcher(catalog, Kernel(old, events, **old_options), lambda path: Kernel(path, events))
    return switcher, key, events


def test_switch_starts_target_only_after_old_kernel_closes(tmp_path):
    switcher, key, events = setup_switcher(tmp_path)
    result = switcher.switch(key)
    assert result == {"ok": True, "url": "http://127.0.0.1:12345/", "changed": True}
    assert events == [("close", "old"), ("start", "target")]
    assert switcher.catalog.get(key)["opened_at_ns"] > 0


def test_pending_cleanup_prevents_target_start_and_is_retryable(tmp_path):
    switcher, key, events = setup_switcher(tmp_path, fail_close=True)
    old = switcher.kernel
    result = switcher.switch(key)
    assert result["ok"] is False and "PRIVATE" not in str(result)
    assert events == [("close", "old")]
    assert switcher.kernel is old
    assert switcher.catalog.get(key)["opened_at_ns"] == 0
    old.fail_close = False
    assert switcher.switch(key)["ok"]


def test_unknown_project_does_not_touch_current_owner(tmp_path):
    switcher, _key, events = setup_switcher(tmp_path)
    assert switcher.switch("a" * 32)["ok"] is False
    assert events == []


def test_failed_target_is_cleaned_before_previous_project_restarts(tmp_path):
    switcher, key, events = setup_switcher(tmp_path)
    switcher.factory = lambda path: Kernel(path, events, fail_start=path.name == "target")
    result = switcher.switch(key)
    assert not result["ok"] and result["url"] == "http://127.0.0.1:12345/"
    assert events == [("close", "old"), ("start", "target"), ("close", "target"), ("start", "old")]
    assert switcher.kernel.workspace.name == "old"


def test_failed_target_cleanup_prevents_unsafe_rollback(tmp_path):
    switcher, key, events = setup_switcher(tmp_path)
    switcher.factory = lambda path: Kernel(path, events, fail_start=True, fail_close=True)
    assert not switcher.switch(key)["ok"]
    assert events == [("close", "old"), ("start", "target"), ("close", "target")]
    assert switcher.kernel.workspace.name == "target"


def test_concurrent_switch_is_rejected_and_close_prevents_later_start(tmp_path):
    switcher, key, events = setup_switcher(tmp_path)
    switcher._lock.acquire()
    try:
        assert not switcher.switch(key)["ok"]
        assert not events
    finally:
        switcher._lock.release()
    switcher.close()
    assert not switcher.switch(key)["ok"]
    assert events == [("close", "old")]


def test_current_project_switch_does_not_restart_kernel(tmp_path):
    switcher, _key, events = setup_switcher(tmp_path)
    key = switcher.catalog.register(switcher.kernel.workspace)["project_id"]
    assert switcher.switch(key)["changed"] is False
    assert events == []


def test_close_during_drain_prevents_target_creation(tmp_path):
    switcher, key, events = setup_switcher(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    old_close = switcher.kernel.close

    def draining():
        entered.set()
        assert finish.wait(5)
        old_close()

    switcher.kernel.close = draining
    results = []
    worker = threading.Thread(target=lambda: results.append(switcher.switch(key)))
    worker.start()
    assert entered.wait(5)
    closer = threading.Thread(target=switcher.close)
    closer.start()
    assert switcher._closing.wait(5)
    finish.set()
    worker.join(5)
    closer.join(5)
    assert not worker.is_alive() and not closer.is_alive()
    assert not results[0]["ok"]
    assert events == [("close", "old")]
