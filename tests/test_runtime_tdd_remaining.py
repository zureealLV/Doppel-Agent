"""Independent remaining TDD seeds and actual service repair controls."""

import asyncio
import subprocess
import sys

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case
from bench.runtime_tdd_harness import probe_tdd


CASES = ("tdd-02", "tdd-03", "tdd-04")


def oracle(workspace, case_id, name):
    return subprocess.run([sys.executable, "-B", str(FIXTURE_ROOT / case_id / "oracle" / name), "-q"],
                          cwd=workspace, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("case_id", CASES)
def test_seed_target_assertions_fail_without_model_while_regressions_pass(tmp_path, case_id):
    fixture = load_task_fixture(case_id)
    workspace = tmp_path / "agent"
    materialize_task_case(fixture, workspace)
    target = oracle(workspace, case_id, "target_tests.py")
    assert target.returncode == 1, target.stderr
    assert "FAILED (failures=2)" in target.stderr
    assert "ERROR:" not in target.stderr
    regression = oracle(workspace, case_id, "regression_tests.py")
    assert regression.returncode == 0, regression.stderr
    assert "Ran 3 tests" in regression.stderr
    assert {p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()} == {
        "service.py", "test_public.py",
    }
    assert fixture.source_kind == "synthetic_seed"


@pytest.mark.parametrize("case_id", CASES)
def test_reference_control_passes_independent_targets_but_is_not_initial_input(tmp_path, case_id):
    fixture = load_task_fixture(case_id)
    workspace = tmp_path / "agent"
    materialize_task_case(fixture, workspace)
    hidden = dict(fixture.hidden_files)
    assert set(hidden) == {"target_tests.py", "regression_tests.py", "reference_source.py", "scripted_test.py"}
    assert not (workspace / "test_candidate.py").exists()
    (workspace / "service.py").write_bytes(hidden["reference_source.py"])
    (workspace / "test_candidate.py").write_bytes(hidden["scripted_test.py"])
    for name in ("target_tests.py", "regression_tests.py"):
        result = oracle(workspace, case_id, name)
        assert result.returncode == 0, result.stderr
    candidate = subprocess.run([sys.executable, "-B", "-m", "unittest", "-q", "test_candidate", "test_public"],
                               cwd=workspace, capture_output=True, timeout=10)
    assert candidate.returncode == 0, candidate.stderr


@pytest.mark.parametrize("case_id", CASES)
def test_actual_service_tdd_has_four_approvals_and_unchanged_tests_between_red_green(tmp_path, case_id):
    report = asyncio.run(probe_tdd(load_task_fixture(case_id), tmp_path / "agent"))
    assert report["deterministic_pass"] is True, report
    assert report["status"] == "completed"
    assert report["runtime"] == report["actual_runtime"] == "graph"
    assert report["boundary"] == "run_service"
    assert report["approval_count"] == 4
    assert report["command_exit_codes"] == report["patch_verification_exit_codes"] == [1, 0]
    assert report["external_target_before"]["failures"] == 2
    assert report["external_target_after"]["tests_run"] == 2
    assert report["external_regression_after"]["tests_run"] == 3
    assert report["checks"]["candidate_test_not_weakened"] is True
    assert report["human_review"] == "pending"
    assert report["task_quality_scored"] is False


def test_queue_candidate_green_cannot_mask_lost_cumulative_accepts(tmp_path, monkeypatch):
    import bench.runtime_tdd_harness as harness

    fixture = load_task_fixture("tdd-02")
    original = harness.ScriptedTddProvider
    golden = dict(fixture.hidden_files)["reference_source.py"]
    partial = golden.replace(b"        self.accepted += 1", b"        self.accepted = 1")
    assert partial != golden
    monkeypatch.setattr(harness, "ScriptedTddProvider", lambda test, _: original(test, partial))
    report = asyncio.run(harness.probe_tdd(fixture, tmp_path / "agent"))
    assert report["status"] == "completed"
    assert report["command_exit_codes"] == report["patch_verification_exit_codes"] == [1, 0]
    assert report["external_target_after"]["failures"] == 1
    assert not report["deterministic_pass"]


def test_stale_patch_candidate_green_cannot_mask_write_before_conflict_check(tmp_path, monkeypatch):
    import bench.runtime_tdd_harness as harness

    fixture = load_task_fixture("tdd-03")
    original = harness.ScriptedTddProvider
    golden = dict(fixture.hidden_files)["reference_source.py"]
    partial = golden.replace(b'        if self._hash(target) != proposal["base_hash"]:',
                             b'        target.write_text(proposal["content"], encoding="utf-8")\n'
                             b'        if self._hash(target) != proposal["base_hash"]:')
    # Keep the public fresh-apply case green: only write early on a stale base.
    partial = partial.replace(b'        target.write_text(proposal["content"], encoding="utf-8")\n        if',
                              b'        if self._hash(target) != proposal["base_hash"]:\n'
                              b'            target.write_text(proposal["content"], encoding="utf-8")\n        if', 1)
    assert partial != golden
    monkeypatch.setattr(harness, "ScriptedTddProvider", lambda test, _: original(test, partial))
    report = asyncio.run(harness.probe_tdd(fixture, tmp_path / "agent"))
    assert report["status"] == "completed"
    assert report["command_exit_codes"] == report["patch_verification_exit_codes"] == [1, 0]
    assert report["external_target_after"]["failures"] == 2
    assert not report["deterministic_pass"]


def test_cancel_candidate_green_cannot_mask_discarding_other_queued_work(tmp_path, monkeypatch):
    import bench.runtime_tdd_harness as harness

    fixture = load_task_fixture("tdd-04")
    original = harness.ScriptedTddProvider
    golden = dict(fixture.hidden_files)["reference_source.py"]
    partial = golden.replace(b'if future.cancelled():',
                             b'if any(item.cancelled() for item in self.futures.values()):')
    assert partial != golden
    monkeypatch.setattr(harness, "ScriptedTddProvider", lambda test, _: original(test, partial))
    report = asyncio.run(harness.probe_tdd(fixture, tmp_path / "agent"))
    assert report["status"] == "completed"
    assert report["command_exit_codes"] == report["patch_verification_exit_codes"] == [1, 0]
    assert report["external_target_after"]["failures"] == 1
    assert not report["deterministic_pass"]
