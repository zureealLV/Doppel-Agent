"""Native cancellation/deadline proofs bind actual parent and child identities."""

import asyncio
import copy
import json
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case


def test_cancel_fixture_is_public_only_and_has_no_edit_permission(tmp_path):
    fixture = load_task_fixture("cancel-01")
    materialize_task_case(fixture, tmp_path / "agent")
    assert fixture.version == "1.7" and fixture.allowed_edits == ()
    assert {p.name for p in (tmp_path / "agent").iterdir()} == {"parent.py", "child.py", "README.md", "LICENSE.txt"}


def test_cancel_and_deadline_each_reap_actual_native_parent_and_child(tmp_path):
    from bench.runtime_cancel_harness import probe_cancel
    report = asyncio.run(probe_cancel(load_task_fixture("cancel-01"), "graph", tmp_path / "agent"))
    assert report["deterministic_pass"], report
    assert report["status"] == "cancelled" and report["termination"] == "cancel"
    deadline = report["deadline_control"]
    assert deadline["status"] == "failed" and deadline["error_class"] == "TimeoutError"
    for row in (report, deadline):
        assert len(row["processes"]) == 2 and all(p["bound_before_shutdown"] and p["exited"] for p in row["processes"])
        assert row["initial"] == row["current"]
        assert row["scheduler_active_after_close"] == 0
        assert len(row["native_commands"]) == 1


@pytest.mark.parametrize("mutation", ["version", "deadline_bool", "grant", "edit", "public_inventory"])
def test_cancel_loader_rejects_control_or_bound_drift(tmp_path, mutation):
    shutil.copytree(FIXTURE_ROOT / "cancel-01", tmp_path / "cancel-01")
    meta = tmp_path / "cancel-01/fixture.json"
    data = json.loads(meta.read_bytes())
    if mutation == "version":
        data["fixture_version"] = "1.6"
    elif mutation == "deadline_bool":
        data["oracle"]["deadline_seconds"] = True
    elif mutation == "grant":
        data["oracle"]["allowed_argv"] = [["{python}", "-c", "pass"]]
    elif mutation == "edit":
        data["allowed_edits"] = ["parent.py"]
    else:
        data["public_files"]["expected.json"] = "bad"
    meta.write_bytes(json.dumps(data).encode())
    with pytest.raises(ValueError):
        load_task_fixture("cancel-01", root=tmp_path)


@pytest.fixture(scope="module")
def cancellation_evidence(tmp_path_factory):
    from bench.runtime_cancel_harness import probe_cancel
    fixture = load_task_fixture("cancel-01")
    workspace = tmp_path_factory.mktemp("cancel-evidence") / "agent"
    return fixture, workspace, asyncio.run(probe_cancel(fixture, "graph", workspace))


@pytest.mark.parametrize("mutation", ["child_alive", "identity_unbound", "fake_cancel_event", "only_parent", "deadline_not_exercised", "active_job", "wrong_argv", "early_execution", "public_write"])
def test_cancel_validator_rejects_event_only_or_incomplete_process_proofs(cancellation_evidence, mutation):
    from bench.runtime_cancel_harness import validate_cancel_evidence
    fixture, workspace, original = cancellation_evidence
    assert all(validate_cancel_evidence(fixture, workspace, original).values())
    evidence = copy.deepcopy(original)
    if mutation == "child_alive":
        evidence["processes"][1]["exited"] = False
    elif mutation == "identity_unbound":
        evidence["processes"][0]["bound_before_shutdown"] = False
    elif mutation == "fake_cancel_event":
        evidence["native_commands"] = []
    elif mutation == "only_parent":
        evidence["processes"].pop()
    elif mutation == "deadline_not_exercised":
        evidence["deadline_control"]["error_class"] = None
    elif mutation == "active_job":
        evidence["scheduler_active_after_close"] = 1
    elif mutation == "wrong_argv":
        evidence["native_commands"][0]["argv"] = ["{python}", "-c", "pass"]
    elif mutation == "early_execution":
        evidence["processes_before_approval"] = 1
    else:
        evidence["current"]["written.txt"] = "bad"
    assert not all(validate_cancel_evidence(fixture, workspace, evidence).values())
