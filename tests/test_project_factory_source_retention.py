"""C FIRST original project factory unknown-source definitions, ALL UNRUN.

Local candidate/catalog fixtures, not actual native ownership or physical cleanup.
"""

import pytest

from doppel_agent.projects.catalog import ProjectCatalog
from doppel_agent.projects.switching import ProjectSwitcher


class Kernel:
    def __init__(self, workspace, events, *, fail_start=False, fail_close=False):
        self.workspace, self.events = workspace, events
        self.fail_start, self.fail_close = fail_start, fail_close
        self.ready, self.url = True, "http://127.0.0.1:12345/"

    def start(self):
        self.events.append(("start", self.workspace.name))
        if self.fail_start:
            raise RuntimeError("PRIVATE_START")

    def close(self):
        self.events.append(("close", self.workspace.name))
        if self.fail_close:
            raise RuntimeError("PRIVATE_CLOSE")
        self.ready = False


def setup_switcher(tmp_path):
    old, target = tmp_path / "old", tmp_path / "target"
    old.mkdir()
    target.mkdir()
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    key, events = catalog.register(target)["project_id"], []
    return ProjectSwitcher(catalog, Kernel(old, events), lambda path: Kernel(path, events)), key, events


@pytest.mark.parametrize("phase", ["opaque", "none", "wrong_workspace"])
def test_original_project_factory_unknown_never_restores_or_constructs_another_kernel(tmp_path, phase):
    switcher, key, events = setup_switcher(tmp_path)
    allocated, calls = [], []

    def factory(path):
        calls.append(path)
        candidate = Kernel(path if phase != "wrong_workspace" else tmp_path, events)
        allocated.append(candidate)  # Fixture observes lost return; not production identity authority.
        if phase == "opaque":
            raise OSError("PRIVATE_FACTORY_LOST_RETURN")
        return None if phase == "none" else candidate

    switcher.factory = factory
    result = switcher.switch(key)
    source = switcher._construction_source
    assert not result["ok"] and "PRIVATE_" not in str(result)
    assert source.factory_attempted and source.factory_returned is (phase != "opaque")
    assert source.cleanup_uncertain and switcher._construction_uncertain.is_set()
    assert switcher._unresolved_kernel_sources[id(source)] is source
    assert len(calls) == 1 and events == [("close", "old")]
    assert source.candidate is (allocated[0] if phase == "wrong_workspace" else None)
    assert switcher.catalog.get(key)["opened_at_ns"] == 0
    assert not switcher.switch(key)["ok"]
    with pytest.raises(RuntimeError, match="project construction cleanup is unresolved"):
        switcher.close()
    assert len(calls) == 1 and events == [("close", "old")]


def test_original_candidate_start_known_failure_and_failed_close_keeps_candidate_without_rollback(tmp_path):
    switcher, key, events = setup_switcher(tmp_path)
    candidates = []

    def factory(path):
        candidate = Kernel(path, events, fail_start=True, fail_close=True)
        candidates.append(candidate)
        return candidate

    switcher.factory = factory
    result = switcher.switch(key)
    assert not result["ok"] and len(candidates) == 1
    source = switcher._construction_source
    assert source.factory_returned and not source.cleanup_uncertain and switcher.kernel is candidates[0]
    assert events == [("close", "old"), ("start", "target"), ("close", "target")]
    # Explicit later close joins SAME candidate's own cleanup contract; not a new factory.
    candidates[0].fail_close = False
    switcher.close()
    assert switcher.kernel is None and len(candidates) == 1
