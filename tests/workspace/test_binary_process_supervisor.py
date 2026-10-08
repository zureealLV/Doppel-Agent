"""S6 owned child/transport definitions. Not executed during construction."""

import asyncio
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import doppel_agent.workspace.process_supervisor as process_module

from doppel_agent.workspace.process_supervisor import (
    ProcessCleanupError,
    ProcessOutputLimitError,
    ProcessSupervisionError,
    ProcessSupervisor,
    _ManagedProcess,
)


class BinaryProcessSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_windows_admission_attaches_before_resume_without_fallback(self):
        events = []

        class Process:
            pid, _handle = 123, 456
            stdout, stderr = io.BytesIO(), io.BytesIO()
            returncode = None

            def kill(self):
                events.append("kill")

            def wait(self):
                events.append("wait")
                self.returncode = 0
                return 0

        class Job:
            def __init__(self, handle):
                self.handle = handle
                events.append("attach")

            def close(self):
                events.append("close-job")

        process = Process()

        def popen(argv, **kwargs):
            self.assertTrue(kwargs["creationflags"] & 0x00000004)
            self.assertFalse(kwargs["shell"])
            events.append("suspended-spawn")
            return process

        def resume(pid):
            self.assertEqual(pid, process.pid)
            self.assertEqual(events, ["suspended-spawn", "attach"])
            events.append("resume")

        root = Path.cwd()  # constructed before the platform simulation
        with patch.object(process_module.os, "name", "nt"), patch.object(process_module.subprocess, "Popen", popen), \
             patch.object(process_module, "_WindowsJob", Job), \
             patch.object(process_module, "_resume_suspended_primary_thread", resume):
            managed = ProcessSupervisor._start(["fixture"], root, {}, None, None, require_tree_ownership=True)
        self.assertEqual(events, ["suspended-spawn", "attach", "resume"])
        self.assertEqual(managed.supervision, "job_object")
        managed.close()
        process.stdout.close()
        process.stderr.close()

    async def test_strict_windows_job_or_resume_failure_kills_joins_and_closes_before_error(self):
        for phase in ("attach", "resume"):
            events = []

            class Process:
                pid, _handle = 123, 456
                stdout, stderr = io.BytesIO(), io.BytesIO()
                returncode = None

                def kill(self, events=events):
                    events.append("kill")

                def wait(self, events=events):
                    events.append("wait")
                    self.returncode = -1
                    return -1

            class Job:
                def __init__(self, handle, phase=phase, events=events):
                    events.append("attach")
                    if phase == "attach":
                        raise OSError("fixture attach failure")

                def close(self, events=events):
                    events.append("close-job")

                def terminate_checked(self, events=events):
                    events.append("terminate-job")

                def wait_empty(self, events=events):
                    events.append("empty-job")

                def close_checked(self):
                    self.close()

            def resume(pid, events=events):
                events.append("resume")
                raise OSError("fixture resume failure")

            process, root = Process(), Path.cwd()
            with patch.object(process_module.os, "name", "nt"), \
                 patch.object(process_module.subprocess, "Popen", return_value=process), \
                 patch.object(process_module, "_WindowsJob", Job), \
                 patch.object(process_module, "_resume_suspended_primary_thread", resume):
                error_type = ProcessCleanupError if phase == "attach" else ProcessSupervisionError
                message = "process startup cleanup unproved" if phase == "attach" else "strict process ownership unavailable"
                with self.assertRaisesRegex(error_type, message) as error:
                    ProcessSupervisor._start(["fixture"], root, {}, None, None, require_tree_ownership=True)
            if phase == "attach":
                # SAME inherited OSError before external Job constructor returns:
                # opaque allocation, not a known no-Job/strict cleanup receipt.
                self.assertIs(error.exception.managed.process, process)
                self.assertIs(error.exception.source, error.exception.managed.startup)
                self.assertTrue(error.exception.source.job_attempted)
                self.assertFalse(error.exception.source.job_returned)
                self.assertEqual(events, ["attach"])
                self.assertNotIn("resume", events)
                self.assertFalse(process.stdout.closed or process.stderr.closed)
                process.stdout.close()  # Disposable fixture teardown ONLY, no source recovery.
                process.stderr.close()
            else:
                self.assertEqual(events[-2:], ["kill", "wait"])
                self.assertTrue(process.stdout.closed and process.stderr.closed)
                self.assertIn("close-job", events)

    @unittest.skipUnless(os.name == "nt", "Windows suspended-child/Job gate, mandatory on Windows at S9")
    async def test_real_windows_failed_attach_never_executes_child_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "child-ran.txt"
            supervisor = ProcessSupervisor()
            code = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran',encoding='utf-8')"
            managed = None
            try:
                with patch.object(process_module, "_WindowsJob", side_effect=OSError("fixture denied job")):
                    with self.assertRaises(ProcessCleanupError) as error:
                        await supervisor.run_binary(
                            [sys.executable, "-c", code], cwd=root, run_id="strict-attach-denied",
                            require_tree_ownership=True,
                        )
                managed = error.exception.managed
                self.assertIs(error.exception.source, managed.startup)
                self.assertIs(supervisor._active['strict-attach-denied'][0], managed)
                self.assertFalse(managed.startup.resume_attempted)
                self.assertFalse(marker.exists())
                self.assertEqual(supervisor.active_count, 1)
                self.assertTrue(supervisor.cleanup_failed)
                with self.assertRaisesRegex(ProcessCleanupError, 'quarantine'):
                    await supervisor.close()
            finally:
                # S9-only explicit teardown of SAME suspended fixture process.
                # Does NOT unregister/reset supervisor or prove original cleanup.
                if managed is not None:
                    managed.process.kill()
                    managed.process.wait()
                    managed.process.stdout.close()
                    managed.process.stderr.close()
                    managed.process._handle.Close()

    async def test_strict_child_parent_exit_does_not_abandon_inherited_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supervisor = ProcessSupervisor()
            marker = root / "orphan-finished.txt"
            ready = root / "child-ready.txt"
            child = (f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready',encoding='utf-8'); time.sleep(4); "
                     f"pathlib.Path({str(marker)!r}).write_text('orphan',encoding='utf-8')")
            parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}])"
            registered = []
            register = supervisor._register

            def capture(run_id, managed):
                registered.append(managed)
                register(run_id, managed)

            task = None
            try:
                with patch.object(supervisor, "_register", capture):
                    task = asyncio.create_task(supervisor.run_binary(
                        [sys.executable, "-c", parent], cwd=root, run_id="strict-exited-parent",
                        timeout_seconds=2, require_tree_ownership=True,
                    ))
                    async with asyncio.timeout(1.5):
                        while not ready.exists() or not registered or registered[0].process.poll() is None:
                            if task.done():
                                await task  # surface startup failure, not a fictitious exited-parent gate
                                self.fail("inherited pipe operation finished before ready gate")
                            await asyncio.sleep(0.01)
                self.assertIn(registered[0].supervision, {"job_object", "process_group"})
                with self.assertRaisesRegex(TimeoutError, "deadline exceeded"):
                    await task
                await asyncio.sleep(4.2)
                self.assertFalse(marker.exists())
                self.assertEqual(supervisor.active_count, 0)
            finally:
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await supervisor.close()

    async def test_workspace_supervisor_import_does_not_require_agent_extras(self):
        source = Path(__file__).resolve().parents[2] / "src"
        code = (
            f"import sys; sys.path.insert(0,{str(source)!r}); "
            "from doppel_agent.workspace.process_supervisor import ProcessSupervisor; "
            "from doppel_agent.owned_async import await_durable; "
            "assert callable(await_durable)"
        )
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ProcessSupervisor()
            try:
                result = await supervisor.run_binary(
                    [sys.executable, "-S", "-c", code],
                    cwd=Path(directory), run_id="binary-no-agent-extras",
                )
                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(supervisor.active_count, 0)
            finally:
                await supervisor.close()

    async def test_raw_bytes_are_lossless_and_binary_path_does_not_use_text_spools(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ProcessSupervisor()
            try:
                with patch("doppel_agent.workspace.process_supervisor.tempfile.TemporaryFile",
                           side_effect=AssertionError("binary protocol must not use unbounded text spool")):
                    result = await supervisor.run_binary(
                        [sys.executable, "-c", "import os; os.write(1,b'\\x00\\xff\\r\\n'); os.write(2,b'\\xfe\\x00')"],
                        cwd=Path(directory), run_id="binary-lossless", output_limit=32,
                    )
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(result.stdout, b"\x00\xff\r\n")
                self.assertEqual(result.stderr, b"\xfe\x00")
                self.assertEqual(supervisor.active_count, 0)
            finally:
                await supervisor.close()

    async def test_both_streams_are_drained_concurrently_to_avoid_pipe_deadlock(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ProcessSupervisor()
            try:
                result = await supervisor.run_binary(
                    [sys.executable, "-c", "import os; os.write(1,b'a'*131072); os.write(2,b'b'*131072)"],
                    cwd=Path(directory), run_id="binary-two-streams", output_limit=131072,
                )
                self.assertEqual(result.stdout, b"a" * 131072)
                self.assertEqual(result.stderr, b"b" * 131072)
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(supervisor.active_count, 0)
            finally:
                await supervisor.close()

    async def test_overflow_is_not_a_truncated_success_even_when_child_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ProcessSupervisor()
            try:
                for stream in (1, 2):
                    with self.assertRaisesRegex(ProcessOutputLimitError, "output limit exceeded"):
                        await supervisor.run_binary(
                            [sys.executable, "-c", f"import os; os.write({stream},b'x'*8193)"],
                            cwd=Path(directory), run_id=f"binary-overflow-{stream}", output_limit=8192,
                        )
                    self.assertEqual(supervisor.active_count, 0)
            finally:
                await supervisor.close()

    async def test_timeout_drains_owned_process_before_return(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ProcessSupervisor()
            try:
                with self.assertRaisesRegex(TimeoutError, "deadline exceeded"):
                    await supervisor.run_binary(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        cwd=Path(directory), run_id="binary-timeout", timeout_seconds=0.1,
                    )
                self.assertEqual(supervisor.active_count, 0)
            finally:
                await supervisor.close()

    async def test_bad_limits_and_argv_reject_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            supervisor = ProcessSupervisor()
            with patch.object(supervisor, "_start", side_effect=AssertionError("must not spawn")):
                for argv, kwargs in [([], {}), ([""], {}), (["x\0"], {}), ("git", {}),
                    (["git"], {"output_limit": True}), (["git"], {"output_limit": 0}),
                    (["git"], {"output_limit": 16 * 1024 * 1024 + 1}),
                    (["git"], {"timeout_seconds": float("nan")}),
                    (["git"], {"timeout_seconds": float("inf")}),
                    (["git"], {"timeout_seconds": False})]:
                    with self.assertRaises(ValueError):
                        await supervisor.run_binary(argv, cwd=Path(directory), run_id="invalid", **kwargs)
            self.assertEqual(supervisor.active_count, 0)

    async def test_repeated_cancel_retains_registration_until_wait_and_read_workers_join(self):
        release = threading.Event()
        wait_started, read_started, terminated = threading.Event(), threading.Event(), threading.Event()
        wait_done, read_done = threading.Event(), threading.Event()

        class Reader(io.BytesIO):
            def read(self, size=-1):
                read_started.set()
                try:
                    if not release.wait(5):
                        raise RuntimeError("fixture reader gate timed out")
                    return b""
                finally:
                    read_done.set()

        class Process:
            stdout = Reader()
            stderr = io.BytesIO()
            returncode = None

            def wait(self):
                wait_started.set()
                try:
                    if not release.wait(5):
                        raise RuntimeError("fixture wait gate timed out")
                    self.returncode = -1  # SAME fixture wait receipt, not a native exit claim.
                    return -1
                finally:
                    wait_done.set()

        class Managed:
            process = Process()
            supervision = "fixture"
            closed = False

            def terminate_tree(self):
                terminated.set()

            def close(self):
                self.closed = True

        managed, supervisor = Managed(), ProcessSupervisor()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(supervisor, "_start", return_value=managed):
                task = asyncio.create_task(supervisor.run_binary(
                    ["fixture"], cwd=Path(directory), run_id="binary-repeated-cancel",
                ))
                try:
                    async with asyncio.timeout(3):
                        while not wait_started.is_set() or not read_started.is_set():
                            await asyncio.sleep(0.01)
                    task.cancel()
                    async with asyncio.timeout(3):
                        while not terminated.is_set():
                            await asyncio.sleep(0.01)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertEqual(supervisor.active_count, 1)
                    self.assertFalse(managed.closed)
                finally:
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    await supervisor.close()
                self.assertTrue(wait_done.is_set() and read_done.is_set())
                self.assertTrue(managed.closed)
                self.assertEqual(supervisor.active_count, 0)

    async def test_job_termination_still_runs_after_parent_exited(self):
        class Process:
            def poll(self):
                return 0

        class Job:
            calls = 0

            def terminate(self):
                self.calls += 1

        job = Job()
        managed = _ManagedProcess(Process(), "job_object", job)
        managed.terminate_tree()
        self.assertEqual(job.calls, 1)

    async def test_strict_normal_eof_waits_for_job_empty_before_close_unregister_and_repeated_cancel(self):
        entered, release = threading.Event(), threading.Event()
        events = []

        class Process:
            stdout, stderr = io.BytesIO(b"ok"), io.BytesIO()
            returncode = None

            def wait(self):
                events.append("parent-exited")
                self.returncode = 0
                return 0

        class Job:
            def terminate_checked(self):
                events.append("terminate-job")

            def wait_empty(self):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("fixture job-empty gate timed out")
                events.append("job-empty")

            def close_checked(self):
                events.append("close-job")

        supervisor = ProcessSupervisor()
        managed = _ManagedProcess(Process(), "fixture_not_native", Job())
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, "_start", return_value=managed):
            task = asyncio.create_task(supervisor.run_binary(["fixture"], cwd=Path(directory), run_id="empty-gate",
                                                            require_tree_ownership=True))
            try:
                async with asyncio.timeout(3):
                    while not entered.is_set():
                        await asyncio.sleep(0.01)
                self.assertEqual(supervisor.active_count, 1)
                self.assertNotIn("close-job", events)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                self.assertFalse(task.done())
            finally:
                release.set()
                outcome = await asyncio.gather(task, return_exceptions=True)
            self.assertIsInstance(outcome[0], asyncio.CancelledError)
            self.assertLess(events.index("job-empty"), events.index("close-job"))
            self.assertFalse(supervisor.cleanup_failed)
            self.assertEqual(supervisor.active_count, 0)
            await supervisor.close()

    async def test_strict_job_empty_failure_never_returns_success_and_retains_quarantine(self):
        class Process:
            stdout, stderr = io.BytesIO(b"parent-success"), io.BytesIO()
            returncode = None

            def wait(self):
                self.returncode = 0
                return 0

        class Job:
            closed = False

            def terminate_checked(self):
                pass

            def wait_empty(self):
                raise ProcessCleanupError("fixture accounting unavailable")

            def close_checked(self):
                self.closed = True

        supervisor, job = ProcessSupervisor(), Job()
        managed = _ManagedProcess(Process(), "fixture_not_native", job)
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, "_start", return_value=managed):
            with self.assertRaises(ProcessCleanupError):
                await supervisor.run_binary(["fixture"], cwd=Path(directory), run_id="quarantine", require_tree_ownership=True)
            self.assertTrue(supervisor.cleanup_failed)
            self.assertEqual(supervisor.active_count, 1)
            self.assertFalse(job.closed)
            self.assertFalse(managed.process.stdout.closed)
            with self.assertRaisesRegex(ProcessCleanupError, "quarantine"):
                await supervisor.run_binary(["fixture"], cwd=Path(directory), run_id="must-not-start")
            with self.assertRaisesRegex(ProcessCleanupError, "quarantine"):
                await supervisor.close()
        # Fake resources only; no production reset/retry path is exposed.
        managed.process.stdout.close()
        managed.process.stderr.close()

    async def test_strict_startup_cleanup_failure_retains_unadmitted_process_identity(self):
        class Process:
            stdout, stderr = io.BytesIO(), io.BytesIO()

        managed = _ManagedProcess(Process(), "fixture_not_native", None)
        supervisor = ProcessSupervisor()
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, "_start",
                side_effect=ProcessCleanupError("fixture suspended cleanup failure", managed=managed)):
            with self.assertRaises(ProcessCleanupError):
                await supervisor.run_binary(["fixture"], cwd=Path(directory), run_id="startup-quarantine",
                                            require_tree_ownership=True)
            self.assertTrue(supervisor.cleanup_failed)
            self.assertEqual(supervisor.active_count, 1)
            self.assertIs(supervisor._active["startup-quarantine"][0], managed)
        # Fake streams only, no real child/process body was executed.
        managed.process.stdout.close()
        managed.process.stderr.close()

    async def test_supervisor_close_waits_for_original_strict_unregister_gate(self):
        entered, release = threading.Event(), threading.Event()

        class Process:
            stdout, stderr = io.BytesIO(), io.BytesIO()
            returncode = None

            def wait(self):
                self.returncode = 0
                return 0

        class Job:
            def terminate(self):
                pass

            def terminate_checked(self):
                pass

            def wait_empty(self):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("fixture close gate timed out")

            def close_checked(self):
                pass

        supervisor = ProcessSupervisor()
        managed = _ManagedProcess(Process(), "fixture_not_native", Job())
        with tempfile.TemporaryDirectory() as directory, patch.object(supervisor, "_start", return_value=managed):
            running = asyncio.create_task(supervisor.run_binary(["fixture"], cwd=Path(directory), run_id="close-gate",
                                                               require_tree_ownership=True))
            closing = None
            try:
                async with asyncio.timeout(3):
                    while not entered.is_set():
                        await asyncio.sleep(.01)
                closing = asyncio.create_task(supervisor.close())
                await asyncio.sleep(.01)
                self.assertFalse(closing.done())
                self.assertEqual(supervisor.active_count, 1)
                with self.assertRaisesRegex(RuntimeError, "closed"):
                    await supervisor.run_binary(["fixture"], cwd=Path(directory), run_id="post-close")
            finally:
                release.set()
                await asyncio.gather(running, *(tuple([closing]) if closing is not None else ()))
            self.assertEqual(supervisor.active_count, 0)
            self.assertFalse(supervisor.cleanup_failed)


if __name__ == "__main__":
    unittest.main()
