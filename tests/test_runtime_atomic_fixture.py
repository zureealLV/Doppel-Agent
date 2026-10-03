"""Batch-gated config coherence and actual native failed-write controls."""

import asyncio
import copy
from hashlib import sha256
import json
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case
from bench.runtime_tdd_harness import external_oracle


def test_patch03_seed_and_reference_have_independent_frozen_behavior_proofs(tmp_path):
    fixture = load_task_fixture("patch-03")
    materialize_task_case(fixture, tmp_path / "agent")
    hidden = dict(fixture.hidden_files)
    assert fixture.version == "1.4" and fixture.source_kind == "synthetic_seed"
    assert set(fixture.allowed_edits) == {"service.py", "settings.json"}
    assert {p.name for p in (tmp_path / "agent").iterdir()} == {"service.py", "settings.json", "test_public.py", "LICENSE.txt"}
    for filename, count in [("target_tests.py", 2), ("regression_tests.py", 3), ("public_check.py", 4)]:
        result = asyncio.run(external_oracle(tmp_path / "agent", hidden[filename]))
        assert result["tests_run"] == count and result["errors"] == 0
        assert result["failures"] == (2 if filename == "target_tests.py" else 0)
    for filename, content in json.loads(hidden["reference_changes.json"]).items():
        (tmp_path / "agent" / filename).write_bytes(content.encode())
    for filename in ("target_tests.py", "regression_tests.py", "public_check.py"):
        assert asyncio.run(external_oracle(tmp_path / "agent", hidden[filename]))["exit_code"] == 0
    for filename, content in fixture.public_files:
        if filename not in fixture.allowed_edits:
            assert (tmp_path / "agent" / filename).read_bytes() == content


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_patch03_native_write_and_both_atomic_controls_are_required(tmp_path, mode):
    from bench.runtime_patch_harness import probe_patch
    report = asyncio.run(probe_patch(load_task_fixture("patch-03"), mode, tmp_path / "agent"))
    assert report["deterministic_pass"], report
    assert report["public_before"]["tests_run"] == report["public_after"]["tests_run"] == 4
    assert "candidate_on_seed" not in report
    assert [r["control"] for r in report["atomic_controls"]] == ["second_replace_failure", "stale_base"]
    assert all(r["deterministic_pass"] for r in report["atomic_controls"])
    rollback, stale = report["atomic_controls"]
    assert rollback["fault"]["apply_errors"] == ["OSError"]
    assert len(rollback["fault"]["replacements"]) == len(rollback["fault"]["rollbacks"]) == 1
    assert rollback["initial"] == rollback["current"]
    assert stale["fault"]["apply_errors"] == ["PatchConflictError"]
    assert stale["fault"]["replace_attempts"] == 0
    assert stale["pre_resume"] == stale["current"] != stale["initial"]


@pytest.mark.parametrize("mode", ["graph", "deep"])
@pytest.mark.parametrize("partial", ["settings_only", "implementation_only", "hardcoded_limit"])
def test_patch03_public_green_incomplete_config_repair_is_rejected(tmp_path, mode, partial):
    from bench.runtime_patch_harness import probe_patch
    fixture = load_task_fixture("patch-03")
    changes = json.loads(dict(fixture.hidden_files)["reference_changes.json"])
    if partial == "settings_only":
        changes.pop("service.py")
    elif partial == "implementation_only":
        changes.pop("settings.json")
    else:
        changes["service.py"] = dict(fixture.public_files)["service.py"].decode().replace("[:20]", "[:8]")
    report = asyncio.run(probe_patch(fixture, mode, tmp_path / "agent", changes=changes))
    assert report["public_after"]["exit_code"] == 0
    assert not report["deterministic_pass"]


@pytest.mark.parametrize("mutation", ["version", "source_kind", "public_count", "regression_bool", "edit", "oracle_kind", "atomic_inventory"])
def test_patch03_loader_rejects_cross_profile_or_bound_drift(tmp_path, mutation):
    shutil.copytree(FIXTURE_ROOT / "patch-03", tmp_path / "patch-03")
    meta = tmp_path / "patch-03/fixture.json"
    data = json.loads(meta.read_bytes())
    if mutation == "version":
        data["fixture_version"] = "1.3"
    elif mutation == "source_kind":
        data["source_kind"] = "synthetic_refactor"
    elif mutation == "public_count":
        data["oracle"]["expected_public_tests"] = 3
    elif mutation == "regression_bool":
        data["oracle"]["expected_regression_tests"] = True
    elif mutation == "edit":
        data["allowed_edits"].append("test_public.py")
    elif mutation == "oracle_kind":
        data["oracle"]["kind"] = "behavior_preserving_refactor"
    else:
        data["oracle"]["atomic_controls"] = ["stale_base"]
    meta.write_bytes(json.dumps(data).encode())
    with pytest.raises(ValueError):
        load_task_fixture("patch-03", root=tmp_path)


@pytest.fixture(scope="module")
def atomic_evidence(tmp_path_factory):
    from bench.runtime_patch_harness import probe_patch
    fixture = load_task_fixture("patch-03")
    workspace = tmp_path_factory.mktemp("atomic-evidence") / "agent"
    return fixture, workspace, asyncio.run(probe_patch(fixture, "graph", workspace))


@pytest.mark.parametrize("mutation", ["missing_control", "fake_fault", "no_real_write", "no_rollback", "stale_write", "leaked_temp", "control_hash", "control_runtime"])
def test_patch03_validator_rejects_claimed_or_incomplete_failure_controls(atomic_evidence, mutation):
    from bench.runtime_validators import validate_patch_evidence
    fixture, workspace, original = atomic_evidence
    assert all(validate_patch_evidence(fixture, workspace, original).values())
    evidence = copy.deepcopy(original)
    rollback, stale = evidence["atomic_controls"]
    if mutation == "missing_control":
        evidence["atomic_controls"].pop()
    elif mutation == "fake_fault":
        rollback["fault"]["apply_errors"] = []
    elif mutation == "no_real_write":
        rollback["fault"]["replacements"] = []
    elif mutation == "no_rollback":
        rollback["fault"]["rollbacks"] = []
    elif mutation == "stale_write":
        stale["fault"]["replace_attempts"] = 1
    elif mutation == "leaked_temp":
        rollback["current"][".settings.json.doppel-patch-leak.tmp"] = sha256(b"leak").hexdigest()
    elif mutation == "control_hash":
        rollback["fixture_sha256"] = "bad"
    else:
        stale["actual_runtime"] = "legacy"
    assert not all(validate_patch_evidence(fixture, workspace, evidence).values())
