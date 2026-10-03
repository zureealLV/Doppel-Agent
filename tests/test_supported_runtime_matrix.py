"""Complete supported scripted coverage is separate from paid/model quality.

Written with the version implementation batch; execution is deferred to its
consolidated acceptance phase, as explicitly requested by Lv.
"""

import asyncio
import copy
import shutil
from pathlib import Path

import pytest

from bench.runtime_contract import NativeTaskContract
from bench.runtime_fixtures import load_task_fixture, materialize_task_case


ROOT = Path(__file__).resolve().parents[1]


def test_native_contract_load_uses_explicit_checkout_not_module_checkout(tmp_path):
    folder = tmp_path / "bench/cases/runtime"
    folder.mkdir(parents=True)
    for name in ("manifest.json", "capabilities.json"):
        shutil.copyfile(ROOT / "bench/cases/runtime" / name, folder / name)
    contract = NativeTaskContract.load(root=tmp_path)
    assert contract.matrix.manifest_sha256 == NativeTaskContract.load().matrix.manifest_sha256
    (folder / "capabilities.json").unlink()
    with pytest.raises(FileNotFoundError):
        NativeTaskContract.load(root=tmp_path)


def test_all_twenty_cases_have_frozen_controls_without_exposing_review_keys(tmp_path):
    from bench.runtime_readonly_harness import load_matrix_fixtures
    contract = NativeTaskContract.load()
    fixtures = load_matrix_fixtures(ROOT, contract)
    assert len(fixtures) == 20 and {fixture.case_id for fixture in fixtures} == {case.case_id for case in contract.matrix.cases}
    for fixture in fixtures:
        if fixture.case_id.startswith("review-"):
            workspace = tmp_path / fixture.case_id
            materialize_task_case(fixture, workspace)
            assert {p.name for p in workspace.iterdir()} == {"service.py"}
            assert set(dict(fixture.hidden_files)) == {"answer_key.json"}
            assert fixture.allowed_edits == ()
        elif fixture.case_id in {"nav-01", "nav-02", "nav-03"}:
            assert len(fixture.public_files) > 20  # retain the original whole source snapshot, not a one-file shortcut
            assert all(path.startswith("src/doppel_agent/") for path, _ in fixture.public_files)


@pytest.mark.parametrize("boundary,count", [("direct_factory", 81), ("run_service", 126)])
def test_supported_selection_is_complete_and_contains_three_independent_repeats(boundary, count):
    from bench.run_supported_runtime_matrix import supported_selection
    contract = NativeTaskContract.load()
    selection = supported_selection(contract, boundary)
    assert len(selection) == count and len(set(selection)) == count
    assert set(key for _, key in selection) == set(contract.capabilities[boundary]["supported_run_keys"])
    assert {int(key.rsplit(":", 1)[1]) for _, key in selection} == {1, 2, 3}
    with pytest.raises(ValueError):
        supported_selection(contract, "pooled")


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
@pytest.mark.parametrize("boundary", ["direct_factory", "run_service"])
def test_review_read_controls_use_the_exact_native_boundary_without_semantic_self_grading(tmp_path, mode, boundary):
    from bench.runtime_readonly_harness import load_matrix_fixtures, probe_readonly
    contract = NativeTaskContract.load()
    fixture = next(f for f in load_matrix_fixtures(ROOT, contract) if f.case_id == "review-01")
    row = asyncio.run(probe_readonly(fixture, mode, tmp_path / "agent", boundary=boundary, _contract=contract))
    assert row["deterministic_pass"], row
    assert row["boundary"] == boundary and row["actual_runtime"] == mode
    assert row["source_unchanged"] is True and row["permissions"] == {}
    assert row["human_review"] == "pending" and row["task_quality_scored"] is False
    assert "seeded_finding_match" not in row and "answer_key" not in {p for p in row["initial"]}


@pytest.mark.parametrize("case_id", ["patch-01", "patch-02", "patch-03"])
def test_factory_deep_patch_uses_native_same_instance_resume_not_a_service_proxy(tmp_path, case_id):
    from bench.runtime_factory_patch_harness import probe_factory_patch
    row = asyncio.run(probe_factory_patch(load_task_fixture(case_id), "deep", tmp_path / "agent"))
    assert row["deterministic_pass"], row
    assert row["boundary"] == "direct_factory" and row["factory_same_instance"] is True
    assert row["reconstructed_service"] is False
    assert row["actual_runtime"] == "deep" and row["permissions"] == {"workspace_write": True}
    if case_id == "patch-03":
        assert [control["control"] for control in row["atomic_controls"]] == ["second_replace_failure", "stale_base"]
        assert all(control["boundary"] == "direct_factory" for control in row["atomic_controls"])


def test_factory_result_cannot_be_relabelled_as_service_reconstruction(tmp_path):
    from bench.runtime_factory_patch_harness import probe_factory_patch
    from bench.runtime_validators import validate_patch_evidence
    fixture, workspace = load_task_fixture("patch-02"), tmp_path / "agent"
    row = asyncio.run(probe_factory_patch(fixture, "deep", workspace))
    assert all(validate_patch_evidence(fixture, workspace, row, boundary="direct_factory").values())
    assert not all(validate_patch_evidence(fixture, workspace, row).values())
    forged = copy.deepcopy(row)
    forged["boundary"], forged["reconstructed_service"] = "run_service", True
    assert not all(validate_patch_evidence(fixture, workspace, forged).values())


def test_supported_input_seal_rejects_omissions_or_repeat_relabelling():
    from bench.run_supported_runtime_matrix import supported_selection
    from bench.runtime_audit_inputs import AuditInputs
    from bench.runtime_readonly_harness import load_matrix_fixtures
    contract = NativeTaskContract.load()
    fixtures = load_matrix_fixtures(ROOT, contract)
    selected = supported_selection(contract, "run_service")
    inputs = AuditInputs.capture(contract, fixtures, selected, {}, scope="supported_matrix")
    assert inputs.document()["budget"]["repeat_ids"] == [1, 2, 3]
    for invalid in (selected[:-1], selected + (selected[0],), (("run_service", "cancel-01:deep:1"),)):
        with pytest.raises(ValueError):
            AuditInputs.capture(contract, fixtures, invalid, {}, scope="supported_matrix")


def test_scripted_matrix_records_failures_and_resumes_without_reexecuting_any_saved_key(tmp_path):
    # Fake dispatcher tests orchestration only. Native behavior is covered above
    # and by the actual full-supported CLI gates at version acceptance.
    from bench.run_supported_runtime_matrix import run_supported_matrix
    seen = []

    async def probe(fixture, run, workspace, contract, boundary):
        seen.append((run.run_key, workspace))
        if len(seen) == 1:
            raise RuntimeError("secret-do-not-persist")
        return {"case_id": fixture.case_id, "fixture_sha256": fixture.sha256, "fixture_version": fixture.version,
                "permissions": dict(run.permissions), "runtime": run.runtime, "actual_runtime": run.runtime,
                "boundary": boundary, "status": "cancelled" if fixture.category == "concurrent_cancel" else "completed",
                "human_review": "pending", "task_quality_scored": False, "checks": {"orchestration_fixture_only": True},
                "deterministic_pass": True, "receipts": [],
                # Deliberately synthetic envelope for orchestration only. The
                # private dispatcher makes this store non-native/non-acceptable.
                "approval_decisions": [] if fixture.category in {"navigation", "known_answer_review"} else [
                    {"source": "service_event", "event_seq": 1, "event_sha256": "a" * 64,
                     "interrupt_id": "synthetic-unit-test", "action": "approve"}]}

    report = asyncio.run(run_supported_matrix(ROOT, "run_service", tmp_path / "results", _probe=probe))
    assert len(seen) == 126 and len({path for _, path in seen}) == 126
    assert report["recorded_run_count"] == report["selected_run_count"] == 126 and report["complete_denominator"] is True
    assert report["passed"] is False and report["infrastructure_failure_count"] == 1
    assert report["full_matrix_ready"] is False and report["task_quality_scored"] is False
    assert "secret-do-not-persist" not in str(report)
    second = asyncio.run(run_supported_matrix(ROOT, "run_service", tmp_path / "results", _probe=probe))
    assert len(seen) == 126 and second["passed"] is False
    assert report["inputs_sha256"] == second["inputs_sha256"]
