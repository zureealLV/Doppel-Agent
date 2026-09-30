"""Seeded contracts, real TDD chronology and external oracles."""

import asyncio
import subprocess
import sys
import copy
import json
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case


def run_hidden(workspace, name):
    return subprocess.run([sys.executable, "-B", str(FIXTURE_ROOT / "tdd-01/oracle" / name), "-q"],
                          cwd=workspace, capture_output=True, text=True, timeout=10)


def test_tdd01_seed_fails_target_but_passes_other_contracts(tmp_path):
    fixture = load_task_fixture("tdd-01")
    workspace = tmp_path / "agent"
    materialize_task_case(fixture, workspace)
    target = run_hidden(workspace, "target_tests.py")
    assert target.returncode == 1
    assert "FAILED (failures=2)" in target.stderr
    assert "ERROR:" not in target.stderr
    assert run_hidden(workspace, "regression_tests.py").returncode == 0
    assert not (workspace / "test_candidate.py").exists()
    assert {p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()} == {
        "service.py", "test_public.py",
    }


def test_tdd01_reference_is_not_materialized_and_passes_independent_oracles(tmp_path):
    fixture = load_task_fixture("tdd-01")
    workspace = tmp_path / "agent"
    materialize_task_case(fixture, workspace)
    hidden = dict(fixture.hidden_files)
    assert len(hidden) == 4
    (workspace / "service.py").write_bytes(hidden["reference_source.py"])
    (workspace / "test_candidate.py").write_bytes(hidden["scripted_test.py"])
    assert run_hidden(workspace, "target_tests.py").returncode == 0
    assert run_hidden(workspace, "regression_tests.py").returncode == 0
    candidate = subprocess.run([sys.executable, "-B", "-m", "unittest", "-q", "test_candidate", "test_public"],
                               cwd=workspace, capture_output=True, timeout=10)
    assert candidate.returncode == 0
    assert not (workspace / "oracle").exists()
    assert not (workspace / "fixture.json").exists()


@pytest.fixture(scope="module")
def accepted_tdd01(tmp_path_factory):
    from bench.runtime_tdd_harness import probe_tdd

    workspace = tmp_path_factory.mktemp("tdd-control") / "agent"
    fixture = load_task_fixture("tdd-01")
    return fixture, workspace, asyncio.run(probe_tdd(fixture, workspace))


def test_tdd01_real_service_records_red_before_source_patch_and_green_after(accepted_tdd01):
    _, _, report = accepted_tdd01

    assert report["deterministic_pass"] is True, report
    assert report["runtime"] == report["actual_runtime"] == "graph"
    assert report["boundary"] == "run_service"
    assert report["status"] == "completed"
    assert report["command_exit_codes"] == [1, 0]
    assert report["patch_verification_exit_codes"] == [1, 0]
    assert report["external_target_before"]["failures"] == 2
    assert report["external_target_after"]["exit_code"] == 0
    assert report["external_regression_after"]["exit_code"] == 0
    assert report["approval_count"] == 4
    assert report["human_review"] == "pending"
    assert report["task_quality_scored"] is False


@pytest.mark.parametrize("mutation", ["order", "no_red", "error_not_assertion", "weakened_test", "argv", "early_effect",
                                      "target_failure", "oracle_hash", "duplicate_ids"])
def test_tdd_validator_rejects_fabricated_completion_and_incomplete_chronology(accepted_tdd01, mutation):
    from bench.runtime_tdd_harness import command_argv, digest
    from bench.runtime_validators import validate_tdd_evidence

    fixture, workspace, original = accepted_tdd01
    evidence = copy.deepcopy(original)
    rows = evidence["receipts"]
    if mutation == "order":
        rows[1], rows[3] = rows[3], rows[1]
    elif mutation == "no_red":
        rows[2]["exit_code"] = 0
    elif mutation == "error_not_assertion":
        rows[2]["errors"] = 1
    elif mutation == "weakened_test":
        rows[3]["snapshot"]["test_candidate.py"] = "changed-test"
    elif mutation == "argv":
        rows[2]["argv_sha256"] = "other-command"
    elif mutation == "early_effect":
        evidence["approvals"][0]["no_unapproved_effects"] = False
    elif mutation == "target_failure":
        evidence["external_target_after"]["exit_code"] = 1
    elif mutation == "oracle_hash":
        evidence["external_target_after"]["oracle_sha256"] = "unfrozen-oracle"
    else:
        for row in rows:
            row["tool_call_id"] = "replayed-call"
    checks = validate_tdd_evidence(fixture, workspace, evidence, argv_sha256=digest(command_argv()))
    assert not all(checks.values()), checks


@pytest.mark.parametrize("mutation", ["hidden_hash", "argv", "edits", "source_kind", "hidden_inventory"])
def test_tdd_fixture_rejects_drift_or_unbounded_controls(tmp_path, mutation):
    shutil.copytree(FIXTURE_ROOT / "tdd-01", tmp_path / "tdd-01")
    path = tmp_path / "tdd-01/fixture.json"
    data = json.loads(path.read_text())
    if mutation == "hidden_hash":
        data["oracle"]["hidden_files"]["target_tests.py"] = "changed"
    elif mutation == "argv":
        data["oracle"]["allowed_argv"] = [["{python}", "-c", "print('anything')"]]
    elif mutation == "edits":
        data["allowed_edits"].append("test_public.py")
    elif mutation == "source_kind":
        data["source_kind"] = "git_snapshot"
    else:
        data["oracle"]["hidden_files"]["secret.py"] = "wrong"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_task_fixture("tdd-01", root=tmp_path)


def test_tdd01_candidate_green_is_not_enough_when_independent_target_fails(tmp_path, monkeypatch):
    import bench.runtime_tdd_harness as harness

    fixture = load_task_fixture("tdd-01")
    original = harness.ScriptedTddProvider
    fixed = dict(fixture.hidden_files)["reference_source.py"].decode().replace(
        'return self.rows[self.keys[idempotency_key]], False',
        'record = self.rows[self.keys[idempotency_key]]\n            record["payload"] = payload\n            return record, False',
    ).encode()
    monkeypatch.setattr(harness, "ScriptedTddProvider", lambda test, _: original(test, fixed))
    report = asyncio.run(harness.probe_tdd(fixture, tmp_path / "agent"))
    assert report["status"] == "completed"
    assert report["command_exit_codes"] == [1, 0]
    assert report["patch_verification_exit_codes"] == [1, 0]
    assert report["external_target_after"]["failures"] == 1
    assert not report["deterministic_pass"]


def test_tdd_validator_rejects_an_oracle_leaked_before_run(accepted_tdd01, tmp_path):
    from hashlib import sha256
    from bench.runtime_tdd_harness import command_argv, digest
    from bench.runtime_validators import validate_tdd_evidence

    fixture, workspace, original = accepted_tdd01
    target = tmp_path / "copy"
    shutil.copytree(workspace, target)
    (target / "leaked_oracle.txt").write_bytes(b"secret expected answer")
    evidence = copy.deepcopy(original)
    unexpected = sha256(b"secret expected answer").hexdigest()
    evidence["initial"]["leaked_oracle.txt"] = unexpected
    for row in evidence["receipts"]:
        row["snapshot"]["leaked_oracle.txt"] = unexpected
    checks = validate_tdd_evidence(fixture, target, evidence, argv_sha256=digest(command_argv()))
    assert not all(checks.values()), checks


def test_offline_task_audit_separates_factory_navigation_from_supported_service_tdd():
    from bench.audit_runtime_task_fixtures import audit

    report = asyncio.run(audit())
    assert report["schema_version"] == "1.1"
    assert report["fixture_cases"] == ["nav-04", "tdd-01"]
    assert report["passed"] is True
    assert len(report["runs"]) == 4
    assert [(row["case_id"], row["runtime"], row["boundary"]) for row in report["runs"]] == [
        ("nav-04", "legacy", "direct_factory"), ("nav-04", "graph", "direct_factory"),
        ("nav-04", "deep", "direct_factory"), ("tdd-01", "graph", "run_service"),
    ]
    assert report["full_matrix_ready"] is False
    assert report["task_quality_scored"] is False
