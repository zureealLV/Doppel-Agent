import threading
from types import SimpleNamespace

import pytest

from doppel_agent.web.server import JobManager


def test_legacy_close_waits_for_accepted_submission_and_does_not_cancel_its_future(tmp_path):
    manager = JobManager(tmp_path)
    manager.pool.shutdown(wait=True)
    shutdowns = []
    manager.pool = SimpleNamespace(shutdown=lambda **kwargs: shutdowns.append(kwargs))
    manager._pending_submissions = 1
    worker = threading.Thread(target=manager.close_owned)
    worker.start()
    with manager._submissions:
        closing = manager._submissions.wait_for(lambda: manager._closing, timeout=5)
        assert not shutdowns
        manager._pending_submissions = 0
        manager._submissions.notify_all()
    worker.join(5)
    assert closing
    assert not worker.is_alive()
    assert shutdowns == [{"wait": True, "cancel_futures": False}]


def test_legacy_closing_rejects_new_admission_without_recording_a_turn(tmp_path):
    manager = JobManager(tmp_path)
    manager.close_owned()
    with pytest.raises(RuntimeError, match="closing"):
        manager.submit({"prompt": "read README.md", "config": {"provider": "mock"}})
    assert not manager.jobs and manager._pending_submissions == 0
