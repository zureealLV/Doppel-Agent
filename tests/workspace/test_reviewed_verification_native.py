"""S9 isolated Windows definitions, never executed during source construction.

Actual interpreter/Job/pipe fixtures, not real project tests/model quality proof.
Non-Windows skips do not waive the Windows native acceptance gates.
"""

import asyncio
import json
import os
import sys
import threading

import pytest

import doppel_agent.workspace.process_supervisor as process_module
from doppel_agent.workspace.process_supervisor import ProcessSupervisor
from doppel_agent.workspace.verification import VerificationPipeline


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Mandatory Windows strict reviewed-Job definitions at S9")


def configured(root, code, *, cap=1024, timeout=5):
    path = root / ".doppel" / "verification.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"commands": [{"name": "isolated-native", "argv": [sys.executable, "-c", code],
                                            "timeout_seconds": timeout}], "max_output_bytes": cap}), encoding="utf-8")
    supervisor = ProcessSupervisor()
    return VerificationPipeline(root, supervisor=supervisor), supervisor


def test_reviewed_native_has_no_text_spool_and_drains_actual_job_accounting(tmp_path, monkeypatch):
    pipeline, supervisor = configured(tmp_path, "import os; os.write(1,b'fixture\\xff')")
    empty_observed = []
    original = process_module._WindowsJob.wait_empty

    def actual_empty(job, *args, **kwargs):
        original(job, *args, **kwargs)
        empty_observed.append(True)

    monkeypatch.setattr(process_module._WindowsJob, "wait_empty", actual_empty)

    def no_spool(*args, **kwargs):
        pytest.fail("reviewed path created text spool")

    monkeypatch.setattr(process_module.tempfile, "TemporaryFile", no_spool)

    async def scenario():
        try:
            report = await pipeline.run_reviewed(pipeline.prepare(), run_id="native-reviewed", command_grant=True)
            assert report.success and report.results[0].stdout == "fixture\ufffd"
            assert report.results[0].supervision == "job_object" and empty_observed == [True]
            assert supervisor.active_count == 0 and not supervisor.cleanup_failed
        finally:
            await supervisor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["attach_failure", "overflow", "timeout"])
def test_reviewed_native_failed_attempt_is_not_truncated_success(tmp_path, monkeypatch, kind):
    marker = tmp_path / "executed.txt"
    code = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran',encoding='utf-8')"
    if kind == "attach_failure":
        def denied(handle):
            raise OSError("fixture job attach denial")
        monkeypatch.setattr(process_module, "_WindowsJob", denied)
    elif kind == "overflow":
        code = "import os; os.write(1,b'x'*65536)"
    else:
        code += "; import time; time.sleep(30)"
    pipeline, supervisor = configured(tmp_path, code, cap=1024, timeout=2)

    async def scenario():
        retained = None
        try:
            if kind == "attach_failure":
                # Keep SAME inherited external OSError input and native no-body gate.
                # Opaque Job factory must propagate cleanup unknown, not a report
                # asserting ordinary known supervision refusal or drained success.
                with pytest.raises(process_module.ProcessCleanupError) as error:
                    await pipeline.run_reviewed(pipeline.prepare(), run_id="native-failed-attempt", command_grant=True)
                retained = error.value.managed
                assert error.value.source is retained.startup and not retained.startup.resume_attempted
                assert supervisor._active['native-failed-attempt'][0] is retained
                assert supervisor.active_count == 1 and supervisor.cleanup_failed
                assert not marker.exists()
                with pytest.raises(process_module.ProcessCleanupError, match='quarantine'):
                    await supervisor.close()
                return
            report = await pipeline.run_reviewed(pipeline.prepare(), run_id="native-failed-attempt", command_grant=True)
            actual = report.results[0]
            assert not report.success and actual.exit_code is None and actual.stdout == actual.stderr == ""
            assert actual.error == {"overflow": "verification_output_limit", "timeout": "verification_timeout"}[kind]
            if kind == "timeout":
                assert marker.exists()  # Process body actually entered, not just a startup delay.
            assert supervisor.active_count == 0 and not supervisor.cleanup_failed
        finally:
            if kind == "attach_failure":
                # Disposable SAME native fixture teardown, NOT original admission/
                # cleanup recovery, no replacement process or registry reset.
                if retained is not None:
                    retained.process.kill()
                    retained.process.wait()
                    retained.process.stdout.close()
                    retained.process.stderr.close()
                    retained.process._handle.Close()
            else:
                await supervisor.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("inherited_pipes", [False, True])
def test_reviewed_parent_exit_drains_descendant_even_when_child_closes_pipes(tmp_path, inherited_pipes):
    ready, marker = tmp_path / "child-ready.txt", tmp_path / "orphan-finished.txt"
    child = (f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready',encoding='utf-8'); "
             f"time.sleep(4); pathlib.Path({str(marker)!r}).write_text('orphan',encoding='utf-8')")
    redirects = "" if inherited_pipes else ",stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL"
    parent = (f"import subprocess,sys,time,pathlib; subprocess.Popen([sys.executable,'-c',{child!r}]{redirects}); "
              f"ready=pathlib.Path({str(ready)!r}); deadline=time.monotonic()+1.5\n"
              "while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
              "assert ready.exists(), 'native child did not enter body'\n")
    pipeline, supervisor = configured(tmp_path, parent, timeout=2)

    async def scenario():
        try:
            report = await pipeline.run_reviewed(pipeline.prepare(), run_id="native-exited-parent", command_grant=True)
            assert ready.exists()
            assert report.success is (not inherited_pipes)
            if inherited_pipes:
                assert report.results[0].error == "verification_timeout"
            assert supervisor.active_count == 0 and not supervisor.cleanup_failed
            await asyncio.sleep(4.2)
            assert not marker.exists()
        finally:
            await supervisor.close()

    asyncio.run(scenario())


def test_reviewed_native_repeated_cancel_retains_registration_until_actual_job_drain(tmp_path, monkeypatch):
    ready = tmp_path / "entered.txt"
    code = f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready',encoding='utf-8'); time.sleep(30)"
    pipeline, supervisor = configured(tmp_path, code, timeout=10)
    entered, release = threading.Event(), threading.Event()
    original = process_module._WindowsJob.wait_empty

    def gated_empty(job, *args, **kwargs):
        original(job, *args, **kwargs)  # Actual native membership reaches zero first.
        entered.set()
        if not release.wait(5):
            raise RuntimeError("native fixture drain gate timed out")

    monkeypatch.setattr(process_module._WindowsJob, "wait_empty", gated_empty)

    async def scenario():
        task = asyncio.create_task(pipeline.run_reviewed(pipeline.prepare(), run_id="native-cancel", command_grant=True))
        try:
            async with asyncio.timeout(3):
                while not ready.exists():
                    if task.done():
                        await task
                        pytest.fail("native command finished before ready gate")
                    await asyncio.sleep(.01)
            task.cancel()
            async with asyncio.timeout(3):
                while not entered.is_set():
                    await asyncio.sleep(.01)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and supervisor.active_count == 1
            release.set()
            assert isinstance((await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError)
            assert supervisor.active_count == 0 and not supervisor.cleanup_failed
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await supervisor.close()

    asyncio.run(scenario())
