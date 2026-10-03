"""Real isolated stdio server through the production service policy gateway."""

import asyncio
import copy
import json
import shutil

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture, materialize_task_case


@pytest.mark.parametrize("case_id", ["mcp-01", "mcp-02"])
def test_mcp_fixture_public_materialization_contains_no_expected_result(tmp_path, case_id):
    fixture = load_task_fixture(case_id)
    materialize_task_case(fixture, tmp_path / "agent")
    assert fixture.version == "1.6" and fixture.allowed_edits == ()
    assert {p.name for p in (tmp_path / "agent").iterdir()} == {"server.py", "README.md", "LICENSE.txt"}


@pytest.mark.parametrize("case_id", ["mcp-01", "mcp-02"])
@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_real_mcp_service_catalog_policy_content_and_process_cleanup(tmp_path, case_id, mode):
    from bench.runtime_mcp_harness import probe_mcp
    report = asyncio.run(probe_mcp(load_task_fixture(case_id), mode, tmp_path / "agent"))
    assert report["deterministic_pass"], report
    assert report["actual_runtime"] == mode and report["boundary"] == "run_service"
    assert report["ungranted_surface"] == [] and report["policy_denial"]["error"] == "PermissionError"
    assert report["server_process"]["bound_before_shutdown"] and report["server_process"]["exited"]
    assert report["server_process"]["identity_backend"] in {"windows-process-handle", "linux-pidfd"}
    assert report["manager_connections_after_close"] == 0
    if case_id == "mcp-02":
        assert report["gateway_results"][0]["application_view"]["content"] == []
        assert report["gateway_results"][1]["application_view"]["is_error"] is True
        assert report["gateway_results"][1]["success"] is False
        assert report["receipts"][-1]["response"].startswith("MCP tool error: ")
    assert report["human_review"] == "pending" and report["task_quality_scored"] is False


@pytest.mark.parametrize("case_id", ["mcp-01", "mcp-02"])
@pytest.mark.parametrize("mutation", ["version", "grant", "hidden_hash", "public_inventory", "edit"])
def test_mcp_loader_rejects_cross_profile_or_permission_drift(tmp_path, case_id, mutation):
    shutil.copytree(FIXTURE_ROOT / case_id, tmp_path / case_id)
    meta = tmp_path / case_id / "fixture.json"
    data = json.loads(meta.read_bytes())
    if mutation == "version":
        data["fixture_version"] = "1.5"
    elif mutation == "grant":
        data["oracle"]["allowed_argv"] = [["python", "-c", "pass"]]
    elif mutation == "hidden_hash":
        data["oracle"]["hidden_files"]["expected.json"] = "bad"
    elif mutation == "public_inventory":
        data["public_files"]["expected.json"] = "bad"
    else:
        data["allowed_edits"] = ["server.py"]
    meta.write_bytes(json.dumps(data).encode())
    with pytest.raises(ValueError):
        load_task_fixture(case_id, root=tmp_path)


@pytest.fixture(scope="module")
def mcp_evidence(tmp_path_factory):
    from bench.runtime_mcp_harness import probe_mcp
    records = {}
    for case_id in ("mcp-01", "mcp-02"):
        fixture = load_task_fixture(case_id)
        workspace = tmp_path_factory.mktemp(case_id) / "agent"
        records[case_id] = fixture, workspace, asyncio.run(probe_mcp(fixture, "graph", workspace))
    return records


@pytest.mark.parametrize("case_id", ["mcp-01", "mcp-02"])
@pytest.mark.parametrize("mutation", ["early_remote_call", "duplicate_remote", "fake_structured", "missing_error_flag", "unreaped_server", "fake_permission", "leaked_oracle"])
def test_mcp_validator_rejects_policy_content_or_cleanup_claims(mcp_evidence, case_id, mutation):
    from bench.runtime_mcp_harness import validate_mcp_evidence
    fixture, workspace, original = mcp_evidence[case_id]
    assert all(validate_mcp_evidence(fixture, workspace, original).values())
    evidence = copy.deepcopy(original)
    if mutation == "early_remote_call":
        evidence["approvals"][0]["prior_remote_calls"] = 1
    elif mutation == "duplicate_remote":
        evidence["remote_calls"].append(evidence["remote_calls"][0])
    elif mutation == "fake_structured":
        evidence["gateway_results"][0]["application_view"]["structured_content"] = {"fake": True}
    elif mutation == "missing_error_flag":
        evidence["gateway_results"][-1]["application_view"]["is_error"] = not evidence["gateway_results"][-1]["application_view"]["is_error"]
    elif mutation == "unreaped_server":
        evidence["server_process"]["exited"] = False
    elif mutation == "fake_permission":
        evidence["permissions"]["workspace_write"] = True
    else:
        evidence["current"]["expected.json"] = "bad"
    assert not all(validate_mcp_evidence(fixture, workspace, evidence).values())
