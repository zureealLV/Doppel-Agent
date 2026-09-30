"""Independent task fixtures and executable evidence, not keyword quality scores."""

from pathlib import Path
import json
import asyncio
import re

import pytest

from bench.runtime_matrix import RuntimeMatrix


ROOT = Path(__file__).resolve().parents[1]


def test_nav04_is_versioned_source_navigation_not_mcp_execution():
    matrix = RuntimeMatrix.load(ROOT / "bench/cases/runtime/manifest.json")
    case = next(case for case in matrix.cases if case.case_id == "nav-04")
    assert matrix.protocol_version == "1.2"
    assert case.permissions == {}
    assert "adapter" in case.prompt
    assert len(matrix.expand()) == 180


def test_nav04_materializes_only_frozen_public_sources(tmp_path):
    from bench.runtime_fixtures import load_task_fixture, materialize_task_case

    fixture = load_task_fixture("nav-04")
    target = tmp_path / "agent"
    digest = materialize_task_case(fixture, target)
    assert len(digest) == 64
    assert fixture.category == "navigation"
    assert fixture.allowed_edits == ()
    assert len(fixture.public_files) == 4
    assert {path for path, _ in fixture.public_files} == {
        "src/doppel_agent/mcp/tool_adapter.py", "src/doppel_agent/mcp/executor.py",
        "src/doppel_agent/permissions.py", "src/doppel_agent/runtime/service.py",
    }
    assert {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()} == {
        path for path, _ in fixture.public_files
    }
    assert not any(name in str(path) for path in target.rglob("*") for name in ("fixture.json", "answer_key", "oracle", "reference"))
    for path, payload in fixture.public_files:
        assert (target / path).read_bytes() == payload
    with pytest.raises(FileExistsError):
        materialize_task_case(fixture, target)


def test_navigation_validator_rejects_keyword_only_and_failed_reads(tmp_path):
    from bench.runtime_fixtures import load_task_fixture, materialize_task_case
    from bench.runtime_validators import validate_navigation_evidence

    fixture = load_task_fixture("nav-04")
    workspace = tmp_path / "agent"
    materialize_task_case(fixture, workspace)
    assert not validate_navigation_evidence(fixture, workspace, [])['deterministic_pass']
    rows = [{"path": path, "success": True, "content": payload.decode()} for path, payload in fixture.public_files]
    report = validate_navigation_evidence(fixture, workspace, rows)
    assert report["deterministic_pass"] is True
    assert report["human_review"] == "pending"
    assert report["task_quality_scored"] is False
    assert report["source_unchanged"] is True
    rows[0]["success"] = False
    assert not validate_navigation_evidence(fixture, workspace, rows)["deterministic_pass"]
    rows[0]["success"] = True
    rows[0]["content"] = "MCP policy gateway"
    assert not validate_navigation_evidence(fixture, workspace, rows)["deterministic_pass"]


def test_navigation_extra_edit_is_not_read_only_success(tmp_path):
    from bench.runtime_fixtures import load_task_fixture, materialize_task_case
    from bench.runtime_validators import validate_navigation_evidence

    fixture = load_task_fixture("nav-04")
    target = tmp_path / "agent"
    materialize_task_case(fixture, target)
    rows = [{"path": p, "success": True, "content": b.decode()} for p, b in fixture.public_files]
    (target / "unapproved.txt").write_text("edited")
    assert not validate_navigation_evidence(fixture, target, rows)["deterministic_pass"]


def test_navigation_evidence_combines_successful_pages_only(tmp_path):
    from bench.runtime_fixtures import load_task_fixture, materialize_task_case
    from bench.runtime_validators import validate_navigation_evidence

    fixture = load_task_fixture("nav-04")
    target = tmp_path / "agent"
    materialize_task_case(fixture, target)
    rows = [{"path": path, "success": True, "content": anchor}
            for path, anchors in fixture.oracle["required_reads"].items() for anchor in anchors]
    assert validate_navigation_evidence(fixture, target, rows)["deterministic_pass"] is True
    rows[-1]["success"] = False
    assert validate_navigation_evidence(fixture, target, rows)["deterministic_pass"] is False
    rows[-1]["success"] = True
    rows[-1]["path"] = "unrelated.py"
    assert validate_navigation_evidence(fixture, target, rows)["deterministic_pass"] is False


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_nav04_offline_audit_records_native_path_but_never_quality(tmp_path, mode):
    from bench.audit_runtime_task_fixtures import probe_navigation
    from bench.runtime_fixtures import load_task_fixture

    report = asyncio.run(probe_navigation(load_task_fixture("nav-04"), mode, tmp_path / "agent"))
    assert report["runtime"] == mode
    assert report["actual_runtime"] == mode
    assert report["status"] == "completed"
    assert report["fallback_runtime"] is None
    assert report["deterministic_pass"] is True
    assert report["source_unchanged"] is True
    assert report["human_review"] == "pending"
    assert report["task_quality_scored"] is False
    assert len(report["reads"]) == (11 if mode == "deep" else 4)
    assert len({row["path"] for row in report["reads"]}) == 4
    assert all(row["success"] is True and row["tool"] == "read_file" for row in report["reads"])
    assert all("content" not in row and row["response_sha256"] for row in report["reads"])


@pytest.mark.parametrize("mutation", ["hash", "escape", "hidden", "empty_oracle", "edit_escape", "nav_edit", "commit"])
def test_task_fixture_rejects_corruption_or_hidden_public_files(tmp_path, mutation):
    from bench.runtime_fixtures import FIXTURE_ROOT, load_task_fixture
    import shutil

    shutil.copytree(FIXTURE_ROOT / "nav-04", tmp_path / "nav-04")
    meta = tmp_path / "nav-04/fixture.json"
    data = json.loads(meta.read_text())
    path = next(iter(data["public_files"]))
    if mutation == "hash":
        data["public_files"][path] = "bad"
    elif mutation == "escape":
        data["public_files"]["../secret.py"] = data["public_files"].pop(path)
    elif mutation == "hidden":
        data["public_files"]["answer_key.json"] = data["public_files"].pop(path)
    elif mutation == "empty_oracle":
        data["oracle"]["required_reads"] = {}
    elif mutation == "edit_escape":
        data["allowed_edits"] = ["../../secret.txt"]
    elif mutation == "nav_edit":
        data["allowed_edits"] = [path]
    else:
        data["source_commit"] = "not-a-frozen-commit"
    meta.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_task_fixture("nav-04", root=tmp_path)


@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
def test_nav04_actual_factory_reads_are_observed_without_custom_tools(tmp_path, mode):
    from bench.runtime_fixtures import load_task_fixture, materialize_task_case
    from bench.runtime_validators import ReadEvidenceProvider, validate_navigation_evidence
    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.factory import create_runtime
    from doppel_agent.runtime.base import RunRequest

    fixture = load_task_fixture("nav-04")
    target = tmp_path / "agent"
    materialize_task_case(fixture, target)
    paths = list(fixture.oracle["required_reads"])

    class Reader:
        turn = 0
        path_index = 0

        def next_turn(self, messages, tools):
            assert not any(tool['function']['name'].startswith('mcp__') for tool in tools)
            offset = 0
            if self.turn:
                latest = next(message for message in reversed(messages) if message.role == "tool")
                continuation = re.search(r"next offset (\d+) @@", latest.content) if mode == "deep" else None
                if continuation:
                    offset = int(continuation[1])
                else:
                    self.path_index += 1
            self.turn += 1
            if self.path_index < len(paths):
                arguments = {"file_path" if mode == "deep" else "path": paths[self.path_index]}
                if mode == "deep":
                    arguments["offset"] = offset
                    arguments["limit"] = 100
                return ModelTurn(tool_calls=(ToolCall(str(self.turn), "read_file", {
                    **arguments,
                }),))
            return ModelTurn(content="Source navigation completed; explanation still requires human adjudication.")

    async def probe():
        provider = ReadEvidenceProvider(Reader())
        runtime = create_runtime(mode, target, provider, core_options={"max_steps": 16})
        try:
            result = await runtime.run(RunRequest("Find the MCP adapter and explain the policy boundary."))
            assert result.status == "completed"
            assert "fallback_runtime" not in result.metadata
            assert len(provider.reads) == (11 if mode == "deep" else 4)
            report = validate_navigation_evidence(fixture, target, provider.reads)
            assert report["deterministic_pass"] is True, json.dumps({
                "report": report,
                "observations": [{"path": row["path"], "success": row["success"],
                    "length": len(row["content"]),
                    "missing": [a for a in fixture.oracle["required_reads"].get(row["path"], [])
                                if a not in row["content"]],
                    "head": row["content"][:120]} for row in provider.reads],
            }, indent=2)
            assert report["task_quality_scored"] is False
        finally:
            if mode == "deep":
                await runtime.process_supervisor.close()

    asyncio.run(probe())
