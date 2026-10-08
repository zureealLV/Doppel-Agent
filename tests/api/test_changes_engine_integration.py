"""D4b definitions FIRST, unexecuted until S9.

Actual Graph/Deep/reviewed RunService Legacy, HTTP routing, durable review/receipt
and lifecycle in disposable roots. Model and process supervisor are SCRIPTED:
no socket/provider/model-quality/browser/Windows Job acceptance is implied.
No direct runs.create/ledger.execute_patch_once seed or fabricated applied receipt.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from doppel_agent.api import create_app
from doppel_agent.owned_async import await_durable
from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.persistence.verification_reviews import VerificationReviewStore
from doppel_agent.provider import ModelTurn, ToolCall
from doppel_agent.workspace.patching import PatchService
from doppel_agent.workspace.verification import VerificationPipeline


MODES = ("graph", "deep", "legacy")
CALL = "actual-changes-patch"
CHANGES = {"file.py": "agent exact bytes\n", "created.py": "new exact bytes\n"}


class ChangesScript:
    model = "scripted-changes-not-model-quality"

    def next_turn(self, messages, tools):
        previous = [message for message in messages if message.role == "tool"]
        if previous:
            return ModelTurn(content=previous[-1].content)
        names = {tool["function"]["name"] for tool in tools}
        assert "propose_patch" in names and "write_file" not in names
        return ModelTurn(tool_calls=(ToolCall(CALL, "propose_patch", {
            "changes": [{"path": path, "content": content} for path, content in CHANGES.items()],
        }),))


class ScriptedSupervisor:
    cleanup_failed = False

    def __init__(self, *, exit_code=0):
        self.calls = []
        self.exit_code = exit_code
        self.active_count = 0

    async def run(self, *args, **kwargs):
        pytest.fail("reviewed command must not enter compatibility text transport")

    async def run_binary(self, argv, **kwargs):
        assert kwargs["require_tree_ownership"] is True
        self.calls.append((list(argv), kwargs))
        return SimpleNamespace(exit_code=self.exit_code, stdout=b"scripted bounded result", stderr=b"",
                               supervision="fixture_not_native")

    async def close(self):
        assert self.active_count == 0


def seed(root, *, commands=1):
    root.mkdir(parents=True, exist_ok=True)
    (root / "file.py").write_bytes(b"seed exact bytes\n")
    config = root / ".doppel" / "verification.json"
    config.parent.mkdir(exist_ok=True)
    config.write_text(json.dumps({"commands": [
        {"name": "step" + str(index), "argv": ["fixture-not-an-executable", str(index)], "timeout_seconds": 5}
        for index in range(commands)], "max_output_bytes": 4096, "stop_on_failure": True}), encoding="utf-8")
    return config


@asynccontextmanager
async def owned_client(root, supervisor=None):
    app = create_app(root, provider=ChangesScript())
    service = app.state.run_service
    fake = supervisor or ScriptedSupervisor()
    service.process_supervisor = fake
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            yield client, service, fake, app


async def response(client, verb, path, *, body=None, code=200):
    result = await client.request(verb, path, json=body) if body is not None else await client.request(verb, path)
    assert result.status_code == code, result.text
    if path.startswith("/api/v1/changes"):
        assert result.headers["cache-control"] == "no-store"
    return result.json()


async def applied_source(client, service, mode):
    accepted = await response(client, "POST", "/api/v1/runs", code=202, body={
        "prompt": "exact two-file offline Changes fixture", "mode": mode, "effort": "balanced",
        # Even an ORIGINAL command grant must not auto-launch post-patch scripts.
        "permissions": {"workspace_write": True, "command_execute": True, "mcp_execute": False, "delegate": False},
    })
    run = accepted["run_id"]
    await asyncio.wait_for(service.scheduler.wait(run), 20)
    paused = await response(client, "GET", f"/api/v1/runs/{run}")
    assert paused["status"] == "interrupted" and paused["lease_active"]
    assert (service.workspace / "file.py").read_bytes() == b"seed exact bytes\n"
    assert not (service.workspace / "created.py").exists()
    # Do not instantiate a ledger to make an interrupted pre-effect GET look
    # empty/successful. Some engines have no existing ledger until application.
    interrupt = paused["metadata"]["interrupts"][0]
    await response(client, "POST", f"/api/v1/runs/{run}/interrupts/{interrupt['id']}/resume", code=202,
                   body={"action": "approve"})
    await asyncio.wait_for(service.scheduler.wait(run), 20)
    record = await response(client, "GET", f"/api/v1/runs/{run}")
    assert record["status"] == "completed" and not record["lease_active"]
    assert record["mode"] == mode and record["metadata"].get("fallback_runtime") is None
    for path, content in CHANGES.items():
        assert (service.workspace / path).read_bytes() == content.encode()
    rows = await response(client, "GET", f"/api/v1/changes/runs/{run}/patches")
    assert len(rows) == 1 and rows[0]["tool_call_id"] == CALL and rows[0]["confirmed_applied"]
    assert rows[0]["source_run_quiescent"] and rows[0]["receipt"]["status"] == "applied"
    detail = await response(client, "GET", f"/api/v1/changes/runs/{run}/patch-evidence?tool_call_id={CALL}")
    assert detail["source"] == {"run_id": run, "tool_call_id": CALL}
    assert detail["result"]["patch_receipt"] == rows[0]["receipt"] and detail["confirmed_applied"]
    assert detail["result"]["changed_paths"] == list(CHANGES)
    assert "before_content" not in json.dumps(detail) and "before_content" not in json.dumps(rows)
    events = await response(client, "GET", f"/api/v1/runs/{run}/events")
    effects = [event for event in events if event["type"] == "patch.applied"]
    assert len(effects) == 1 and effects[0]["payload"]["tool_call_id"] == CALL
    output = json.loads(effects[0]["payload"]["result"])
    assert output["verification"]["status"] == "not_run_separate_review_required"
    assert output["verification"]["success"] is None
    return run, rows[0]["receipt"]["patch_id"], record


def prepare_body(patch, *, operation=None, names=None):
    return {"confirmed": True, "operation_id": operation or uuid4().hex, "source_tool_call_id": CALL,
            "source_patch_id": patch, "names": names, "command_execute": True, "workspace_write": True}


def decision_body(view):
    return {"confirmed": True, "plan_id": view["plan"]["plan_id"], "action": "approve",
            "command_execute": True, "workspace_write": True}


@pytest.mark.parametrize("mode", MODES)
def test_actual_engine_http_patch_inverse_and_separate_verification_keep_original_outcomes(tmp_path, mode):
    seed(tmp_path)

    async def scenario():
        async with owned_client(tmp_path) as (client, service, fake, _):
            run, patch, original = await applied_source(client, service, mode)
            assert fake.calls == [] and service._verification_reviews is None
            base = f"/api/v1/changes/runs/{run}"
            body = prepare_body(patch, names=["step0"])
            await response(client, "POST", base + "/verification-reviews", body={**body, "command_execute": False}, code=403)
            view = await response(client, "POST", base + "/verification-reviews", body=body)
            assert view["status"] == "pending" and fake.calls == []
            assert view["source"] == {"run_id": run, "tool_call_id": CALL, "patch_id": patch}
            inverse = await response(client, "POST", base + "/inverse-reviews", body={"confirmed": True,
                "operation_id": uuid4().hex, "source_tool_call_id": CALL, "source_patch_id": patch, "workspace_write": True})
            assert inverse["status"] == "pending" and fake.calls == []
            path = base + "/verification-reviews/" + view["review_id"]
            await response(client, "POST", path + "/decision", body={**decision_body(view), "workspace_write": False}, code=403)
            done = await response(client, "POST", path + "/decision", body=decision_body(view))
            assert done["status"] == "completed" and done["success"] and len(fake.calls) == 1
            assert fake.calls[0][0] == view["plan"]["commands"][0]["argv"]
            assert fake.calls[0][1]["run_id"] == done["operation_id"] != run
            assert done["steps"][0]["result"]["supervision"] == "fixture_not_native"
            replay = await response(client, "POST", path + "/decision", body=decision_body(view))
            assert replay["decision_replayed"] and len(fake.calls) == 1
            history = await response(client, "GET", base + "/verification-reviews?limit=1&tool_call_id=" + CALL + "&patch_id=" + patch)
            assert [item["review_id"] for item in history["items"]] == [view["review_id"]]
            assert "argv" not in json.dumps(history) and "stdout" not in json.dumps(history)
            inverse_path = base + "/inverse-reviews/" + inverse["review_id"]
            inverse_decision = {"confirmed": True, "patch_id": inverse["patch_id"], "action": "approve", "workspace_write": True}
            await response(client, "POST", inverse_path + "/decision", body={**inverse_decision, "workspace_write": False}, code=403)
            assert (await response(client, "POST", inverse_path + "/decision", body=inverse_decision))["status"] == "applied"
            assert (tmp_path / "file.py").read_bytes() == b"seed exact bytes\n" and not (tmp_path / "created.py").exists()
            # Both records survive, but verification is not current-workspace/undo success.
            detail = await response(client, "GET", path)
            assert detail["status"] == "completed" and detail["lifecycle"]["all_planned_attempts_sealed"]
            assert detail["target"] == "current_workspace_not_original_patch_snapshot"
            assert detail["provenance"]["patch_success"] == "not_inferred_from_verification"
            assert await response(client, "GET", f"/api/v1/runs/{run}") == original
            (tmp_path / "file.py").write_bytes(b"later user exact bytes\n")
            await response(client, "POST", inverse_path + "/decision", body=inverse_decision)
            assert (tmp_path / "file.py").read_bytes() == b"later user exact bytes\n" and len(fake.calls) == 1
            assert (await response(client, "GET", base + f"/patch-evidence?tool_call_id={CALL}"))["confirmed_applied"]

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
def test_actual_engine_reconstruction_queries_do_not_refresh_config_grants_or_other_project_scope(tmp_path, monkeypatch, mode):
    first, second = tmp_path / "first", tmp_path / "second"
    config = seed(first)
    seed(second)

    async def scenario():
        async with owned_client(first) as (client, service, fake, old_app):
            run, patch, _ = await applied_source(client, service, mode)
            base = f"/api/v1/changes/runs/{run}"
            body = prepare_body(patch)
            view = await response(client, "POST", base + "/verification-reviews", body=body)
            inverse = await response(client, "POST", base + "/inverse-reviews", body={"confirmed": True,
                "operation_id": uuid4().hex, "source_tool_call_id": CALL, "source_patch_id": patch, "workspace_write": True})
            assert fake.calls == []
        assert service.cleanup_complete and not service._owner.held
        # Old owner queries do not call start/reopen admission just to answer GET.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=old_app), base_url="http://127.0.0.1") as client:
            error = await response(client, "GET", base + "/verification-reviews/" + view["review_id"], code=503)
            assert error == {"detail": "verification_query_owner_unavailable"} and not service._owner.held
        config.write_text("now invalid config; stored review must not be recaptured", encoding="utf-8")
        (first / "file.py").write_bytes(b"later user work\n")
        async with owned_client(first) as (client, recovered, fake, _):
            event_baseline = await response(client, "GET", f"/api/v1/runs/{run}/events")

            def forbidden(*args, **kwargs):
                pytest.fail("read-only HTTP recovery instantiated stores or inspected/reprepared config/project files")

            with monkeypatch.context() as reads:
                reads.setattr(ToolExecutionLedger, "__init__", forbidden)
                reads.setattr(VerificationReviewStore, "__init__", forbidden)
                reads.setattr(VerificationPipeline, "prepare", forbidden)
                reads.setattr(PatchService, "prepare_inverse", forbidden)
                detail = await response(client, "GET", base + "/verification-reviews/" + view["review_id"])
                assert detail["plan"] == view["plan"] and detail["expires_at"] == view["expires_at"]
                assert detail["query"]["sql_read_only"] and not detail["lifecycle"]["approval_available"]
                assert detail["service"]["owner_held"] and not detail["service"]["failed_close_diagnostic"]
                stored_inverse = await response(client, "GET", base + "/inverse-reviews/" + inverse["review_id"])
                assert stored_inverse["review"] == inverse["review"]
                page = await response(client, "GET", base + "/verification-reviews?limit=1&after_id=")
                assert page["items"][0]["review_id"] == view["review_id"] and not page["has_more"]
                assert page["sql_read_only"] and page["filesystem_zero_write_guarantee"] is False
                assert page["order"] == "review_id_keyset_not_chronological"
                assert "argv" not in json.dumps(page) and "stdout" not in json.dumps(page)
                assert (await response(client, "GET", base + f"/patch-evidence?tool_call_id={CALL}"))["confirmed_applied"]
            assert fake.calls == [] and recovered.scheduler.accepted_count == 0
            assert await response(client, "GET", f"/api/v1/runs/{run}/events") == event_baseline
            assert (first / "file.py").read_bytes() == b"later user work\n"
        async with owned_client(second) as (client, other, fake, _):
            other_run, other_patch, _ = await applied_source(client, other, mode)
            assert other_run != run
            await response(client, "POST", f"/api/v1/changes/runs/{other_run}/verification-reviews", body=prepare_body(other_patch))
            await response(client, "POST", f"/api/v1/changes/runs/{other_run}/inverse-reviews", body={"confirmed": True,
                "operation_id": uuid4().hex, "source_tool_call_id": CALL, "source_patch_id": other_patch, "workspace_write": True})
            for tail in ("/patches", f"/patch-evidence?tool_call_id={CALL}", "/verification-reviews",
                         "/verification-reviews/" + view["review_id"], "/inverse-reviews/" + inverse["review_id"]):
                await response(client, "GET", base + tail, code=404)
            await response(client, "GET", f"/api/v1/changes/runs/{other_run}/verification-reviews/{view['review_id']}", code=404)
            assert fake.calls == [] and (second / "file.py").read_bytes() == CHANGES["file.py"].encode()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
def test_actual_engine_effect_then_error_reply_keeps_exact_prepare_and_consumed_decision(tmp_path, monkeypatch, mode):
    config = seed(tmp_path)

    async def scenario():
        async with owned_client(tmp_path) as (client, service, fake, _):
            run, patch, original = await applied_source(client, service, mode)
            base = f"/api/v1/changes/runs/{run}/verification-reviews"
            prepare = service.prepare_verification
            captured = []

            async def drop_prepare(*args, **kwargs):
                captured.append(await prepare(*args, **kwargs))
                raise OSError("private simulated effect-then-error reply")

            monkeypatch.setattr(service, "prepare_verification", drop_prepare)
            body = prepare_body(patch, names=["step0"])
            assert await response(client, "POST", base, body=body, code=503) == {"detail": "changes_service_unavailable"}
            assert captured[0]["status"] == "pending" and fake.calls == []
            config.write_text(config.read_text(encoding="utf-8").replace("fixture-not-an-executable", "changed-not-an-executable"), encoding="utf-8")
            monkeypatch.setattr(service, "prepare_verification", prepare)
            replay = await response(client, "POST", base, body=body)
            assert replay == captured[0]  # Exact original operation/selection/config/TTL, not a new capture.
            await response(client, "POST", base + "/" + replay["review_id"] + "/decision",
                           body={"confirmed": True, "plan_id": replay["plan"]["plan_id"], "action": "reject"})
            view = await response(client, "POST", base, body=prepare_body(patch, names=["step0"]))
            decide = service.decide_verification

            async def drop_decision(*args, **kwargs):
                captured.append(await decide(*args, **kwargs))
                raise OSError("private simulated decision acknowledgement loss")

            monkeypatch.setattr(service, "decide_verification", drop_decision)
            path = base + "/" + view["review_id"]
            assert await response(client, "POST", path + "/decision", body=decision_body(view), code=503) == {"detail": "changes_service_unavailable"}
            assert captured[-1]["status"] == "completed" and len(fake.calls) == 1
            detail = await response(client, "GET", path)
            assert detail["status"] == "completed" and detail["plan"] == view["plan"]
            assert detail["lifecycle"]["retry_available"] is False and len(fake.calls) == 1
            monkeypatch.setattr(service, "decide_verification", decide)
            # Server idempotency guard only; UI must NOT automatically resend approve.
            assert (await response(client, "POST", path + "/decision", body=decision_body(view)))["decision_replayed"]
            assert len(fake.calls) == 1 and await response(client, "GET", f"/api/v1/runs/{run}") == original

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
def test_actual_engine_http_failure_prefix_is_not_all_planned_commands_or_patch_failure(tmp_path, mode):
    seed(tmp_path, commands=2)

    async def scenario():
        async with owned_client(tmp_path, ScriptedSupervisor(exit_code=1)) as (client, service, fake, _):
            run, patch, original = await applied_source(client, service, mode)
            base = f"/api/v1/changes/runs/{run}/verification-reviews"
            view = await response(client, "POST", base, body=prepare_body(patch))
            path = base + "/" + view["review_id"]
            result = await response(client, "POST", path + "/decision", body=decision_body(view))
            assert result["status"] == "completed" and result["success"] is False and len(fake.calls) == 1
            detail = await response(client, "GET", path)
            assert detail["lifecycle"]["planned_commands"] == 2 and detail["lifecycle"]["sealed_steps"] == 1
            assert detail["lifecycle"]["remaining_commands"] == 1 and not detail["lifecycle"]["all_planned_attempts_sealed"]
            assert detail["steps"][0]["result"]["exit_code"] == 1 and detail["has_unknown_command"] is False
            assert await response(client, "GET", f"/api/v1/runs/{run}") == original
            assert (await response(client, "GET", f"/api/v1/changes/runs/{run}/patch-evidence?tool_call_id={CALL}"))["confirmed_applied"]
            assert (tmp_path / "created.py").read_bytes() == CHANGES["created.py"].encode()
            await response(client, "POST", path + "/decision", body=decision_body(view))
            assert len(fake.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("close_service", [False, True])
def test_actual_engine_original_http_operation_drain_pins_owner_and_writer_before_cancel_or_close(tmp_path, mode, close_service):
    seed(tmp_path)

    async def scenario():
        entered, stopping, drain, writer_acquired = (asyncio.Event() for _ in range(4))

        class GatedSupervisor(ScriptedSupervisor):
            async def run_binary(self, argv, **kwargs):
                assert kwargs["require_tree_ownership"] is True
                self.calls.append((list(argv), kwargs))
                self.active_count = 1
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    stopping.set()
                    await await_durable(drain.wait())
                    raise
                finally:
                    self.active_count = 0

        operation = control = writer = None
        async with owned_client(tmp_path, GatedSupervisor()) as (client, service, fake, _):
            try:
                run, patch, original = await applied_source(client, service, mode)
                base = f"/api/v1/changes/runs/{run}"
                view = await response(client, "POST", base + "/verification-reviews", body=prepare_body(patch))
                path = base + "/verification-reviews/" + view["review_id"]
                operation = asyncio.create_task(client.post(path + "/decision", json=decision_body(view)))
                await asyncio.wait_for(entered.wait(), 5)

                async def writer_waiter():
                    async with service.workspace_locks.write(tmp_path):
                        writer_acquired.set()  # No actual new mutation.

                writer = asyncio.create_task(writer_waiter())
                if close_service:
                    control = asyncio.create_task(service.close())
                else:
                    control = asyncio.create_task(response(client, "POST", path + "/cancel", body={"confirmed": True, "plan_id": view["plan"]["plan_id"]}))
                await asyncio.wait_for(stopping.wait(), 5)
                await asyncio.sleep(0)
                assert not control.done() and service._owner.held and fake.active_count == 1
                assert not writer_acquired.is_set() and len(fake.calls) == 1
                drain.set()
                await asyncio.wait_for(asyncio.gather(operation, control, writer, return_exceptions=True), 10)
                assert fake.active_count == 0 and writer_acquired.is_set()
                assert writer.exception() is None
                assert not service._verification_tasks and not service._verification_cancels
                if close_service:
                    assert control.exception() is None and service.cleanup_complete and not service._owner.held
                else:
                    assert control.exception() is None
                    cancelled = control.result()
                    assert cancelled["status"] == "cancelled" and cancelled["has_unknown_command"] and cancelled["success"] is None
                    detail = await response(client, "GET", path)
                    assert detail["lifecycle"]["outcome_unknown"] and service._owner.held
                    await response(client, "POST", path + "/decision", body=decision_body(view), code=409)
                    assert await response(client, "GET", f"/api/v1/runs/{run}") == original
                    assert (await response(client, "GET", base + f"/patch-evidence?tool_call_id={CALL}"))["confirmed_applied"]
                assert len(fake.calls) == 1  # Fake drain only, not an observed OS Job/process tree.
            finally:
                drain.set()
                tasks = [task for task in (operation, control, writer) if task is not None]
                if tasks:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        if close_service:
            async with owned_client(tmp_path) as (client, recovered, fake, _):
                detail = await response(client, "GET", path)
                assert detail["status"] == "cancelled" and detail["has_unknown_command"] and detail["success"] is None
                assert detail["lifecycle"]["outcome_unknown"] and recovered._owner.held
                await response(client, "POST", path + "/decision", body=decision_body(view), code=409)
                assert (await response(client, "GET", base + f"/patch-evidence?tool_call_id={CALL}"))["confirmed_applied"]
                assert await response(client, "GET", f"/api/v1/runs/{run}") == original and fake.calls == []

    asyncio.run(scenario())
