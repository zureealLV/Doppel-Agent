"""Capability eligibility is not fixture readiness or model task quality."""

from pathlib import Path
import asyncio
import json

import pytest

from bench.runtime_matrix import RuntimeMatrix


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "bench/cases/runtime/manifest.json"
CONTRACT = ROOT / "bench/cases/runtime/capabilities.json"


def test_contract_keeps_original_and_supported_denominators_separate():
    matrix = RuntimeMatrix.load(MANIFEST)
    direct = matrix.capability_document(CONTRACT, boundary="direct_factory")
    service = matrix.capability_document(CONTRACT, boundary="run_service")
    assert direct["original_run_count"] == service["original_run_count"] == 180
    assert direct["supported_run_count"] == 81
    assert service["supported_run_count"] == 126
    for report in (direct, service):
        assert report["excluded_run_count"] + report["supported_run_count"] == 180
        assert len(report["supported_run_keys"]) == report["supported_run_count"]
        assert all(item["reasons"] for item in report["excluded_runs"])
        assert report["full_matrix_ready"] is False
        assert report["task_quality_scored"] is False
    assert "nav-04:legacy:1" in direct["supported_run_keys"]
    assert "tdd-01:deep:1" not in service["supported_run_keys"]
    assert "cancel-01:graph:1" in service["supported_run_keys"]
    assert "cancel-01:graph:1" not in direct["supported_run_keys"]


def test_live_freeze_records_exact_keys_and_rejects_unsupported_or_duplicate_selection():
    from bench.live_runtime_matrix import freeze_capability_selection, select_runs

    matrix = RuntimeMatrix.load(MANIFEST)
    runs = select_runs(matrix, "canary")
    frozen = freeze_capability_selection(matrix, runs)
    assert frozen["selected_run_keys"] == [run.run_key for run in runs]
    assert frozen["selected_run_count"] == 9
    assert frozen["boundary"] == "direct_factory"
    assert frozen["original_run_count"] == 180
    assert frozen["supported_run_count"] == 81
    assert len(frozen["contract_sha256"]) == 64
    unsupported = next(run for run in matrix.expand() if run.run_key == "tdd-01:graph:1")
    with pytest.raises(ValueError, match="unsupported"):
        freeze_capability_selection(matrix, (unsupported,))
    with pytest.raises(ValueError, match="unique"):
        freeze_capability_selection(matrix, (runs[0], runs[0]))


@pytest.mark.parametrize("mutation", ["version", "manifest", "case", "runtime", "feature", "support", "reason", "grant", "requirement", "boundary"])
def test_contract_rejects_incomplete_or_stale_decisions(tmp_path, mutation):
    payload = json.loads(CONTRACT.read_text())
    features = payload["boundaries"]["run_service"]["graph"]
    if mutation == "version":
        payload["contract_version"] = "99"
    elif mutation == "manifest":
        payload["protocol_sha256"] = "stale"
    elif mutation == "case":
        del payload["cases"]["mcp-02"]
    elif mutation == "runtime":
        del payload["boundaries"]["direct_factory"]["legacy"]
    elif mutation == "feature":
        del features["cancellation"]
    elif mutation == "support":
        features["workspace_read"]["supported"] = 1
    elif mutation == "reason":
        features["workspace_read"]["reason"] = " "
    elif mutation == "grant":
        features["workspace_read"]["grants"] = ["shell_bypass"]
    elif mutation == "requirement":
        payload["cases"]["nav-01"]["requires"] = ["imagined_feature"]
    else:
        del payload["boundaries"]["run_service"]
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        RuntimeMatrix.load(MANIFEST).capability_document(path, boundary="direct_factory")


@pytest.mark.parametrize("boundary", ["direct_factory", "run_service"])
@pytest.mark.parametrize("mode", ["legacy", "graph", "deep"])
@pytest.mark.parametrize("granted", [False, True])
def test_production_factory_and_service_read_and_tool_surface(tmp_path, boundary, mode, granted):
    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.base import RunRequest
    from doppel_agent.runtime.factory import create_runtime
    from doppel_agent.runtime.service import RunService

    marker = "factory-capability-evidence-417"
    (tmp_path / "evidence.txt").write_text(marker)

    class Reader:
        names = set()
        calls = 0

        def next_turn(self, messages, tools):
            self.calls += 1
            self.names = {tool["function"]["name"] for tool in tools}
            if self.calls == 1:
                args = {"file_path" if mode == "deep" else "path": "evidence.txt"}
                return ModelTurn(tool_calls=(ToolCall("read-evidence", "read_file", args),))
            assert marker in messages[-1].content
            return ModelTurn(content=marker)

    async def probe():
        provider = Reader()
        grants = {"workspace_write": granted, "command_execute": granted}
        service = None
        if boundary == "run_service":
            service = RunService(tmp_path, provider=provider)
            runtime = await service._runtime({"mode": mode, "request": {"permissions": grants, "effort": "balanced"}})
        else:
            runtime = create_runtime(mode, tmp_path, provider, core_options={
                "allow_write": granted, "allow_command": granted,
            })
        try:
            result = await runtime.run(RunRequest("read evidence.txt"))
            assert result.status == "completed"
            assert result.answer == marker
            assert "fallback_runtime" not in result.metadata
            assert provider.calls == 2
            assert "read_file" in provider.names
            patch_expected = granted and (mode == "deep" or boundary == "run_service")
            assert ("propose_patch" in provider.names) == patch_expected
            command_expected = mode == "legacy" or (granted and mode == "graph" and boundary == "run_service")
            assert ("run_command" in provider.names) == command_expected
            assert "execute" not in provider.names
            assert not any(name.startswith("mcp__") for name in provider.names)
            assert runtime.supports_resume == (mode != "legacy" or boundary == "run_service")
            # Graph cancellation belongs to its service scheduler, never its factory.
            assert runtime.supports_cancel == (mode == "deep")
        finally:
            if mode == "deep":
                await runtime.process_supervisor.close()
            if service:
                await service.close()

    asyncio.run(probe())


def test_service_graph_command_cancel_reaps_actual_parent_and_child(tmp_path):
    import ctypes
    import os
    import sys
    from ctypes import wintypes

    if os.name != "nt":
        pytest.skip("Windows process handles prove the OS child-death contract")

    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.service import RunService

    (tmp_path / "tree_child.py").write_text(
        "import os,time; from pathlib import Path; "
        "Path('child-pid.txt').write_text(str(os.getpid())); time.sleep(30)"
    )
    (tmp_path / "tree_parent.py").write_text(
        "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'tree_child.py'])\n"
        "while not Path('child-pid.txt').exists(): time.sleep(.01)\n"
        "Path('tree-ready.tmp').write_text(json.dumps([os.getpid(),child.pid]))\n"
        "Path('tree-ready.tmp').replace('tree-ready.json')\n"
        "time.sleep(30)\n"
    )

    class CommandProvider:
        def next_turn(self, messages, tools):
            return ModelTurn(tool_calls=(ToolCall("tree-command", "run_command", {
                "argv": [sys.executable, "tree_parent.py"],
            }),))

    async def probe():
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = []
        service = RunService(tmp_path, provider=CommandProvider())
        await service.start()
        try:
            record, _ = await service.create({"mode": "graph", "prompt": "command", "permissions": {"command_execute": True}})
            run_id = record["run_id"]
            await service.scheduler.wait(run_id)
            paused = await service.get(run_id)
            assert paused["status"] == "interrupted"
            await service.resume(run_id, paused["metadata"]["interrupts"][0]["id"], {"action": "approve"})
            ready = tmp_path / "tree-ready.json"
            async with asyncio.timeout(10):
                while not ready.exists() or not service.process_supervisor.active_count:
                    await asyncio.sleep(.01)
            # Hold handles before cancellation: no PID-reuse or sentinel-only proof.
            for pid in json.loads(ready.read_text()):
                handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                assert handle, ctypes.get_last_error()
                handles.append(handle)
                assert kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: alive
            assert len(handles) == 2
            assert await service.cancel(run_id)
            with pytest.raises(asyncio.CancelledError):
                await service.scheduler.wait(run_id)
            assert (await service.get(run_id))["status"] == "cancelled"
            assert sum(e["type"] == "run.cancelled" for e in await service.list_events(run_id)) == 1
            assert service.process_supervisor.active_count == 0
            assert all(kernel.WaitForSingleObject(handle, 2000) == 0 for handle in handles)
        finally:
            await service.close()
            for handle in handles:
                kernel.CloseHandle(handle)

    asyncio.run(probe())


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_service_injects_real_local_mcp_gateway_only_with_permission(tmp_path, mode):
    import sys

    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.service import RunService

    config = tmp_path / ".doppel"
    config.mkdir()
    (config / "mcp.json").write_text(json.dumps({"servers": {"demo": {
        "transport": "stdio", "command": sys.executable,
        "args": [str(ROOT / "tests/fixtures/mcp_echo.py")],
    }}}))

    class MCPProvider:
        calls = 0

        def next_turn(self, messages, tools):
            self.calls += 1
            assert "mcp__demo__echo" in {tool["function"]["name"] for tool in tools}
            if self.calls == 1:
                return ModelTurn(tool_calls=(ToolCall("mcp-probe", "mcp__demo__echo", {"text": "gateway-evidence"}),))
            assert "gateway-evidence" in messages[-1].content
            return ModelTurn(content="gateway verified")

    async def probe():
        provider = MCPProvider()
        service = RunService(tmp_path, provider=provider)
        await service.start()
        try:
            ungranted = await service._runtime({"mode": mode, "request": {"permissions": {}}})
            if mode == "graph":
                assert not any(name.startswith("mcp__") for name in ungranted.tools.tools)
            else:
                assert ungranted.additional_tools == []
                await ungranted.process_supervisor.close()
            record, _ = await service.create({"mode": mode, "prompt": "echo", "permissions": {"mcp_execute": True}})
            run_id = record["run_id"]
            await service.scheduler.wait(run_id)
            paused = await service.get(run_id)
            assert paused["status"] == "interrupted"
            assert not any(e["type"] == "mcp.tool_executed" for e in await service.list_events(run_id))
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
            await service.resume(run_id, interrupt_id, {"action": "approve"})
            await service.scheduler.wait(run_id)
            completed = await service.get(run_id)
            assert completed["status"] == "completed", completed
            assert "fallback_runtime" not in completed["metadata"]
            calls = [e for e in await service.list_events(run_id) if e["type"] == "mcp.tool_executed"]
            assert len(calls) == 1
            assert calls[0]["payload"]["logical_name"] == "mcp__demo__echo"
            assert calls[0]["payload"]["success"] is True
        finally:
            await service.close()

    asyncio.run(probe())


def test_new_capability_context_refuses_old_or_changed_resume(tmp_path):
    from bench.live_runtime_matrix import ResultStore, freeze_capability_selection, select_runs

    matrix = RuntimeMatrix.load(MANIFEST)
    selection = freeze_capability_selection(matrix, select_runs(matrix, "canary"))
    ResultStore(tmp_path, {"capability_selection": selection})
    with pytest.raises(ValueError, match="configuration"):
        ResultStore(tmp_path, {})
    changed = {**selection, "selected_run_keys": selection["selected_run_keys"][:-1]}
    with pytest.raises(ValueError, match="configuration"):
        ResultStore(tmp_path, {"capability_selection": changed})


def test_reconstructed_direct_deep_cannot_resume_reviewed_patch_without_external_metadata(tmp_path):
    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.base import ResumeCommand, RunRequest
    from doppel_agent.runtime.factory import create_runtime

    class PatchProvider:
        calls = 0

        def next_turn(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelTurn(tool_calls=(ToolCall("patch", "propose_patch", {
                    "changes": [{"path": "target.txt", "content": "reviewed"}],
                }),))
            return ModelTurn(content="done")

    async def probe():
        provider = PatchProvider()
        (tmp_path / "target.txt").write_text("base")
        request = RunRequest("patch target.txt")
        runtime = create_runtime("deep", tmp_path, provider, core_options={"allow_write": True})
        assert (await runtime.run(request)).status == "interrupted"
        await runtime.process_supervisor.close()
        (tmp_path / "target.txt").write_text("external")
        rebuilt = create_runtime("deep", tmp_path, provider, core_options={"allow_write": True})
        try:
            with pytest.raises(ValueError, match="patch_reviewed_run_scope_unavailable"):
                await rebuilt.resume(ResumeCommand(request.run_id, request.thread_id, {"action": "approve"}))
            assert (tmp_path / "target.txt").read_text() == "external"
            report = RuntimeMatrix.load(MANIFEST).capability_document(CONTRACT, boundary="direct_factory")
            assert "approval-01:deep:1" not in report["supported_run_keys"]
        finally:
            await rebuilt.process_supervisor.close()

    asyncio.run(probe())


@pytest.mark.parametrize("mode", ["graph", "deep"])
def test_service_reconstruction_preserves_reviewed_patch_and_runs_explicit_verification_once(tmp_path, mode):
    import sys
    from uuid import uuid4

    from doppel_agent.provider import ModelTurn, ToolCall
    from doppel_agent.runtime.service import RunService

    config = tmp_path / ".doppel"
    config.mkdir()
    # A real bounded subprocess verifies both approved files and records execution.
    script = (
        "from pathlib import Path; "
        "assert Path('implementation.txt').read_text() == 'fixed'; "
        "assert Path('test.txt').read_text() == 'regression'; "
        "p=Path('verification-count.txt'); "
        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
    )
    (config / "verification.json").write_text(json.dumps({"commands": [{
        "name": "verify", "argv": [sys.executable, "-c", script], "timeout_seconds": 10,
    }]}))

    class PatchProvider:
        calls = 0
        outcome = None

        def next_turn(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ModelTurn(tool_calls=(ToolCall("patch", "propose_patch", {"changes": [
                    {"path": "implementation.txt", "content": "fixed"},
                    {"path": "test.txt", "content": "regression"},
                ]}),))
            content = messages[-1].content
            # Deep HITL prepends its human-edit notice to the JSON tool result.
            self.outcome = json.loads(content[content.rindex('{"patch_id"'):])
            return ModelTurn(content="verified")

    async def probe():
        provider = PatchProvider()
        service = RunService(tmp_path, provider=provider)
        await service.start()
        try:
            record, _ = await service.create({
                "mode": mode, "prompt": "patch", "effort": "balanced",
                "permissions": {"workspace_write": True, "command_execute": True},
            })
            run_id = record["run_id"]
            await service.scheduler.wait(run_id)
            paused = await service.get(run_id)
            assert paused["status"] == "interrupted"
            assert not (tmp_path / "implementation.txt").exists()
            assert not (tmp_path / "verification-count.txt").exists()
            interrupt_id = paused["metadata"]["interrupts"][0]["id"]
        finally:
            await service.close()
        # Actual service reconstruction, not resume on the same runtime instance.
        service = RunService(tmp_path, provider=provider)
        await service.start()
        try:
            await service.resume(run_id, interrupt_id, {"action": "approve"})
            await service.scheduler.wait(run_id)
            completed = await service.get(run_id)
            assert completed["status"] == "completed", completed
            assert "fallback_runtime" not in completed["metadata"]
            assert provider.outcome["verification"]["status"] == "not_run_separate_review_required"
            assert provider.outcome["verification"]["success"] is None
            assert not (tmp_path / "verification-count.txt").exists()
            view = await service.prepare_verification(run_id, "patch", provider.outcome["patch_id"],
                operation_id=uuid4().hex, names=["verify"], command_execute=True, workspace_write=True)
            assert view["status"] == "pending" and not (tmp_path / "verification-count.txt").exists()
            verified = await service.decide_verification(run_id, view["review_id"], view["plan"]["plan_id"],
                action="approve", command_execute=True, workspace_write=True)
            assert verified["status"] == "completed" and verified["success"] is True
            assert (tmp_path / "verification-count.txt").read_text() == "1"
            replay = await service.decide_verification(run_id, view["review_id"], view["plan"]["plan_id"],
                action="approve", command_execute=True, workspace_write=True)
            assert replay["decision_replayed"] and await service.get(run_id) == completed
            with pytest.raises(ValueError, match="waiting for an interrupt"):
                await service.resume(run_id, interrupt_id, {"action": "approve"})
            assert (tmp_path / "verification-count.txt").read_text() == "1"
        finally:
            await service.close()

    asyncio.run(probe())
