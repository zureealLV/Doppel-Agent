"""Two-file refactor controls: behavior starts green, delegation starts red."""

import asyncio
import copy
import json
from hashlib import sha256
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case
from bench.runtime_tdd_harness import external_oracle


def test_patch02_seed_is_behavior_correct_but_fails_two_refactor_boundaries(tmp_path):
    fixture = load_task_fixture("patch-02")
    assert fixture.version == "1.3" and fixture.source_kind == "synthetic_refactor"
    materialize_task_case(fixture, tmp_path / "agent")
    hidden = dict(fixture.hidden_files)
    assert {p.name for p in (tmp_path / "agent").iterdir()} == {"service.py", "helpers.py", "test_public.py", "LICENSE.txt"}
    assert set(fixture.allowed_edits) == {"service.py", "helpers.py"}
    for name, count in [("target_tests.py", 2), ("regression_tests.py", 5), ("public_check.py", 5)]:
        result = asyncio.run(external_oracle(tmp_path / "agent", hidden[name]))
        assert result["tests_run"] == count and result["errors"] == 0
        assert result["failures"] == (2 if name == "target_tests.py" else 0)


def test_patch02_reference_preserves_frozen_behavior_and_passes_refactor_controls(tmp_path):
    fixture = load_task_fixture("patch-02")
    materialize_task_case(fixture, tmp_path / "agent")
    hidden = dict(fixture.hidden_files)
    changes = json.loads(hidden["reference_changes.json"])
    assert set(changes) == {"service.py", "helpers.py"}
    for path, content in changes.items():
        (tmp_path / "agent" / path).write_bytes(content.encode())
    for name in ["target_tests.py", "regression_tests.py", "public_check.py"]:
        assert asyncio.run(external_oracle(tmp_path / "agent", hidden[name]))["exit_code"] == 0
    for path, content in fixture.public_files:
        if path not in changes:
            assert (tmp_path / "agent" / path).read_bytes() == content


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_patch02_native_service_refactor_preserves_public_behavior(tmp_path, mode):
    from bench.runtime_patch_harness import probe_patch
    report = asyncio.run(probe_patch(load_task_fixture("patch-02"), mode, tmp_path / "agent"))
    assert report["deterministic_pass"], report
    assert report["actual_runtime"] == mode and report["approval_count"] == 1
    assert report["public_before"]["tests_run"] == report["public_after"]["tests_run"] == 5
    assert report["checks"]["public_behavior_preserved"]
    assert "candidate_on_seed" not in report
    assert report["human_review"] == "pending" and report["task_quality_scored"] is False


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("partial", ["comment_only", "service_only", "changed_error_priority"])
def test_patch02_candidate_green_non_refactors_or_behavior_drift_are_rejected(tmp_path, mode, partial):
    from bench.runtime_patch_harness import probe_patch
    fixture = load_task_fixture("patch-02")
    changes = json.loads(dict(fixture.hidden_files)["reference_changes.json"])
    if partial == "comment_only":
        changes = {p: dict(fixture.public_files)[p].decode() + "\n# refactored\n" for p in fixture.allowed_edits}
    elif partial == "service_only":
        changes.pop("helpers.py")
    else:
        old = '    prefix = _text(prefix, "prefix")\n    return f"{prefix}: {normalize_name(name)}"'
        new = '    canonical = normalize_name(name)\n    prefix = _text(prefix, "prefix")\n    return f"{prefix}: {canonical}"'
        assert old in changes["helpers.py"]
        changes["helpers.py"] = changes["helpers.py"].replace(old, new)
    report = asyncio.run(probe_patch(fixture, mode, tmp_path / "agent", changes=changes))
    assert report["status"] == "completed" and report["public_before"]["exit_code"] == report["public_after"]["exit_code"] == 0
    assert not report["deterministic_pass"]
    if partial == "changed_error_priority":
        assert report["external_target_after"]["exit_code"] == 0
        assert report["external_regression_after"]["failures"] == 1
    elif partial == "comment_only":
        assert report["external_target_after"]["failures"] == 2
    else:
        assert not report["checks"]["required_multi_file_paths"]


@pytest.mark.parametrize("mutation", ["version", "source_kind", "protected_edit", "bool_count", "wrong_count", "reference_escape", "hidden_hash", "grant"])
def test_patch02_loader_freezes_refactor_specific_bounds(tmp_path, mutation):
    shutil.copytree(FIXTURE_ROOT / "patch-02", tmp_path / "patch-02")
    meta = tmp_path / "patch-02/fixture.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    if mutation == "version":
        data["fixture_version"] = "1.2"
    elif mutation == "source_kind":
        data["source_kind"] = "synthetic_seed"
    elif mutation == "protected_edit":
        data["allowed_edits"].append("test_public.py")
    elif mutation == "bool_count":
        data["oracle"]["expected_regression_tests"] = True
    elif mutation == "wrong_count":
        data["oracle"]["expected_public_tests"] = 4
    elif mutation == "reference_escape":
        path = tmp_path / "patch-02/oracle/reference_changes.json"
        changes = json.loads(path.read_bytes())
        changes["../outside.py"] = changes.pop("helpers.py")
        path.write_text(json.dumps(changes), encoding="utf-8")
        data["oracle"]["hidden_files"][path.name] = sha256(path.read_bytes()).hexdigest()
    elif mutation == "hidden_hash":
        data["oracle"]["hidden_files"]["target_tests.py"] = "bad"
    else:
        data["oracle"]["allowed_argv"] = [["python", "-c", "pass"]]
    meta.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_task_fixture("patch-02", root=tmp_path)


@pytest.fixture(scope="module")
def accepted_refactor_evidence(tmp_path_factory):
    from bench.runtime_patch_harness import probe_patch
    fixture = load_task_fixture("patch-02")
    workspace = tmp_path_factory.mktemp("refactor-evidence") / "agent"
    return fixture, workspace, asyncio.run(probe_patch(fixture, "graph", workspace))


@pytest.mark.parametrize("mutation", ["before_exit", "before_hash", "after_count", "changed_protected_test", "missing_helper", "response_snapshot"])
def test_patch02_validator_rejects_behavior_or_receipt_mutants(accepted_refactor_evidence, mutation):
    from bench.runtime_validators import validate_patch_evidence
    fixture, workspace, original = accepted_refactor_evidence
    assert all(validate_patch_evidence(fixture, workspace, original).values())
    evidence = copy.deepcopy(original)
    if mutation == "before_exit":
        evidence["public_before"]["exit_code"] = 1
    elif mutation == "before_hash":
        evidence["public_before"]["oracle_sha256"] = "bad"
    elif mutation == "after_count":
        evidence["public_after"]["tests_run"] = 4
    elif mutation == "changed_protected_test":
        evidence["receipts"][0]["snapshot"]["test_public.py"] = "bad"
    elif mutation == "missing_helper":
        evidence["receipts"][-1]["changed_paths"].remove("helpers.py")
    else:
        evidence["receipts"][-1]["snapshot"]["helpers.py"] = "bad"
    assert not all(validate_patch_evidence(fixture, workspace, evidence).values())
