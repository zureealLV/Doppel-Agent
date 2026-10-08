"""C2c2 fake transport definitions, not native/process/project acceptance."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from doppel_agent.workspace.process_supervisor import ProcessOutputLimitError, ProcessSupervisionError
from doppel_agent.workspace.verification import VerificationPipeline


def pipeline(root, supervisor, *, cap=32, stop=True):
    path = root / ".doppel" / "verification.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"commands": [{"name": "one", "argv": ["fixture", "spaces in arg"], "timeout_seconds": 2},
                                            {"name": "two", "argv": ["fixture-two"], "timeout_seconds": 2}],
                                "max_output_bytes": cap, "stop_on_failure": stop}), encoding="utf-8")
    return VerificationPipeline(root, supervisor=supervisor)


def test_reviewed_uses_strict_binary_exact_cap_and_explicit_presentation_decoding(tmp_path):
    calls = []

    class Fake:
        async def run_binary(self, argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(exit_code=0, stdout=b"ok\xff", stderr=b"", supervision="fixture_not_native")

        async def run(self, *args, **kwargs):
            pytest.fail("reviewed verification entered text spool compatibility path")

    value = pipeline(tmp_path, Fake())
    seals = []

    async def seal(result):
        seals.append(result)

    report = asyncio.run(value.run_reviewed(value.prepare(), run_id="manual-fixture", command_grant=True, on_result=seal))
    assert report.success and report.results[0].stdout == "ok\ufffd" and len(seals) == 2
    argv, options = calls[0]
    assert argv == ("fixture", "spaces in arg") and options["require_tree_ownership"] is True
    assert options["output_limit"] == 16 and options["timeout_seconds"] == 2
    assert options["run_id"] == "manual-fixture"


@pytest.mark.parametrize("failure,error,supervision", [
    (TimeoutError, "verification_timeout", "terminated"),
    (ProcessOutputLimitError, "verification_output_limit", "terminated"),
    (ProcessSupervisionError, "verification_supervision_unavailable", "unavailable"),
])
@pytest.mark.parametrize("stop", [False, True])
def test_strict_failures_are_sealed_failed_attempts_not_success_or_raw_exception(tmp_path, failure, error, supervision, stop):
    calls = []

    class Fake:
        async def run_binary(self, argv, **kwargs):
            calls.append(argv)
            raise failure("private raw process message must not escape into evidence")

    value = pipeline(tmp_path, Fake(), stop=stop)
    report = asyncio.run(value.run_reviewed(value.prepare(), run_id="manual-fixture", command_grant=True))
    assert not report.success and len(calls) == (1 if stop else 2)
    for actual in report.results:
        assert actual.error == error and actual.supervision == supervision and actual.exit_code is None
        assert actual.stdout == actual.stderr == "" and not actual.success
    assert "private raw" not in json.dumps(report.as_dict())


def test_one_byte_total_budget_cannot_be_rounded_to_two_byte_success(tmp_path):
    class Fake:
        async def run_binary(self, argv, **kwargs):
            assert kwargs["output_limit"] == 1
            return SimpleNamespace(exit_code=0, stdout=b"a", stderr=b"b", supervision="fixture_not_native")

    value = pipeline(tmp_path, Fake(), cap=1)
    report = asyncio.run(value.run_reviewed(value.prepare(), run_id="manual-fixture", command_grant=True))
    assert not report.success and report.results[0].error == "verification_output_limit"


def test_direct_compatibility_run_remains_distinct_and_never_claims_reviewed_evidence(tmp_path):
    class Fake:
        async def run(self, argv, **kwargs):
            return SimpleNamespace(exit_code=0, stdout="compatibility", stderr="", supervision="fixture_not_native")

        async def run_binary(self, *args, **kwargs):
            pytest.fail("direct compatibility transport changed")

    value = pipeline(tmp_path, Fake())
    report = asyncio.run(value.run(run_id="direct-fixture"))
    assert report.success and "review" not in report.as_dict()
