"""Confirmed-note persistence/source definitions; run only at unified S9."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from doppel_agent.context.manifest import ManifestReader, ManifestStore
from doppel_agent.context.notes import ProjectNoteError, ProjectNoteStore
from doppel_agent.persistence.runs import RuntimeRunStore
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def payload(**changes):
    return {"title": "user constraint", "body": "Preserve the existing database.", "kind": "constraint",
        "scope_work_order_id": None, "sources": [{"kind": "user", "label": "explicit user decision"}], **changes}


def create(store, body=None, **kwargs):
    return store.create(body or payload(), confirmed=True, idempotency_key=uuid4().hex, **kwargs)


def test_unconfirmed_proposals_are_not_stored_and_cannot_become_context(tmp_path):
    store = ProjectNoteStore(tmp_path / "runtime.sqlite3")
    for confirmation in (False, 1, "true", None):
        with pytest.raises(ProjectNoteError, match="confirmation_required"):
            store.create(payload(), confirmed=confirmation, idempotency_key="unconfirmed-key")
    assert store.list() == [] and store.get(uuid4().hex) is None


def test_confirmed_version_edit_delete_history_and_lost_reply_key_replay(tmp_path):
    store = ProjectNoteStore(tmp_path / "runtime.sqlite3")
    note = store.create(payload(), confirmed=True, idempotency_key="create-confirmed-key")
    assert note["revision"] == 1 and note["scope"] == "project" and not note["deleted"]
    assert ProjectNoteStore(store.database).get(note["note_id"]) == note
    assert store.create(payload(), confirmed=True, idempotency_key="create-confirmed-key") == note
    edited = payload(body="Preserve database and original user changes.")
    with pytest.raises(ProjectNoteError, match="confirmation_required"):
        store.update(note["note_id"], edited, expected_revision=1, confirmed=False, idempotency_key="unconfirmed-edit-key")
    assert store.get(note["note_id"]) == note
    revised = store.update(note["note_id"], edited, expected_revision=1, confirmed=True, idempotency_key="edit-confirmed-key")
    assert revised["revision"] == 2 and store.get(note["note_id"], revision=1)["body"] == note["body"]
    original_replay = store.create(payload(), confirmed=True, idempotency_key="create-confirmed-key")
    assert original_replay["revision"] == 1 and original_replay["current_revision"] == 2 and original_replay["body"] == note["body"]
    assert store.update(note["note_id"], edited, expected_revision=1, confirmed=True, idempotency_key="edit-confirmed-key") == revised
    with pytest.raises(ProjectNoteError, match="note_revision_changed"):
        store.update(note["note_id"], payload(), expected_revision=1, confirmed=True, idempotency_key="stale-edit-key")
    with pytest.raises(ProjectNoteError, match="confirmation_required"):
        store.delete(note["note_id"], expected_revision=2, confirmed=False, idempotency_key="delete-unconfirmed-key")
    tombstone = store.delete(note["note_id"], expected_revision=2, confirmed=True, idempotency_key="delete-confirmed-key")
    assert tombstone["deleted"] and tombstone["revision"] == 3
    assert store.get(note["note_id"]) is None and store.list() == []
    assert store.get(note["note_id"], revision=1)["current_deleted"] is True
    assert store.delete(note["note_id"], expected_revision=2, confirmed=True, idempotency_key="delete-confirmed-key") == tombstone


def test_file_sources_resolve_from_accepted_manifest_and_run_sources_require_durable_drained_result(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    (tmp_path / "source.txt").write_text("offline source", encoding="utf-8")
    reader, manifests, notes, runs = ManifestReader(tmp_path), ManifestStore(path), ProjectNoteStore(path), RuntimeRunStore(path)
    preview = reader.preview([{"path": "source.txt"}])
    manifest = manifests.accept(reader, [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
        budget_bytes=65536, confirmed=True, idempotency_key="file-source-key")
    note = create(notes, payload(sources=[{"kind": "file", "manifest_id": manifest["manifest_id"], "index": 0}]))
    assert note["sources"][0]["file_sha256"] == manifest["entries"][0]["file_sha256"]
    assert "text" not in note["sources"][0]
    (tmp_path / "source.txt").write_text("edited after confirmation", encoding="utf-8")
    assert notes.get(note["note_id"])["sources"] == note["sources"]
    run = runs.create(uuid4().hex, uuid4().hex, "graph", {"prompt": "offline fixture"}, None, native=True)[0]
    source = payload(sources=[{"kind": "run", "run_id": run["run_id"]}])
    with pytest.raises(ProjectNoteError, match="note_run_source_not_drained"):
        create(notes, source)
    runs.update(run["run_id"], "completed", answer="fixture answer")
    with pytest.raises(ProjectNoteError, match="note_run_source_not_drained"):
        create(notes, source)
    runs.release_conversation_turn(run["run_id"])
    result = create(notes, source)
    assert result["sources"][0]["status"] == "completed" and len(result["sources"][0]["answer_sha256"]) == 64


def test_invalid_sources_scope_body_and_reused_key_never_write_a_note(tmp_path):
    store = ProjectNoteStore(tmp_path / "runtime.sqlite3")
    for changes in ({"sources": []}, {"sources": [{"kind": "file", "manifest_id": uuid4().hex, "index": 0}]},
                    {"sources": [{"kind": "run", "run_id": uuid4().hex}]}, {"body": "x" * 16001}, {"body": "\ud800"},
                    {"sources": [{"kind": "user", "label": "", "hash": "untrusted"}]},
                    {"scope_work_order_id": uuid4().hex}, {"kind": "model_guess"}):
        with pytest.raises(ProjectNoteError):
            create(store, payload(**changes))
    assert store.list() == []
    store.create(payload(), confirmed=True, idempotency_key="reused-note-key")
    with pytest.raises(ProjectNoteError, match="note_key_reused"):
        store.create(payload(body="different"), confirmed=True, idempotency_key="reused-note-key")


def test_scoped_notes_list_project_plus_only_selected_work_order_with_stable_cursor(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    notes, orders = ProjectNoteStore(path), WorkOrderStore(path)
    definition = plan_from_payload({"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "fixture"}]})
    first, second = orders.create(definition)[0]["work_order_id"], orders.create(definition)[0]["work_order_id"]
    project = create(notes)
    scoped = create(notes, payload(scope_work_order_id=first))
    other = create(notes, payload(scope_work_order_id=second))
    assert [item["note_id"] for item in notes.list()] == [project["note_id"]]
    page = notes.list(scope_work_order_id=first, limit=1)
    assert page[0]["note_id"] == scoped["note_id"]
    rest = notes.list(scope_work_order_id=first, before_id=page[-1]["note_id"])
    assert [item["note_id"] for item in rest] == [project["note_id"]]
    assert other["note_id"] not in {item["note_id"] for item in notes.list(scope_work_order_id=first)}
    with pytest.raises(ProjectNoteError, match="note_scope_changed"):
        notes.update(scoped["note_id"], payload(), expected_revision=1, confirmed=True, idempotency_key="scope-change-key")


def test_concurrent_note_cas_preserves_one_revision_and_original_source_audit(tmp_path):
    store = ProjectNoteStore(tmp_path / "runtime.sqlite3")
    note, barrier = create(store), Barrier(2)

    def update(body):
        barrier.wait(timeout=5)
        try:
            return store.update(note["note_id"], payload(body=body), expected_revision=1, confirmed=True, idempotency_key=uuid4().hex)
        except ProjectNoteError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, body) for body in ("first", "second")]
        results = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(result, dict) for result in results) == 1
    assert store.get(note["note_id"])["revision"] == 2
    assert store.get(note["note_id"], revision=1)["sources"] == note["sources"]
