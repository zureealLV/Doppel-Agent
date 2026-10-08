"""S5 selected context CAS/scope definitions, unexecuted until S9."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from doppel_agent.context.manifest import ContextManifestError, ManifestReader, ManifestStore
from doppel_agent.context.notes import ProjectNoteStore
from doppel_agent.context.selection import ContextSelectionStore
from doppel_agent.persistence.database import sqlite_connection
from doppel_agent.persistence.work_orders import WorkOrderStore
from doppel_agent.tasks.work_orders import plan_from_payload


def fixture(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    picks, notes, orders, manifests = ContextSelectionStore(database), ProjectNoteStore(database), WorkOrderStore(database), ManifestStore(database)
    definition = plan_from_payload({"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "fixture"}]})
    first, second = orders.create(definition)[0]["work_order_id"], orders.create(definition)[0]["work_order_id"]

    def note(scope):
        return notes.create({"title": "fixture", "body": "explicit human constraint", "kind": "constraint", "scope_work_order_id": scope,
            "sources": [{"kind": "user", "label": "confirmed fixture"}]}, confirmed=True, idempotency_key=uuid4().hex)

    (tmp_path / "source.txt").write_text("fixture", encoding="utf-8")
    reader = ManifestReader(tmp_path)
    preview = reader.preview([{"path": "source.txt"}])
    manifest = manifests.accept(reader, [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
        budget_bytes=65536, confirmed=True, idempotency_key="selection-manifest-key")
    return picks, notes, orders, first, second, note(None), note(first), manifest


def save(picks, scope, manifest=None, notes=(), *, revision=None, key=None):
    return picks.set(scope, manifest, notes, expected_revision=picks.get(scope)["revision"] if revision is None else revision,
        idempotency_key=key or uuid4().hex)


def ref(note):
    return {"note_id": note["note_id"], "revision": note["revision"]}


def test_scope_is_project_owned_and_never_saved_differs_from_explicit_empty(tmp_path):
    picks, _, orders, first, _, project, scoped, manifest = fixture(tmp_path)
    assert picks.get()["saved"] is False and picks.get(first)["revision"] == 0
    before = orders.get(first)
    global_pick = save(picks, None, manifest["manifest_id"], [ref(project)])
    order_pick = save(picks, first, manifest["manifest_id"], [ref(project), ref(scoped)])
    reopened = ContextSelectionStore(picks.database)
    assert reopened.get() == global_pick and reopened.get(first) == order_pick
    cleared = save(picks, first)
    assert cleared["saved"] and cleared["manifest_id"] is None and cleared["notes"] == []
    assert picks.get() == global_pick and orders.get(first) == before


def test_wrong_scope_deleted_note_stale_revision_missing_manifest_fail_atomically(tmp_path):
    picks, notes, _, first, second, project, scoped, manifest = fixture(tmp_path)
    before = save(picks, first, manifest["manifest_id"], [ref(project)])
    for scope, mid, refs in ((None, None, [ref(scoped)]), (second, None, [ref(scoped)]),
                             (first, uuid4().hex, []), (first, None, [ref(project), ref(project)])):
        with pytest.raises(ContextManifestError):
            save(picks, scope, mid, refs)
        assert picks.get(first) == before
    body = {"kind": "constraint", "title": "updated", "body": "updated fixture", "scope_work_order_id": None,
            "sources": [{"kind": "user", "label": "confirmed fixture"}]}
    notes.update(project["note_id"], body, expected_revision=1, confirmed=True, idempotency_key="new-note-revision-key")
    with pytest.raises(ContextManifestError, match="selected_note_revision_changed"):
        save(picks, first, None, [ref(project)])
    # Existing persisted choice is audit, not silently replaced by latest note.
    assert picks.get(first) == before
    notes.delete(project["note_id"], expected_revision=2, confirmed=True, idempotency_key="delete-note-source-key")
    with pytest.raises(ContextManifestError, match="note_not_found"):
        save(picks, first, None, [ref(project)])


def test_exact_key_replay_and_late_old_cas_cannot_restore_discarded_context(tmp_path):
    picks, _, _, first, _, project, _, manifest = fixture(tmp_path)
    accepted = save(picks, first, manifest["manifest_id"], [ref(project)], revision=0, key="lost-context-choice-key")
    assert save(picks, first, manifest["manifest_id"], [ref(project)], revision=0, key="lost-context-choice-key") == accepted
    with pytest.raises(ContextManifestError, match="context_selection_key_reused"):
        save(picks, first, None, [], revision=0, key="lost-context-choice-key")
    current = save(picks, first, revision=1)
    with pytest.raises(ContextManifestError, match="context_selection_revision_changed"):
        save(picks, first, manifest["manifest_id"], [ref(project)], revision=0, key="lost-context-choice-key")
    assert picks.get(first) == current


def test_concurrent_context_selection_cas_has_one_winner_and_no_unselected_notes_mutation(tmp_path):
    picks, notes, _, first, _, project, _, manifest = fixture(tmp_path)
    barrier = Barrier(2)

    def write(mid):
        barrier.wait(timeout=5)
        try:
            return save(picks, first, mid, [ref(project)], revision=0)
        except ContextManifestError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, mid) for mid in (None, manifest["manifest_id"])]
        results = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(result, dict) for result in results) == 1 and picks.get(first)["revision"] == 1
    assert notes.get(project["note_id"]) == project


def test_reads_do_not_create_selection_and_bad_bool_or_extra_fields_do_not_write(tmp_path):
    picks = ContextSelectionStore(tmp_path / "runtime.sqlite3")
    assert picks.get()["notes"] == []
    for refs, revision in (([], True), ([{"note_id": uuid4().hex, "revision": True}], 0),
                           ([{"note_id": uuid4().hex, "revision": 1, "body": "untrusted"}], 0)):
        with pytest.raises(ContextManifestError):
            save(picks, None, notes=refs, revision=revision)
    with sqlite_connection(picks.database) as db:
        assert db.execute("SELECT COUNT(*) FROM context_selections").fetchone()[0] == 0


def test_migration_11_to_12_preserves_order_selection_and_immutable_context(tmp_path, monkeypatch):
    from doppel_agent.persistence import migrations

    database = tmp_path / "runtime.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {v: sql for v, sql in migrations.MIGRATIONS.items() if v < 12})
        orders, notes, manifests = WorkOrderStore(database), ProjectNoteStore(database), ManifestStore(database)
        order = orders.create(plan_from_payload({"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "fixture"}]}))[0]
        selected_order = orders.set_selection(order["work_order_id"], None, expected_revision=0, idempotency_key="migration-old-order-choice")
        old_note = notes.create({"kind": "decision", "title": "fixture", "body": "Preserve fixture.",
            "sources": [{"kind": "user", "label": "human"}]}, confirmed=True, idempotency_key="migration-old-note-key")
        (tmp_path / "source.txt").write_text("fixture", encoding="utf-8")
        reader = ManifestReader(tmp_path)
        preview = reader.preview([{"path": "source.txt"}])
        old_manifest = manifests.accept(reader, [{"path": "source.txt", "expected_file_sha256": preview["entries"][0]["file_sha256"]}],
            budget_bytes=65536, confirmed=True, idempotency_key="migration-old-manifest-key")
    picks = ContextSelectionStore(database)
    assert WorkOrderStore(database).get(order["work_order_id"]) == order
    assert WorkOrderStore(database).selection() == selected_order
    assert ProjectNoteStore(database).get(old_note["note_id"]) == old_note
    assert ManifestStore(database).get(old_manifest["manifest_id"]) == old_manifest
    assert picks.get()["saved"] is False and picks.get(order["work_order_id"])["saved"] is False
    with sqlite_connection(database) as db:
        assert db.execute("SELECT COUNT(*) FROM context_selections").fetchone()[0] == 0
