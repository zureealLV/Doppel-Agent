"""S5 admission input definitions only; execute with the unified S9 freeze."""

from uuid import uuid4
import json

import pytest

from doppel_agent.context.input import ContextInputResolver, compose_prompt
from doppel_agent.context.manifest import ContextManifestError, ManifestReader, ManifestStore
from doppel_agent.context.notes import ProjectNoteStore
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.runs import RuntimeRunStore


def fixture(tmp_path):
    database = tmp_path / "state/runtime.sqlite3"
    runs, notes, manifests = RuntimeRunStore(database), ProjectNoteStore(database), ManifestStore(database)
    source = tmp_path / "source.txt"
    source.write_text("fixture context\n", encoding="utf-8", newline="\n")
    reader = ManifestReader(tmp_path)
    preview = reader.preview([{"path": "source.txt"}])
    manifest = manifests.accept(reader, [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
        budget_bytes=65536, confirmed=True, idempotency_key="fixture-input-manifest")
    payload = {"kind": "constraint", "title": "fixture", "body": "Preserve files.", "sources": [{"kind": "user", "label": "human"}]}
    note = notes.create(payload, confirmed=True, idempotency_key="fixture-input-note")
    spec = {"manifest_id": manifest["manifest_id"], "notes": [{"note_id": note["note_id"], "revision": 1}]}
    return runs, notes, reader, source, note, payload, spec


def request(context=None, key="fixture-input-run"):
    result = {"conversation_id": None, "prompt": "do not modify files", "mode": "graph", "permissions": {}, "idempotency_key": key}
    if context is not None:
        result["context"] = context
    return result


def accept(runs, reader, payload):
    return runs.create(uuid4().hex, uuid4().hex, "graph", payload, payload["idempotency_key"], native=True,
        input_resolver=ContextInputResolver(reader).resolve)


def test_atomic_input_snapshot_retains_raw_prompt_provenance_and_original_key_replay(tmp_path):
    runs, notes, reader, source, note, body, spec = fixture(tmp_path)
    payload = request(spec)
    record, fresh = accept(runs, reader, payload)
    assert fresh and record["request"] == payload
    snapshot = record["input_snapshot"]
    assert snapshot["manifest"]["entries"][0]["text"] == "fixture context\n"
    assert snapshot["notes"][0]["note_id"] == note["note_id"] and snapshot["notes"][0]["revision"] == 1
    assert snapshot["notes"][0]["sources"][0]["origin"] == "user_declared"
    assert snapshot["context_bytes"] == len(snapshot["rendered_context"].encode("utf-8")) and snapshot["actual_tokens"] is None
    source.write_text("changed", encoding="utf-8")
    notes.update(note["note_id"], {**body, "body": "new constraint"}, expected_revision=1, confirmed=True, idempotency_key="fixture-input-note-updated")
    replay, fresh = accept(runs, reader, payload)
    assert not fresh and replay == record  # Replay before any current file/note checks.
    assert runs.get(record["run_id"])["input_snapshot"] == snapshot
    assert compose_prompt(payload["prompt"], snapshot["rendered_context"]).count("fixture context") == 1


def test_stale_file_note_or_missing_source_rejects_before_run_conversation_or_lease(tmp_path):
    runs, notes, reader, source, note, body, spec = fixture(tmp_path)
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ContextManifestError, match="context_file_stale"):
        accept(runs, reader, request(spec))
    source.write_text("fixture context\n", encoding="utf-8", newline="\n")
    notes.update(note["note_id"], body, expected_revision=1, confirmed=True, idempotency_key="fixture-input-new-note")
    with pytest.raises(ContextManifestError, match="selected_note_revision_changed"):
        accept(runs, reader, request(spec))
    notes.delete(note["note_id"], expected_revision=2, confirmed=True, idempotency_key="fixture-input-delete-note")
    with pytest.raises(ContextManifestError, match="note_not_found"):
        accept(runs, reader, request({"manifest_id": None, "notes": [{"note_id": note["note_id"], "revision": 2}]}))
    with pytest.raises(ContextManifestError, match="context_record_not_found"):
        accept(runs, reader, request({"manifest_id": uuid4().hex, "notes": []}))
    with sqlite_connection(runs.database) as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM native_conversations").fetchone()[0] == 0


def test_absent_null_empty_and_old_request_replay_have_distinct_compatible_semantics(tmp_path):
    runs, _, reader, _, _, _, _ = fixture(tmp_path)
    payload = request()
    record, fresh = accept(runs, reader, payload)
    assert fresh and record["input_snapshot"] is None
    assert accept(runs, reader, {**payload, "context": None}) == (record, False)
    empty, _ = accept(runs, reader, request({"manifest_id": None, "notes": []}, "explicit-empty-input"))
    assert empty["input_snapshot"]["rendered_context"] == "" and empty["input_snapshot"]["context_bytes"] == 0
    assert compose_prompt("raw prompt", "") == "raw prompt"
    with pytest.raises(ValueError, match="different request"):
        accept(runs, reader, request({"manifest_id": None, "notes": []}))


def test_context_overflow_is_explicit_and_does_not_truncate_confirmed_notes(tmp_path):
    runs, notes, reader, _, _, _, _ = fixture(tmp_path)
    refs = []
    for index in range(4):
        note = notes.create({"kind": "fact", "title": str(index), "body": "界" * 16000,
            "sources": [{"kind": "user", "label": "fixture"}]}, confirmed=True, idempotency_key=f"large-input-note-{index}")
        refs.append({"note_id": note["note_id"], "revision": 1})
    with pytest.raises(ContextManifestError, match="context_input_budget_exceeded"):
        accept(runs, reader, request({"manifest_id": None, "notes": refs}))
    with sqlite_connection(runs.database) as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_runs").fetchone()[0] == 0


def test_file_note_source_alone_is_checked_and_foreign_scope_cannot_be_admitted(tmp_path):
    from doppel_agent.persistence.work_orders import WorkOrderStore
    from doppel_agent.tasks.work_orders import plan_from_payload

    runs, notes, reader, source, _, _, spec = fixture(tmp_path)
    note = notes.create({"kind": "fact", "title": "file fact", "body": "Fixture assertion.",
        "sources": [{"kind": "file", "manifest_id": spec["manifest_id"], "index": 0}]}, confirmed=True, idempotency_key="file-source-input-note")
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ContextManifestError, match="context_file_stale"):
        accept(runs, reader, request({"manifest_id": None, "notes": [{"note_id": note["note_id"], "revision": 1}]}))
    order = WorkOrderStore(runs.database).create(plan_from_payload({"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "fixture"}]}))[0]
    scoped = notes.create({"kind": "constraint", "title": "private", "body": "Only this order.", "scope_work_order_id": order["work_order_id"],
        "sources": [{"kind": "user", "label": "human"}]}, confirmed=True, idempotency_key="scoped-source-input-note")
    with pytest.raises(ContextManifestError, match="selected_note_wrong_scope"):
        accept(runs, reader, request({"manifest_id": None, "notes": [{"note_id": scoped["note_id"], "revision": 1}]}))


def test_no_caller_can_supply_frozen_input_and_composed_context_has_a_separate_bound(tmp_path):
    runs, _, _, _, _, _, spec = fixture(tmp_path)
    with pytest.raises(ValueError, match="context resolver required"):
        runs.create(uuid4().hex, uuid4().hex, "graph", request(spec), "fixture-input-run", native=True)
    with pytest.raises(ContextManifestError, match="context_input_budget_exceeded"):
        compose_prompt("raw prompt", "x" * (128 * 1024 + 1))
    assert compose_prompt("x" * 100000, "accepted data").startswith("x" * 100000)


def test_additive_input_column_keeps_old_request_bytes_and_replay_without_synthetic_context(tmp_path, monkeypatch):
    from doppel_agent.persistence import migrations

    database = tmp_path / "runtime.sqlite3"
    payload, rid = request(), uuid4().hex
    encoded = json.dumps(payload)
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {v: sql for v, sql in migrations.MIGRATIONS.items() if v < 13})
        migrations.apply_migrations(database)
        with sqlite_connection(database) as db:
            db.execute("INSERT INTO runtime_runs(run_id,thread_id,status,mode,idempotency_key,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (rid, uuid4().hex, "completed", "graph", payload["idempotency_key"], encoded, "fixture", "fixture"))
    runs = RuntimeRunStore(database)
    before = runs.get(rid)
    assert before["input_snapshot"] is None and runs.replay({**payload, "context": None}) == before
    with sqlite_connection(database) as db:
        assert db.execute("SELECT request_json FROM runtime_runs WHERE run_id=?", (rid,)).fetchone()[0] == encoded


def test_mutable_saved_pick_is_never_an_implicit_admission_input(tmp_path):
    from doppel_agent.context.selection import ContextSelectionStore

    runs, _, reader, _, _, _, spec = fixture(tmp_path)
    picks = ContextSelectionStore(runs.database)
    picks.set(None, spec["manifest_id"], spec["notes"], expected_revision=0, idempotency_key="fixture-saved-input-choice")
    plain, _ = accept(runs, reader, request())
    assert plain["input_snapshot"] is None
    explicit, _ = accept(runs, reader, request({"manifest_id": spec["manifest_id"], "notes": []}, "fixture-explicit-file-only"))
    assert explicit["input_snapshot"]["notes"] == []
    picks.set(None, None, [], expected_revision=1, idempotency_key="fixture-clear-input-choice")
    assert runs.get(explicit["run_id"])["input_snapshot"] == explicit["input_snapshot"]


def test_run_note_source_changed_or_undrained_is_rejected_not_used_as_completion_proof(tmp_path):
    runs, notes, reader, _, _, _, _ = fixture(tmp_path)
    rid = uuid4().hex
    runs.create(rid, uuid4().hex, "graph", request(key="fixture-source-run"), "fixture-source-run")
    runs.update(rid, "failed", answer="fixture failure")
    note = notes.create({"kind": "fact", "title": "failure record", "body": "The fixture failed.",
        "sources": [{"kind": "run", "run_id": rid}]}, confirmed=True, idempotency_key="fixture-run-source-note")
    spec = {"manifest_id": None, "notes": [{"note_id": note["note_id"], "revision": 1}]}
    record, _ = accept(runs, reader, request(spec))
    assert record["input_snapshot"]["notes"][0]["sources"][0]["status"] == "failed"
    runs.update(rid, "failed", answer="different failure")
    with pytest.raises(ContextManifestError, match="context_note_source_changed"):
        accept(runs, reader, request(spec, "fixture-changed-run-source"))
    with sqlite_connection(runs.database) as db:
        db.execute("UPDATE runtime_runs SET lease_active=1 WHERE run_id=?", (rid,))
    with pytest.raises(ContextManifestError, match="note_run_source_not_drained"):
        accept(runs, reader, request(spec, "fixture-undrained-run-source"))
