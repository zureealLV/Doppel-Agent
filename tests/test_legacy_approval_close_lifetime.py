"""C FIRST original approval waiter/manager close definitions, ALL UNRUN.

Original local broker/pool and disposable stores only; not provider/native proof.
"""

import threading

import pytest

from doppel_agent.web.approvals import ApprovalBroker
from doppel_agent.web.server import JobManager, LegacyOperationEvidenceError


def test_original_broker_close_wakes_pending_waiter_denies_new_and_late_allow():
    broker, returned = ApprovalBroker(timeout_seconds=60), []
    waiter = threading.Thread(
        target=lambda: returned.append(broker.request("command_execute", "fixture", {}))
    )
    waiter.start()
    try:
        with broker.condition:
            assert broker.condition.wait_for(lambda: bool(broker.pending), timeout=2)
            original = next(iter(broker.pending))
        broker.close()
        waiter.join(2)
        assert not waiter.is_alive() and returned == [False] and not broker.pending
        assert not broker.decide(original, True) and not broker.request("command_execute", "fixture", {})
        assert not broker.pending
        broker.close()
    finally:
        broker.close()
        waiter.join(2)


def test_original_close_before_decision_consumed_never_grants_late_effect():
    broker, returned = ApprovalBroker(timeout_seconds=60), []
    waiter = threading.Thread(
        target=lambda: returned.append(broker.request("command_execute", "fixture", {}))
    )
    waiter.start()
    try:
        with broker.condition:
            assert broker.condition.wait_for(lambda: bool(broker.pending), timeout=2)
            original = next(iter(broker.pending))
            assert broker.decide(original, True)
            broker.close()  # SAME condition prevents waiter from consuming True before close.
        waiter.join(2)
        assert returned == [False] and not broker.decisions and not broker.pending
    finally:
        broker.close()
        waiter.join(2)


def test_actual_original_manager_closes_broker_before_joining_accepted_pool_work(tmp_path):
    manager, broker = JobManager(tmp_path), ApprovalBroker(timeout_seconds=60)
    results, failures = [], []
    manager.brokers["original"] = broker
    manager._pending_submissions = 1  # Explicit original acceptance fixture, no full runtime claim.

    def accepted():
        try:
            results.append(broker.request("command_execute", "fixture", {}))
            with pytest.raises(LegacyOperationEvidenceError):
                manager._assert_effect_admission()  # Before another provider/effect after wake.
        except BaseException as error:
            failures.append(error)
        finally:
            with manager._submissions:
                manager._pending_submissions -= 1
                manager._submissions.notify_all()

    future = manager.pool.submit(accepted)  # SAME original pool, no production executor addition.
    closer = threading.Thread(target=manager.close_owned)
    try:
        with broker.condition:
            assert broker.condition.wait_for(lambda: bool(broker.pending), timeout=2)
        closer.start()
        closer.join(2)
        assert not closer.is_alive() and future.done() and results == [False] and not failures
        assert manager._closing and manager._pending_submissions == 0
    finally:
        broker.close()
        future.result(timeout=2)
        if closer.ident is not None:
            closer.join(2)
        manager.pool.shutdown(wait=True, cancel_futures=False)
