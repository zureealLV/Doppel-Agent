"""Sealed provenance, native bounds and human forms cannot be conflated."""

import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from bench.runtime_audit_inputs import AuditInputs, OFFLINE_BUDGET, adjudication_forms, normalize_observation, source_context
from bench.runtime_contract import NativeTaskContract
from bench.runtime_fixtures import load_task_fixture
from bench.runtime_freeze import canonical_json, thaw_json
from bench.runtime_matrix import RuntimeMatrix


ROOT = Path(__file__).resolve().parents[1]


def source_repository(root):
    (root / "src/doppel_agent").mkdir(parents=True)
    for name in ("keep.py", "removed.py"):
        (root / "src/doppel_agent" / name).write_text("original = True\n", encoding="utf-8")
    for arguments in (["init"], ["add", "src"],
                      ["-c", "user.name=Offline test", "-c", "user.email=offline@example.invalid", "commit", "-m", "seed"]):
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)


def test_source_context_seals_tracked_deletions_and_detects_restoration(tmp_path):
    source_repository(tmp_path)
    initial = source_context(tmp_path)
    target = tmp_path / "src/doppel_agent/removed.py"
    original = target.read_bytes()
    target.unlink()
    deleted = source_context(tmp_path)
    assert deleted["deleted_files"] == ["src/doppel_agent/removed.py"]
    assert "src/doppel_agent/removed.py" not in deleted["files"]
    assert deleted["evaluation_source_dirty"] is True
    assert deleted["files_sha256"] != initial["files_sha256"]
    target.write_bytes(original)
    assert source_context(tmp_path) == initial


def test_source_context_does_not_treat_a_nonregular_replacement_as_deletion(tmp_path):
    source_repository(tmp_path)
    target = tmp_path / "src/doppel_agent/removed.py"
    target.unlink()
    target.mkdir()
    with pytest.raises(ValueError, match="owned regular file"):
        source_context(tmp_path)


def test_navigation_snapshot_keeps_new_assets_and_binds_deleted_assets(tmp_path):
    from bench.runtime_readonly_harness import _whole_source_snapshot
    source_repository(tmp_path)
    (tmp_path / "src/doppel_agent/removed.py").unlink()
    (tmp_path / "src/doppel_agent/new.js").write_bytes(b"new_build = true;\n")
    context, public = _whole_source_snapshot(tmp_path)
    assert context["deleted_files"] == ["src/doppel_agent/removed.py"]
    assert dict(public) == {"src/doppel_agent/keep.py": (tmp_path / "src/doppel_agent/keep.py").read_bytes(),
                            "src/doppel_agent/new.js": b"new_build = true;\n"}


def inputs_and_row():
    fixture = load_task_fixture("patch-02")
    contract = NativeTaskContract.load()
    source = {"commit": "a" * 40, "files": {"src/example.py": "b" * 64}, "evaluation_source_dirty": True}
    inputs = AuditInputs.capture(contract, [fixture], [("run_service", "patch-02:graph:1")], source)
    row = {"case_id": fixture.case_id, "fixture_sha256": fixture.sha256, "fixture_version": fixture.version,
           "run_key": "patch-02:graph:1", "runtime": "graph", "actual_runtime": "graph", "boundary": "run_service",
           "permissions": {"workspace_write": True}, "status": "completed", "human_review": "pending", "task_quality_scored": False,
           "checks": {"synthetic_unit_test_input_only": True}, "deterministic_pass": True,
           "receipts": [{"tool": "read_file", "path": "service.py", "response_sha256": "c" * 64}],
           "approval_count": 1,
           "approval_decisions": [{"source": "service_event", "event_seq": 5, "event_sha256": "d" * 64,
                                    "interrupt_id": "actual-interrupt", "action": "approve"}]}
    return inputs, row, source


def test_fixture_oracles_and_matrix_grants_are_deeply_immutable():
    fixture = load_task_fixture("patch-03")
    with pytest.raises(TypeError):
        fixture.oracle["hidden_files"]["target_tests.py"] = "changed"
    with pytest.raises(TypeError):
        fixture.oracle["atomic_controls"][0] = "changed"
    with pytest.raises(TypeError):
        OFFLINE_BUDGET["external_model_calls_allowed"] = True
    matrix = RuntimeMatrix.load(ROOT / "bench/cases/runtime/manifest.json")
    case = next(case for case in matrix.cases if case.case_id == "patch-02")
    with pytest.raises(TypeError):
        case.permissions["command_execute"] = True
    run = next(run for run in matrix.expand() if run.case_id == "patch-02")
    with pytest.raises(TypeError):
        run.validator["value"] = "fake acceptance"


def test_constructor_inputs_do_not_remain_mutable_aliases():
    fixture = load_task_fixture("patch-03")
    mutable = thaw_json(fixture.oracle)
    rebuilt = replace(fixture, oracle=mutable)
    mutable["atomic_controls"].append("forged")
    assert rebuilt.oracle["atomic_controls"] == ("second_replace_failure", "stale_base")
    inputs, _, source = inputs_and_row()
    seal = inputs.sha256
    source["files"]["src/example.py"] = "changed"
    document = inputs.document()
    document["budget"]["max_cost_usd"] = 100
    assert inputs.sha256 == seal and inputs.document()["budget"]["max_cost_usd"] == 0
    with pytest.raises(ValueError, match="changed"):
        inputs.assert_source_unchanged(source)
    body = inputs.document()
    del body["inputs_sha256"]
    assert sha256(canonical_json(body)).hexdigest() == seal


@pytest.mark.parametrize("mutation", ["paid", "budget", "repeat", "deadline", "bool_budget"])
def test_scripted_freeze_rejects_spend_or_execution_bound_drift(mutation):
    budget = thaw_json(OFFLINE_BUDGET)
    key, value = {"paid": ("external_model_calls_allowed", True), "budget": ("max_cost_usd", 1),
                  "repeat": ("repeat_ids", [1, 2, 3]), "deadline": ("cancel_service_deadline_seconds", 30),
                  "bool_budget": ("max_cost_usd", False)}[mutation]
    budget[key] = value
    with pytest.raises(ValueError, match="budget"):
        AuditInputs.capture(NativeTaskContract.load(), [load_task_fixture("patch-02")], [("run_service", "patch-02:graph:1")], {}, budget=budget)


@pytest.mark.parametrize("selection", [[], [("run_service", "patch-02:graph:1")] * 2,
                                       [("direct_factory", "patch-02:graph:1")], [("run_service", "patch-02:graph:2")],
                                       [("run_service", "patch-01:graph:1")]])
def test_freeze_rejects_missing_duplicate_unsupported_or_unseeded_keys(selection):
    with pytest.raises(ValueError):
        AuditInputs.capture(NativeTaskContract.load(), [load_task_fixture("patch-02")], selection, {})


@pytest.mark.parametrize("mutation", ["fixture", "version", "permission", "native", "fallback", "flag", "boolean", "read_hash", "quality", "human", "key"])
def test_normalization_rejects_identity_or_self_score_mutants(mutation):
    inputs, row, _ = inputs_and_row()
    if mutation == "fixture":
        row["fixture_sha256"] = "wrong"
    elif mutation == "version":
        row["fixture_version"] = "1.2"
    elif mutation == "permission":
        row["permissions"]["command_execute"] = True
    elif mutation == "native":
        row["actual_runtime"] = "legacy"
    elif mutation == "fallback":
        row["fallback_runtime"] = "legacy"
    elif mutation == "flag":
        row["deterministic_pass"] = False
    elif mutation == "boolean":
        row["checks"]["synthetic_unit_test_input_only"] = 1
    elif mutation == "read_hash":
        row["receipts"][0]["response_sha256"] = "fake"
    elif mutation == "quality":
        row["task_quality_scored"] = True
    elif mutation == "human":
        row["human_review"] = "passed"
    else:
        row["run_key"] = "patch-02:graph:2"
    with pytest.raises(ValueError):
        normalize_observation(inputs, row)


def test_adjudication_keeps_all_denominators_and_does_not_auto_fill_human_answers():
    inputs, row, _ = inputs_and_row()
    normalized = normalize_observation(inputs, row)
    queue = adjudication_forms(inputs, [normalized])
    assert queue["task_quality_scored"] is False
    direct, service = queue["boundaries"]["direct_factory"], queue["boundaries"]["run_service"]
    assert direct["original_run_count"] == service["original_run_count"] == 180
    assert direct["supported_run_count"] == 81 and service["supported_run_count"] == 126
    assert direct["recorded_control_count"] == 0 and service["recorded_control_count"] == 1
    observed = next(item for item in service["items"] if item["run_key"] == "patch-02:graph:1")
    assert observed["assessment_state"] == "pending_human_verdict"
    assert observed["human_verdict"] == "pending" and observed["seeded_finding_match"] is None and observed["false_positives"] is None
    assert observed["evidence_sha256"] == sha256(canonical_json(row)).hexdigest()
    assert len(service["items"]) == len(direct["items"]) == 180
    assert all(item["task_quality_score"] is None for form in queue["boundaries"].values() for item in form["items"])
    assert any(item["assessment_state"] == "unsupported" and item["unsupported_reasons"] for item in service["items"])
    assert any(item["assessment_state"] == "not_recorded" for item in service["items"])


def test_infrastructure_failure_is_not_a_model_failure_or_missing_human_verdict():
    inputs, row, _ = inputs_and_row()
    row.update(status="infrastructure_failed", actual_runtime=None, infrastructure_failure=True,
               failure_class="runtime_timeout", checks={"native_probe_returned_evidence": False}, deterministic_pass=False, receipts=[])
    queue = adjudication_forms(inputs, [normalize_observation(inputs, row)])
    observed = next(item for item in queue["boundaries"]["run_service"]["items"] if item["run_key"] == row["run_key"])
    assert observed["assessment_state"] == "infrastructure_failure" and observed["human_verdict"] == "pending"


@pytest.mark.parametrize("mutation", ["duplicate", "seal", "outside_selection"])
def test_adjudication_rejects_cross_context_or_duplicate_evidence(mutation):
    inputs, row, _ = inputs_and_row()
    normalized = normalize_observation(inputs, row)
    rows = [normalized]
    if mutation == "duplicate":
        rows.append(copy.deepcopy(normalized))
    elif mutation == "seal":
        normalized["inputs_sha256"] = "changed"
    else:
        normalized["run_key"] = "patch-02:graph:2"
    with pytest.raises(ValueError):
        adjudication_forms(inputs, rows)


def test_result_store_binds_configuration_without_mutable_alias_and_detects_disk_drift(tmp_path):
    from bench.live_runtime_matrix import ResultStore
    configuration = {"protocol": {"hash": "original"}, "model": "fixture"}
    store = ResultStore(tmp_path, configuration)
    configuration["protocol"]["hash"] = "caller-mutation"
    store.save("nav-01:graph:1", {"run_key": "nav-01:graph:1", "status": "failed"})
    assert store.load("nav-01:graph:1")["status"] == "failed"
    (tmp_path / "context.json").write_text(json.dumps(configuration))
    with pytest.raises(ValueError, match="configuration"):
        store.load("nav-01:graph:1")


def test_competing_result_store_writers_cannot_overwrite_a_saved_attempt(tmp_path):
    from bench.live_runtime_matrix import ResultStore
    first, second = ResultStore(tmp_path, {"model": "fixture"}), ResultStore(tmp_path, {"model": "fixture"})
    first.save("nav-01:graph:1", {"run_key": "nav-01:graph:1", "status": "failed"})
    with pytest.raises(ValueError, match="exists"):
        second.save("nav-01:graph:1", {"run_key": "nav-01:graph:1", "status": "completed"})
    assert first.load("nav-01:graph:1")["status"] == "failed"
    assert not list(tmp_path.rglob("*.tmp-*"))


def test_paid_execution_rejects_price_or_budget_drift_before_runner_calls(tmp_path):
    import asyncio
    from bench.live_runtime_matrix import ResultStore, execute_runs, select_runs
    calls = []

    async def runner(run):
        calls.append(run.run_key)
        raise AssertionError("drifted paid execution must not call a provider")

    store = ResultStore(tmp_path, {"input_price_per_million": 1, "output_price_per_million": 2, "max_cost_usd": 3})
    runs = select_runs(NativeTaskContract.load().matrix, "canary")
    with pytest.raises(ValueError, match="budget"):
        asyncio.run(execute_runs(runs, store, runner, input_price=1, output_price=2, max_cost_usd=10))
    assert calls == [] and list((tmp_path / "runs").iterdir()) == []


def test_simultaneous_publication_has_one_winner_without_partial_file(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from bench import live_runtime_matrix as live

    stores = [live.ResultStore(tmp_path, {"model": "fixture"}) for _ in range(2)]
    original, barrier = live._write_json_exclusive, Barrier(2)

    def publish(path, value):
        barrier.wait(timeout=10)
        return original(path, value)

    monkeypatch.setattr(live, "_write_json_exclusive", publish)

    def save(index):
        try:
            stores[index].save("nav-01:graph:1", {"run_key": "nav-01:graph:1", "writer": index})
            return index
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, (0, 1)))
    winners = [index for index in results if index is not None]
    assert len(winners) == 1
    assert stores[0].load("nav-01:graph:1")["writer"] == winners[0]
    assert not list(tmp_path.rglob("*.tmp-*"))


def test_native_decisions_are_extracted_from_durable_events_without_exporting_edit_source():
    from bench.runtime_contract import native_approval_decisions
    event = {"seq": 9, "type": "approval.decided", "run_id": "run", "payload": {
        "interrupt_id": "interrupt", "decision": {"action": "edit", "tool_calls": [{"source": "sensitive-edit"}]},
    }}
    result = native_approval_decisions([{"seq": 8, "type": "run.started"}, event])
    assert result == [{"source": "service_event", "event_seq": 9, "event_sha256": sha256(canonical_json(event)).hexdigest(),
                       "interrupt_id": "interrupt", "action": "edit"}]
    assert "sensitive-edit" not in str(result)


@pytest.mark.parametrize("mutation", ["id", "action", "seq", "duplicate_id", "duplicate_seq"])
def test_native_decision_extraction_rejects_invalid_or_duplicate_events(mutation):
    from bench.runtime_contract import native_approval_decisions
    event = {"seq": 9, "type": "approval.decided", "payload": {"interrupt_id": "interrupt", "decision": {"action": "approve"}}}
    events = [event]
    if mutation == "id":
        event["payload"]["interrupt_id"] = ""
    elif mutation == "action":
        event["payload"]["decision"]["action"] = "guessed"
    elif mutation == "seq":
        event["seq"] = True
    else:
        other = copy.deepcopy(event)
        if mutation == "duplicate_id":
            other["seq"] = 10
        else:
            other["payload"]["interrupt_id"] = "different"
        events.append(other)
    with pytest.raises(ValueError):
        native_approval_decisions(events)


@pytest.mark.parametrize("mutation", ["missing", "fake_source", "factory_source", "hash", "bool_seq", "duplicate", "count", "bool_count"])
def test_normalization_requires_native_approval_evidence_for_positive_write_controls(mutation):
    inputs, row, _ = inputs_and_row()
    decision = row["approval_decisions"][0]
    if mutation == "missing":
        row["approval_decisions"] = []
    elif mutation == "fake_source":
        decision["source"] = "provider_text"
    elif mutation == "factory_source":
        decision["source"] = "factory_resume_command"
    elif mutation == "hash":
        decision["event_sha256"] = "bad"
    elif mutation == "bool_seq":
        decision["event_seq"] = True
    elif mutation == "duplicate":
        row["approval_decisions"].append(copy.deepcopy(decision))
    elif mutation == "count":
        row["approval_count"] = 2
    else:
        row["approval_count"] = True
    with pytest.raises(ValueError):
        normalize_observation(inputs, row)
