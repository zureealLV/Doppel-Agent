"""Production approval boundaries, not event-name or answer-text validators."""

import asyncio
import copy
import json
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case


@pytest.mark.parametrize("case_id", ["approval-01", "approval-02"])
def test_approval_materialization_has_only_frozen_public_files(tmp_path, case_id):
    fixture = load_task_fixture(case_id)
    materialize_task_case(fixture, tmp_path / "agent")
    assert fixture.version == "1.5" and fixture.source_kind == "synthetic_control"
    assert {p.relative_to(tmp_path / "agent").as_posix() for p in (tmp_path / "agent").rglob("*") if p.is_file()} == {
        p for p, _ in fixture.public_files
    }
    assert fixture.allowed_edits == (("note.txt",) if case_id == "approval-01" else ())


@pytest.mark.parametrize("case_id,mode", [("approval-01", "graph"), ("approval-01", "deep"), ("approval-02", "graph")])
def test_native_approval_reconstruction_duplicate_resume_and_rejection(tmp_path, case_id, mode):
    from bench.runtime_approval_harness import probe_approval
    report = asyncio.run(probe_approval(load_task_fixture(case_id), mode, tmp_path / "agent"))
    assert report["deterministic_pass"], report
    assert report["actual_runtime"] == mode and report["approval_decision_count"] == 1
    assert report["wrong_interrupt"]["error"] == "ValueError"
    assert len(report["duplicate_resumes"]) == 2
    assert all(r["error"] == "ValueError" and r["snapshot"] == report["current"] for r in report["duplicate_resumes"])
    if case_id == "approval-01":
        assert report["native_trace"]["patch_apply_count"] == 1
        assert report["native_trace"]["command_calls"] == []
    else:
        assert report["native_trace"]["command_calls"] == []
        assert report["current"] == report["initial"]
        assert report["command_positive_control"]["deterministic_pass"]
        assert len(report["command_positive_control"]["native_trace"]["command_calls"]) == 1
    assert report["task_quality_scored"] is False and report["human_review"] == "pending"


@pytest.mark.parametrize("case_id", ["approval-01", "approval-02"])
@pytest.mark.parametrize("mutation", ["version", "grant", "decision", "inventory", "edit"])
def test_approval_loader_rejects_bound_or_decision_drift(tmp_path, case_id, mutation):
    shutil.copytree(FIXTURE_ROOT / case_id, tmp_path / case_id)
    meta = tmp_path / case_id / "fixture.json"
    data = json.loads(meta.read_bytes())
    if mutation == "version":
        data["fixture_version"] = "1.4"
    elif mutation == "grant":
        data["oracle"]["allowed_argv"] = [["{python}", "-c", "pass"]]
    elif mutation == "decision":
        data["oracle"]["decision"] = "reject" if case_id == "approval-01" else "approve"
    elif mutation == "inventory":
        data["oracle"]["hidden_files"] = {}
    else:
        data["allowed_edits"].append("README.md")
    meta.write_bytes(json.dumps(data).encode())
    with pytest.raises(ValueError):
        load_task_fixture(case_id, root=tmp_path)


@pytest.fixture(scope="module")
def approval_evidence(tmp_path_factory):
    from bench.runtime_approval_harness import probe_approval
    records = {}
    for case_id in ("approval-01", "approval-02"):
        fixture = load_task_fixture(case_id)
        workspace = tmp_path_factory.mktemp(case_id) / "agent"
        records[case_id] = fixture, workspace, asyncio.run(probe_approval(fixture, "graph", workspace))
    return records


@pytest.mark.parametrize("case_id", ["approval-01", "approval-02"])
@pytest.mark.parametrize("mutation", ["early_effect", "wrong_id_accepted", "duplicate_accepted", "duplicate_write", "decision_count", "native_execution_count", "protected_file", "wrong_argv"])
def test_approval_validator_rejects_side_effect_or_receipt_mutants(approval_evidence, case_id, mutation):
    from bench.runtime_approval_harness import validate_approval_evidence
    fixture, workspace, original = approval_evidence[case_id]
    assert all(validate_approval_evidence(fixture, workspace, original).values())
    evidence = copy.deepcopy(original)
    if mutation == "early_effect":
        evidence["paused_snapshot"]["extra.txt"] = "bad"
    elif mutation == "wrong_id_accepted":
        evidence["wrong_interrupt"]["error"] = None
    elif mutation == "duplicate_accepted":
        evidence["duplicate_resumes"][0]["error"] = None
    elif mutation == "duplicate_write":
        evidence["duplicate_resumes"][1]["snapshot"]["extra.txt"] = "bad"
    elif mutation == "decision_count":
        evidence["approval_decision_count"] = 2
    elif mutation == "native_execution_count":
        evidence["native_trace"]["patch_apply_count"] = 2
    elif mutation == "protected_file":
        evidence["current"]["README.md"] = "bad"
    elif case_id == "approval-01":
        evidence["permissions"]["command_execute"] = True
    else:
        evidence["command_positive_control"]["native_trace"]["command_calls"][0]["argv"] = ["{python}", "-c", "pass"]
    assert not all(validate_approval_evidence(fixture, workspace, evidence).values())
