"""C2c2c FIRST definitions, ALL UNRUN: original disposable Legacy journal.

These operation pairs are not runtime SQL/provider/transport/billing receipts.
"""

import json

import pytest

from doppel_agent.persistence.legacy_recovery import LegacyOperationRecoveryQueries
from doppel_agent.storage import LegacyStorageReadError, RunStore


ID = "a" * 32


def test_missing_runs_directory_is_not_created_or_claimed_full_coverage(tmp_path):
    result = LegacyOperationRecoveryQueries(tmp_path).read()
    assert not result.quarantined
    assert result.reason == "no_unresolved_recorded_operations_found"
    assert result.operation_rows == 0 and not (tmp_path / "runs").exists()


@pytest.mark.parametrize("operation", ["run", "probe"])
def test_exact_original_operation_pair_and_recreated_reader(tmp_path, operation):
    store = RunStore(tmp_path)
    store.append_operation(ID, operation)
    unknown = LegacyOperationRecoveryQueries(tmp_path).read()
    assert unknown.quarantined and unknown.reason == "unresolved_operations"
    store.append_operation(ID, operation, outcome="returned")
    for _ in range(2):
        result = LegacyOperationRecoveryQueries(tmp_path).read()
        assert not result.quarantined and result.operation_rows == 2
    assert not (tmp_path / "runs" / ID / "session.json").exists()  # Probe is NOT an invented Core session.


@pytest.mark.parametrize("case", ["failed", "orphan", "duplicate", "cross_operation", "private_payload"])
def test_unknown_or_ambiguous_operation_cannot_be_cleared_by_terminal_session(tmp_path, case):
    store = RunStore(tmp_path)
    if case != "orphan":
        store.append_operation(ID, "run")
    if case == "duplicate":
        store.append_operation(ID, "run")
    elif case == "private_payload":
        store.append_event(
            ID,
            {
                "run_id": ID,
                "kind": "legacy.operation_finished",
                "payload": {
                    "version": 1,
                    "operation_id": ID,
                    "operation": "run",
                    "outcome": "returned",
                    "api_key": "PRIVATE",
                },
            },
        )
    else:
        store.append_operation(
            ID,
            "probe" if case == "cross_operation" else "run",
            outcome="failed" if case == "failed" else "returned",
        )
    store.write_session(ID, {"status": "completed", "answer": "NOT_CLOSURE_AUTHORITY"})
    assert LegacyOperationRecoveryQueries(tmp_path).read().quarantined


def test_historical_started_core_needs_original_terminal_not_session(tmp_path):
    store = RunStore(tmp_path)
    store.append_event(ID, {"run_id": ID, "kind": "run_started", "payload": {"prompt": "PRIVATE"}})
    store.write_session(ID, {"status": "completed"})
    assert LegacyOperationRecoveryQueries(tmp_path).read().quarantined
    store.append_event(ID, {"run_id": ID, "kind": "run_completed", "payload": {"answer": "PRIVATE"}})
    result = LegacyOperationRecoveryQueries(tmp_path).read()
    assert not result.quarantined and result.operation_rows == 0
    assert "PRIVATE" not in repr(result)  # Not a claim of historical provider coverage.


@pytest.mark.parametrize(
    "line",
    [
        b'{"kind": "legacy.operation_started"}',
        b'{"kind":"x","kind":"y"}\n',
        b'{"kind":"x","payload":NaN}\n',
        b"broken\n",
    ],
)
def test_partial_duplicate_nonfinite_and_corrupt_original_lines_fail_closed(tmp_path, line):
    directory = tmp_path / "runs" / ID
    directory.mkdir(parents=True)
    (directory / "events.jsonl").write_bytes(line)
    result = LegacyOperationRecoveryQueries(tmp_path).read()
    assert result.quarantined and result.reason == "evidence_unavailable"


@pytest.mark.parametrize("case", ["empty", "missing", "terminal_then_start"])
def test_allocated_but_empty_source_or_reversed_core_terminal_is_unknown(tmp_path, case):
    directory = tmp_path / "runs" / ID
    directory.mkdir(parents=True)
    if case == "empty":
        (directory / "events.jsonl").write_bytes(b"")
    elif case == "terminal_then_start":
        store = RunStore(tmp_path)
        store.append_event(ID, {"run_id": ID, "kind": "run_completed", "payload": {}})
        store.append_event(ID, {"run_id": ID, "kind": "run_started", "payload": {}})
    assert LegacyOperationRecoveryQueries(tmp_path).read().quarantined


def test_all_directories_and_full_bounded_stream_not_recent_session_sample(tmp_path, monkeypatch):
    import doppel_agent.persistence.legacy_recovery as module

    store = RunStore(tmp_path)
    for index in range(20):
        identity = f"{index:032x}"
        store.append_operation(identity, "probe")
        store.append_operation(identity, "probe", outcome="returned")
    store.append_operation(ID, "run")
    assert LegacyOperationRecoveryQueries(tmp_path).read().quarantined
    monkeypatch.setattr(module, "MAX_DIRECTORIES", 16)
    assert LegacyOperationRecoveryQueries(tmp_path).read().reason == "evidence_limit"


def test_line_and_total_decode_limits_are_unknown_not_prefix_success(tmp_path, monkeypatch):
    import doppel_agent.persistence.legacy_recovery as module

    store = RunStore(tmp_path)
    store.append_operation(ID, "run")
    store.append_operation(ID, "run", outcome="returned")
    monkeypatch.setattr(module, "MAX_LINE", 10)
    assert LegacyOperationRecoveryQueries(tmp_path).read().reason == "evidence_limit"
    monkeypatch.setattr(module, "MAX_LINE", 256 * 1024)
    monkeypatch.setattr(module, "MAX_DECODE", 10)
    assert LegacyOperationRecoveryQueries(tmp_path).read().reason == "evidence_limit"


def test_original_operation_fsync_precedes_append_return_and_failure_never_becomes_success(
    tmp_path, monkeypatch
):
    import doppel_agent.storage as module

    store = RunStore(tmp_path)
    calls = []

    def unavailable(fd):
        calls.append(fd)
        raise OSError("PRIVATE_FSYNC_FAILURE")

    monkeypatch.setattr(module.os, "fsync", unavailable)
    with pytest.raises(LegacyStorageReadError) as failure:
        store.append_operation(ID, "probe")
    assert failure.value.source is store and store.failed
    assert len(calls) == 1
    # Even a complete line written before failed fsync is NOT a closure pair.
    assert LegacyOperationRecoveryQueries(tmp_path).read().quarantined


def test_original_operation_metadata_has_closed_frame_and_no_private_payload(tmp_path):
    store = RunStore(tmp_path)
    store.append_operation(ID, "probe")
    frame = json.loads((tmp_path / "runs" / ID / "events.jsonl").read_text(encoding="utf-8"))
    assert set(frame) == {"run_id", "sequence", "timestamp", "kind", "payload"}
    assert frame["payload"] == {"version": 1, "operation_id": ID, "operation": "probe"}
    assert frame["kind"] == "legacy.operation_started" and frame["sequence"] == 0


@pytest.mark.parametrize("recorded", ["closed", "open", "none"])
def test_original_reviewed_legacy_core_pause_uses_existing_registered_sql_receipt_source(tmp_path, recorded):
    from doppel_agent.persistence.events import EventStore
    from doppel_agent.persistence.runs import RuntimeRunStore

    database = tmp_path / "runtime.sqlite3"
    thread = "d" * 32
    runs = RuntimeRunStore(database)
    runs.create(ID, thread, "legacy", {"mode": "legacy", "prompt": "offline"}, None)
    events = EventStore(database)
    start = dict(
        version=1,
        call_id="e" * 32,
        engine="legacy",
        actor="runtime_model",
        path="sync_worker",
        boundary="provider_method_not_transport_attempt",
    )
    if recorded != "none":
        events.append(ID, thread, "provider.call_started", start)
    if recorded == "closed":
        events.append(
            ID,
            thread,
            "provider.call_finished",
            dict(
                start,
                outcome="returned",
                method_entered=True,
                usage=None,
                usage_state="unknown",
                failure=None,
                billing_complete=False,
                account_cap_guaranteed=False,
                network_attempts=None,
                price_receipt=None,
            ),
        )
    store = RunStore(tmp_path)
    store.append_event(ID, {"run_id": ID, "kind": "run_started", "payload": {}})
    result = LegacyOperationRecoveryQueries(tmp_path, runtime_database=database).read()
    assert result.quarantined == (recorded != "closed")
    # A separate native-console operation marker cannot borrow that SQL closure.
    store.append_operation(ID, "run")
    assert LegacyOperationRecoveryQueries(tmp_path, runtime_database=database).read().quarantined
