"""Frozen multi-file inputs and actual reviewed service-boundary controls."""

import asyncio
import json

import pytest

from bench.runtime_fixtures import load_task_fixture, materialize_task_case
from bench.runtime_tdd_harness import external_oracle


def test_patch01_seed_fails_two_targets_and_only_public_files_are_visible(tmp_path):
    fixture = load_task_fixture("patch-01")
    materialize_task_case(fixture, tmp_path / "agent")
    hidden = dict(fixture.hidden_files)
    target = asyncio.run(external_oracle(tmp_path / "agent", hidden["target_tests.py"]))
    regression = asyncio.run(external_oracle(tmp_path / "agent", hidden["regression_tests.py"]))
    assert target["failures"] == 2 and target["errors"] == 0
    assert regression["tests_run"] == 3 and regression["exit_code"] == 0
    assert {p.name for p in (tmp_path / "agent").iterdir()} == {"service.py", "test_public.py", "README.md", "LICENSE.txt"}


def test_patch01_reference_passes_targets_regressions_and_new_tests(tmp_path):
    fixture = load_task_fixture("patch-01")
    materialize_task_case(fixture, tmp_path / "agent")
    hidden = dict(fixture.hidden_files)
    changes = json.loads(hidden["reference_changes.json"])
    assert set(changes) == set(fixture.allowed_edits)
    for path, content in changes.items():
        (tmp_path / "agent" / path).write_text(content, encoding="utf-8")
    for name in ("target_tests.py", "regression_tests.py", "public_check.py"):
        result = asyncio.run(external_oracle(tmp_path / "agent", hidden[name]))
        assert result["exit_code"] == 0, result


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_patch01_reconstructed_service_approves_exact_multi_file_patch(tmp_path, mode):
    from bench.runtime_patch_harness import probe_patch
    report = asyncio.run(probe_patch(load_task_fixture("patch-01"), mode, tmp_path / "agent"))
    assert report["deterministic_pass"], report
    assert report["actual_runtime"] == mode and report["boundary"] == "run_service"
    assert report["approval_count"] == 1 and report["reconstructed_service"]
    assert report["external_target_after"]["tests_run"] == 2
    assert report["candidate_on_seed"]["failures"] >= 1
    assert report["quality_scored"] is False


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("partial", ["ascii_only", "missing_docs", "weak_tests"])
def test_candidate_green_partial_repairs_are_rejected(tmp_path, mode, partial):
    from bench.runtime_patch_harness import probe_patch
    fixture = load_task_fixture("patch-01")
    changes = json.loads(dict(fixture.hidden_files)["reference_changes.json"])
    if partial == "ascii_only":
        changes["service.py"] = changes["service.py"].replace(".casefold()", ".lower()")
        changes["test_public.py"] = changes["test_public.py"].replace(" Stra\u00dfe ", " Alice ").replace("strasse", "alice")
    elif partial == "missing_docs":
        changes.pop("README.md")
    else:
        changes["test_public.py"] = dict(fixture.public_files)["test_public.py"].decode() + "\n# updated tests\n"
    report = asyncio.run(probe_patch(fixture, mode, tmp_path / "agent", changes=changes))
    assert report["status"] == "completed"
    assert report["public_after"]["exit_code"] == 0
    assert not report["deterministic_pass"]


@pytest.mark.parametrize("mutation", ["public_hash", "hidden_hash", "edit_escape", "grant", "seed_bool", "version", "hidden_public", "inventory", "reference_escape", "reference_type", "extra_oracle"])
def test_patch_fixture_rejects_metadata_or_reference_drift(tmp_path, mutation):
    import shutil
    from hashlib import sha256
    from bench.runtime_fixtures import FIXTURE_ROOT
    shutil.copytree(FIXTURE_ROOT / "patch-01", tmp_path / "patch-01")
    meta = tmp_path / "patch-01/fixture.json"
    data = json.loads(meta.read_text())
    if mutation == "public_hash":
        data["public_files"]["service.py"] = "bad"
    elif mutation == "hidden_hash":
        data["oracle"]["hidden_files"]["target_tests.py"] = "bad"
    elif mutation == "edit_escape":
        data["allowed_edits"][0] = "../secret.py"
    elif mutation == "grant":
        data["oracle"]["allowed_argv"] = [["python", "-c", "pass"]]
    elif mutation == "seed_bool":
        data["oracle"]["expected_seed_target_failures"] = True
    elif mutation == "version":
        data["fixture_version"] = "1.1"
    elif mutation == "hidden_public":
        data["public_files"]["oracle/target_tests.py"] = data["public_files"].pop("LICENSE.txt")
    elif mutation == "inventory":
        data["oracle"]["hidden_files"].pop("regression_tests.py")
    elif mutation.startswith("reference_"):
        path = tmp_path / "patch-01/oracle/reference_changes.json"
        changes = json.loads(path.read_text(encoding="utf-8"))
        if mutation == "reference_escape":
            changes["../secret.py"] = changes.pop("service.py")
        else:
            changes["service.py"] = None
        path.write_text(json.dumps(changes), encoding="utf-8")
        data["oracle"]["hidden_files"][path.name] = sha256(path.read_bytes()).hexdigest()
    else:
        data["oracle"]["trust_completed"] = True
    meta.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_task_fixture("patch-01", root=tmp_path)


@pytest.fixture(scope="module")
def accepted_patch_evidence(tmp_path_factory):
    from bench.runtime_patch_harness import probe_patch
    fixture = load_task_fixture("patch-01")
    workspace = tmp_path_factory.mktemp("patch-evidence") / "agent"
    return fixture, workspace, asyncio.run(probe_patch(fixture, "graph", workspace))


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "failed_read", "base", "receipt_snapshot", "extra_inventory", "missing_path", "identity", "native_grant", "oracle_hash", "fallback", "insensitive_tests", "approval_content"])
def test_patch_validator_rejects_incomplete_or_forged_trusted_evidence(accepted_patch_evidence, mutation):
    import copy
    from bench.runtime_validators import validate_patch_evidence
    fixture, workspace, original = accepted_patch_evidence
    assert all(validate_patch_evidence(fixture, workspace, original).values())
    evidence = copy.deepcopy(original)
    if mutation == "empty":
        evidence["receipts"] = []
    elif mutation == "duplicate":
        evidence["receipts"][1]["tool_call_id"] = evidence["receipts"][0]["tool_call_id"]
    elif mutation == "failed_read":
        evidence["receipts"][0]["success"] = False
    elif mutation == "base":
        evidence["initial"]["service.py"] = "bad"
    elif mutation == "receipt_snapshot":
        evidence["receipts"][-1]["snapshot"]["service.py"] = "bad"
    elif mutation == "extra_inventory":
        evidence["current"]["oracle.py"] = "bad"
    elif mutation == "missing_path":
        evidence["receipts"][-1]["changed_paths"].remove("README.md")
    elif mutation == "identity":
        evidence["receipts"][-1]["patch_id"] = "wrong"
    elif mutation == "native_grant":
        evidence["receipts"][-1]["native_verification_present"] = True
    elif mutation == "oracle_hash":
        evidence["external_target_after"]["oracle_sha256"] = "wrong"
    elif mutation == "fallback":
        evidence["actual_runtime"] = "legacy"
    elif mutation == "insensitive_tests":
        evidence["candidate_on_seed"]["exit_code"] = 0
    else:
        evidence["approval"]["content_sha256"]["service.py"] = "bad"
    assert not all(validate_patch_evidence(fixture, workspace, evidence).values())
