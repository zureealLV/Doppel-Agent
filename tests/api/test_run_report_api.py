"""S8 A HTTP definitions, UNRUN; temp original stores, not native/GUI proof."""

import json
import asyncio

import httpx

from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider
from doppel_agent.reporting.queries import RunReportQueries


PARENT = "a" * 32
BASE = f"/api/v1/reports/runs/{PARENT}"


def app_fixture(tmp_path):
    app = create_app(tmp_path, provider=MockProvider())
    service = app.state.run_service
    service.runs.create(PARENT, "PRIVATE_THREAD", "graph", {"prompt": "PRIVATE_PROMPT"}, None)
    service.runs.update(PARENT, "completed", answer="PRIVATE_ANSWER")
    service.events.append(
        PARENT,
        "PRIVATE_THREAD",
        "graph.model_finished",
        {"usage": {"input_tokens": 3, "output_tokens": 1}, "reasoning": "PRIVATE_REASONING"},
    )
    return app, service


def test_explicit_report_and_attachment_are_same_allowlist_projection_without_new_work(tmp_path, monkeypatch):
    app, service = app_fixture(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("read started/recovered/submitted execution")

        monkeypatch.setattr(service, "start", forbidden)
        monkeypatch.setattr(service.scheduler, "submit", forbidden)
        response = client.get(BASE)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert "PRIVATE_" not in response.text
        report = response.json()
        assert report["version"] == 4
        assert report["usage_basis"] == "provider_usage_only_not_compat_model_events"
        assert report["provider_usage"]["selected"]["input_tokens"] is None
        assert report["provider_usage"]["compatibility"]["input_tokens"] == 3
        assert (
            report["evidence"]["snapshot_consistency"]
            == "independent_read_transactions_not_atomic_with_event_snapshot"
        )
        assert report["evidence"]["patches"]["state"] == "unknown"  # no on-read ledger construction
        assert report["usage"]["observed"]["input_tokens"] == 3 and report["cost"]["state"] == "unknown"
        assert report["cost"]["selected_units"] == 0 and report["cost"]["currencies"] == []
        assert report["cost"]["billing_complete"] is report["cost"]["account_cap_guaranteed"] is False
        assert report["service"]["read_only"] is True and report["service"]["owner_held"] is True
        attachment = client.get(BASE + "/download")
        assert attachment.status_code == 200 and attachment.json() == report
        assert (
            attachment.headers["content-disposition"] == f'attachment; filename="doppel-report-{PARENT}.json"'
        )
        assert attachment.headers["cache-control"] == "no-store"


def test_report_reads_original_receipt_phase_without_creating_missing_ledger_or_inferring_task_success(
    tmp_path, monkeypatch
):
    app, service = app_fixture(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        missing = service.state_root / "tool-executions.sqlite3"
        assert not missing.exists()
        report = client.get(BASE).json()
        assert not missing.exists()
        assert report["evidence"]["patches"]["total"] is None
        assert report["evidence"]["current_git_ownership_proven"] is False
        assert report["evidence"]["project_acceptance_proven"] is False


def test_scope_queries_origin_and_failed_owner_never_expose_raw_details(tmp_path):
    app, _service = app_fixture(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/reports/runs/" + "b" * 32).status_code == 404
        for path in (BASE + "?format=raw", BASE + "?x=1&x=2", "/api/v1/reports/runs/PRIVATE_ID"):
            result = client.get(path)
            assert result.status_code == 422 and result.headers["cache-control"] == "no-store"
            assert "PRIVATE" not in result.text
        origin = client.get(BASE, headers={"Origin": "https://foreign.invalid"})
        assert origin.status_code == 403 and origin.headers["cache-control"] == "no-store"

    # No second lifespan/restart of the original closed service.
    async def closed_read():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            result = await client.get(BASE)
            assert result.status_code == 503 and result.headers["cache-control"] == "no-store"
            assert result.json()["detail"] == "report_evidence_unavailable"

    asyncio.run(closed_read())


def test_oversized_event_payload_is_unknown_without_decoding_or_returning_private_text(tmp_path):
    app, service = app_fixture(tmp_path)
    service.events.append(
        PARENT,
        "thread",
        "graph.model_finished",
        {"usage": {"input_tokens": 99, "output_tokens": 99}, "secret": "PRIVATE_" * 10000},
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        report = client.get(BASE).json()
        assert report["snapshot"]["omitted_usage_rows"] == 1
        assert report["usage"]["state"] == "partial" and report["usage"]["unclassified_usage_envelopes"] == 1
        assert report["usage"]["observed"]["input_tokens"] == 3 and "PRIVATE_" not in json.dumps(report)


def test_report_query_never_creates_a_missing_database_or_state_directory(tmp_path):
    root = tmp_path / "absent"
    reader = RunReportQueries(root / "runtime.sqlite3")
    try:
        reader.read(PARENT)
    except ValueError as error:
        assert str(error) == "report_evidence_unavailable"
    else:
        raise AssertionError("missing evidence did not fail closed")
    assert not root.exists()


def test_bounded_snapshot_preserves_real_total_and_never_claims_all_events_seen(tmp_path):
    from doppel_agent.persistence.database import sqlite_connection

    app, service = app_fixture(tmp_path)
    with sqlite_connection(service.runs.database) as connection:
        connection.executemany(
            "INSERT INTO runtime_events(run_id,thread_id,type,timestamp,payload_json) VALUES(?,?,?,?,?)",
            [
                (
                    PARENT,
                    "fixture",
                    "graph.model_finished",
                    "fixture",
                    '{"usage":{"input_tokens":1,"output_tokens":1}}',
                )
            ]
            * 5000,
        )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        report = client.get(BASE).json()
        assert report["snapshot"]["event_total"] == 5001 and report["snapshot"]["scanned_events"] == 5000
        assert report["snapshot"]["truncated"] is True and report["usage"]["state"] == "partial"
        assert report["usage"]["billing_complete"] is False


def test_ambiguous_json_and_decode_budget_are_omitted_not_trusted(tmp_path, monkeypatch):
    from doppel_agent.persistence.database import sqlite_connection
    from doppel_agent.reporting import queries

    app, service = app_fixture(tmp_path)
    bad = service.events.append(PARENT, "thread", "subagent.runtime", {})
    with sqlite_connection(service.runs.database) as connection:
        connection.execute(
            "UPDATE runtime_events SET payload_json=? WHERE seq=?",
            ('{"usage":{"input_tokens":999,"input_tokens":1,"output_tokens":1}}', bad["seq"]),
        )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        report = client.get(BASE).json()
        assert report["usage"]["observed"]["input_tokens"] == 3
        assert report["usage"]["unclassified_usage_envelopes"] == 1 and report["usage"]["state"] == "partial"
        monkeypatch.setattr(queries, "MAX_DECODE", 1)
        report = client.get(BASE).json()
        assert report["usage"]["observed"]["input_tokens"] is None and report["usage"]["state"] == "unknown"
        assert report["snapshot"]["omitted_usage_rows"] == 2 and report["query"]["decoded_bytes"] == 0


def test_original_v4_read_and_download_keep_unpriced_historical_receipts_unknown_without_new_execution(
    tmp_path, monkeypatch
):
    from test_provider_usage_report import transport_rows
    from doppel_agent.persistence.database import sqlite_connection

    app, service = app_fixture(tmp_path)
    with sqlite_connection(service.runs.database) as db:
        db.execute("DELETE FROM runtime_events WHERE run_id=?", (PARENT,))
    for event in transport_rows():
        service.events.append(PARENT, "PRIVATE_THREAD", event["type"], event["payload"])

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("report admitted execution/provider/recovery")

    with TestClient(app, base_url="http://127.0.0.1") as client:
        monkeypatch.setattr(service, "start", forbidden)
        monkeypatch.setattr(service.scheduler, "submit", forbidden)
        report = client.get(BASE).json()
        assert report["version"] == 4 and report["provider_usage"]["selected"]["total_tokens"] == 10
        assert report["cost"]["state"] == "unknown" and report["cost"]["unknown_units"] == 1
        assert (
            report["cost"]["unknown_reasons"]["no_frozen_tariff"] == 1 and report["cost"]["currencies"] == []
        )
        assert report["provider_usage"]["counts"]["callbacks_matched"] == 1
        assert report["provider_usage"]["compatibility"]["known_units"] == 0
        assert report["usage"]["observed"]["input_tokens"] == 7
        assert (
            report["evidence"]["snapshot_consistency"]
            == "independent_read_transactions_not_atomic_with_event_snapshot"
        )
        assert client.get(BASE + "/download").json() == report
        assert client.get(BASE + "?provider_usage=true").status_code == 422
        assert not (service.state_root / "tool-executions.sqlite3").exists()


def test_original_full4_api_prices_frozen_journal_root_children_and_unknown_other_without_lookup_or_execution(
    tmp_path, monkeypatch
):
    # FIRST definition, ALL UNRUN. Actual original temp journal/read path, not
    # native/real provider/price authority. Fictitious declared rates ONLY.
    from fractions import Fraction
    from test_billing_tariff import PROFILE
    from test_billing_tariff_recording import price, priced_rows
    from test_provider_usage_report import call_frames, row
    from doppel_agent.persistence.database import sqlite_connection

    app, service = app_fixture(tmp_path)
    with sqlite_connection(service.runs.database) as db:
        db.execute("DELETE FROM runtime_events WHERE run_id=?", (PARENT,))
        db.execute(
            "UPDATE runtime_runs SET profile_json=? WHERE run_id=?",
            (
                json.dumps(
                    {**PROFILE, "billing_price_receipt": price(), "private_reference": "PRIVATE_SOURCE"}
                ),
                PARENT,
            ),
        )
    events = priced_rows()
    for index in range(18):
        start, finish = call_frames(call_id=f"{index + 100:032x}")
        for frame in (start, finish):
            frame.update(version=3, price_receipt=price())
        child = f"{index + 1:032x}"
        entries = [
            row(len(events) + 1, "provider.call_started", start, child),
            row(len(events) + 2, "provider.call_finished", finish, child),
        ]
        events.extend(entries if index < 17 else entries[:1])
    for event in events:
        service.events.append(PARENT, "PRIVATE_THREAD", event["type"], event["payload"])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("report consulted provider/key/latest profile/runtime/admission")

    with TestClient(app, base_url="http://127.0.0.1") as client:
        monkeypatch.setattr(service, "_provider", forbidden)
        monkeypatch.setattr(service, "_runtime", forbidden)
        monkeypatch.setattr(service.scheduler, "submit", forbidden)
        report = client.get(BASE)
        assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
        value, cost = report.json(), report.json()["cost"]
        assert value["version"] == 4 and cost["state"] == "partial"
        assert cost["selected_units"] == 19 and cost["known_units"] == 18 and cost["unknown_units"] == 1
        assert cost["request_units"] == 1 and cost["logical_call_units"] == 18
        assert Fraction(cost["currencies"][0]["amount"]) == 18 * Fraction("0.00001625")
        assert cost["scopes"]["root"]["known_units"] == 1
        assert cost["scopes"]["children_total"] == 18 and cost["scopes"]["children_omitted"] == 2
        assert (
            cost["scopes"]["other_children"]["known_units"]
            == cost["scopes"]["other_children"]["unknown_units"]
            == 1
        )
        assert cost["scopes"]["other_children"]["unknown_reasons"]["source_not_eligible"] == 1
        assert cost["scopes"]["children"][0]["generation"] == 2
        assert value["provider_usage"]["samples"]["emitted"] == 16
        assert cost["billing_complete"] is cost["source_verified"] is cost["account_cap_guaranteed"] is False
        assert (
            "PRIVATE_" not in report.text
            and "source_reference" not in report.text
            and "fixture-model" not in report.text
        )
        assert client.get(BASE + "/download").json() == value
        assert client.get(BASE + "?billing=true").status_code == 422
        assert not (service.state_root / "tool-executions.sqlite3").exists()
