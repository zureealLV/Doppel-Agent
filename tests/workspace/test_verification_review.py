"""C1 definitions for S9; fake supervisor is not command/native test evidence."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from doppel_agent.workspace.verification import VerificationPipeline


def config(root, *, commands=None):
    path = root / ".doppel" / "verification.json"
    path.parent.mkdir(exist_ok=True)
    data = {"commands": commands or [{"name": "unit", "argv": ["python", "-m", "pytest", "tests with spaces"],
                                      "timeout_seconds": 10}], "max_output_bytes": 1024, "stop_on_failure": True}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class FakeSupervisor:
    def __init__(self):
        self.calls = []

    async def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(exit_code=0, stdout="fixture", stderr="", supervision="fixture_not_native")

    async def run_binary(self, argv, **kwargs):
        assert kwargs["require_tree_ownership"] is True
        value = await self.run(argv, **kwargs)
        return SimpleNamespace(exit_code=value.exit_code, stdout=value.stdout.encode(), stderr=value.stderr.encode(),
                               supervision=value.supervision)


def test_preview_is_immutable_exact_argv_and_not_execution_or_permission(tmp_path):
    config(tmp_path)
    fake = FakeSupervisor()
    pipeline = VerificationPipeline(tmp_path, supervisor=fake)
    plan = pipeline.prepare(["unit"])
    view = plan.as_dict()
    assert view["commands"][0]["argv"] == ["python", "-m", "pytest", "tests with spaces"]
    assert len(plan.plan_id) == 64 and len(view["source"]["config_hash"]) == 64
    assert view["source"]["path"] == ".doppel/verification.json"
    view["commands"][0]["argv"][0] = "edited public copy"
    assert pipeline.prepare(["unit"]) == plan and plan.commands[0].argv[0] == "python"
    assert fake.calls == []
    with pytest.raises(TypeError):
        pipeline.commands["replacement"] = plan.commands[0]
    with pytest.raises(PermissionError, match="command_grant"):
        asyncio.run(pipeline.run_reviewed(plan, run_id="fixture", command_grant=False))
    assert fake.calls == []


@pytest.mark.parametrize("change", ["argv", "whitespace", "replace_file", "removed"])
def test_config_change_after_preview_refuses_before_launch(tmp_path, change):
    path = config(tmp_path)
    fake = FakeSupervisor()
    pipeline = VerificationPipeline(tmp_path, supervisor=fake)
    plan = pipeline.prepare()
    raw = path.read_bytes()
    if change == "argv":
        config(tmp_path, commands=[{"name": "unit", "argv": ["other"], "timeout_seconds": 10}])
    elif change == "whitespace":
        path.write_bytes(raw + b"\n")
    elif change == "replace_file":
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(raw)
        replacement.replace(path)
    else:
        path.unlink()
    with pytest.raises(ValueError, match="verification_(review_stale|config_unavailable)"):
        asyncio.run(pipeline.run_reviewed(plan, run_id="fixture", command_grant=True))
    assert fake.calls == []


def test_forged_or_foreign_plan_never_changes_execution_argv(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    config(first)
    config(second)
    fake = FakeSupervisor()
    pipeline = VerificationPipeline(first, supervisor=fake)
    plan = pipeline.prepare()
    forged = replace(plan, commands=(replace(plan.commands[0], argv=("not-reviewed",)),))
    for candidate in (forged, VerificationPipeline(second).prepare()):
        with pytest.raises(ValueError, match="verification_review_stale"):
            asyncio.run(pipeline.run_reviewed(candidate, run_id="fixture", command_grant=True))
    assert fake.calls == []


def test_second_command_rechecks_config_and_report_identifies_plan_not_project_success(tmp_path):
    path = config(tmp_path, commands=[{"name": name, "argv": [name], "timeout_seconds": 10} for name in ("one", "two")])

    class ChangeAfterFirst(FakeSupervisor):
        async def run(self, argv, **kwargs):
            result = await super().run(argv, **kwargs)
            path.write_bytes(path.read_bytes() + b"\n")
            return result

    fake = ChangeAfterFirst()
    pipeline = VerificationPipeline(tmp_path, supervisor=fake)
    recorded = []

    async def record(result):
        recorded.append(result)

    with pytest.raises(ValueError, match="verification_review_stale"):
        asyncio.run(pipeline.run_reviewed(pipeline.prepare(), run_id="fixture", command_grant=True, on_result=record))
    assert len(fake.calls) == 1
    assert len(recorded) == 1 and recorded[0].name == "one"
    # C2 must durably record that partial/unknown operation, not repeat it.


@pytest.mark.parametrize("raw", ['{"commands":[],"commands":[]}', '{"commands":[]}' + ' ' * 65536,
                                 '{"commands":[{"name":"x","argv":["python"],"timeout_seconds":NaN}]}'],
                         ids=["duplicate-commands", "oversized", "nonfinite-timeout"])
def test_bounded_strict_config_rejects_duplicates_oversize_nonfinite(tmp_path, raw):
    path = tmp_path / ".doppel" / "verification.json"
    path.parent.mkdir()
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        VerificationPipeline(tmp_path)


def test_hardlinked_or_external_config_refused_without_reading_contents(tmp_path):
    path = config(tmp_path)
    alias = tmp_path / "linked.json"
    try:
        alias.hardlink_to(path)
    except OSError:
        pytest.skip("fixture volume does not support hardlinks")
    with pytest.raises(ValueError, match="verification_config_unavailable"):
        VerificationPipeline(tmp_path)
    with pytest.raises(ValueError, match="verification_config_unavailable"):
        VerificationPipeline(tmp_path, config_path=tmp_path.parent / "not-an-allowed-external-config.json")


def test_reviewed_fake_report_has_exact_plan_provenance_and_explicit_non_native_supervision(tmp_path):
    config(tmp_path)
    fake = FakeSupervisor()
    pipeline = VerificationPipeline(tmp_path, supervisor=fake)
    plan = pipeline.prepare()
    report = asyncio.run(pipeline.run_reviewed(plan, run_id="fixture", command_grant=True))
    assert report.as_dict()["review"]["plan_id"] == plan.plan_id
    assert report.as_dict()["review"]["source"] == plan.as_dict()["source"]
    assert report.results[0].supervision == "fixture_not_native"
    assert fake.calls[0][1]["cwd"] == tmp_path.resolve()
