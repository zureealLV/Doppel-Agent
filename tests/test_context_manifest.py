"""S5 bounded file context definitions for S9; not executed receipts."""

import hashlib
import os
from uuid import uuid4

import pytest

from doppel_agent.context.manifest import ContextManifestError, ManifestReader, ManifestStore
from doppel_agent.persistence.database import sqlite_connection


def file(path="source.txt", **kwargs):
    return {"path": path, **kwargs}


def test_line_snapshot_hashes_raw_and_included_content_separately(tmp_path):
    raw = b"\xef\xbb\xbfalpha\r\nbeta\r\ngamma\r\n"
    (tmp_path / "source.txt").write_bytes(raw)
    result = ManifestReader(tmp_path).preview([file(start_line=2, end_line=2)])
    entry = result["entries"][0]
    assert entry["path"] == "source.txt" and entry["text"] == "beta\r\n"
    assert entry["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["content_sha256"] == hashlib.sha256(b"beta\r\n").hexdigest()
    assert entry["start_line"] == entry["end_line"] == 2 and entry["total_lines"] == 3
    assert result["total_bytes"] == 6 and result["estimated_tokens"] == 2
    assert result["estimate_method"] == "utf8_bytes_div4" and result["actual_tokens"] is None
    assert result["entries"][0]["truncation_reasons"] == []
    assert not (tmp_path / ".doppel-agent").exists()


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/secret", "a\\b", ".env", ".env.local",
    ".git/config", ".ssh/config", ".doppel-agent/runtime.sqlite3", "node_modules/a.js", "build/a.txt", "id_rsa", "private.pem"])
def test_secret_ignored_and_host_paths_are_denied_before_content_read(tmp_path, path):
    with pytest.raises(ContextManifestError):
        ManifestReader(tmp_path).preview([file(path)])


def test_deny_only_workspace_nested_ignores_never_allow_secret_override(tmp_path):
    (tmp_path / ".gitignore").write_text("cache/\n*.log\na/**/skip.txt\n!cache/ok.txt\n!.env\n", encoding="utf-8")
    for name in ("cache/ok.txt", "x.log", "a/skip.txt", "a/deep/skip.txt", "src/private/data.txt"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    (tmp_path / "src/.doppelignore").write_text("private/\n", encoding="utf-8")
    for name in ("cache/ok.txt", "x.log", "a/skip.txt", "a/deep/skip.txt", "src/private/data.txt", ".env"):
        with pytest.raises(ContextManifestError):
            ManifestReader(tmp_path).preview([file(name)])
    (tmp_path / "safe.txt").write_text("fixture", encoding="utf-8")
    assert ManifestReader(tmp_path).preview([file("safe.txt")])["entries"][0]["text"] == "fixture"


def test_unsupported_or_oversized_ignore_rules_fail_closed(tmp_path):
    (tmp_path / "source.txt").write_text("fixture", encoding="utf-8")
    for rules in ("escaped\\ name.txt\n", "x" * (64 * 1024 + 1)):
        (tmp_path / ".gitignore").write_text(rules, encoding="utf-8")
        with pytest.raises(ContextManifestError):
            ManifestReader(tmp_path).preview([file()])


def test_utf8_and_line_limits_are_visible_and_never_split_a_multibyte_character(tmp_path):
    (tmp_path / "source.txt").write_text("汉" * 20000 + "\n", encoding="utf-8")
    result = ManifestReader(tmp_path).preview([file()], budget_bytes=256)
    entry = result["entries"][0]
    assert entry["bytes"] <= 256 and "�" not in entry["text"]
    assert "manifest_byte_limit" in entry["truncation_reasons"]
    assert "entry_byte_limit" in entry["truncation_reasons"]
    (tmp_path / "source.txt").write_text("x\n" * 2100, encoding="utf-8")
    entry = ManifestReader(tmp_path).preview([file()])["entries"][0]
    assert entry["end_line"] == 2000 and "line_limit" in entry["truncation_reasons"]


def test_raw_large_binary_invalid_ranges_duplicate_and_aggregate_exhaustion_fail_closed(tmp_path):
    reader = ManifestReader(tmp_path)
    for raw in (b"x" * (1024 * 1024 + 1), b"bad\xff", b"a\0b"):
        (tmp_path / "source.txt").write_bytes(raw)
        with pytest.raises(ContextManifestError):
            reader.preview([file()])
    (tmp_path / "source.txt").write_text("one\ntwo\n", encoding="utf-8")
    for specs in ([file(start_line=0)], [file(start_line=True)], [file(end_line=3)], [file(), file()],
                  [file(start_line=2, end_line=1)], [file(unrecognized=True)]):
        with pytest.raises(ContextManifestError):
            reader.preview(specs)
    (tmp_path / "source.txt").write_text("x" * 300, encoding="utf-8")
    (tmp_path / "other.txt").write_text("fixture", encoding="utf-8")
    with pytest.raises(ContextManifestError, match="manifest_budget_exhausted"):
        reader.preview([file(), file("other.txt")], budget_bytes=256)


def test_links_and_nonregular_paths_never_become_explicit_attachments(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    hard = tmp_path / "hard.txt"
    try:
        os.link(source, hard)
    except OSError:
        pytest.skip("fixture filesystem does not support hard links")
    with pytest.raises(ContextManifestError):
        ManifestReader(tmp_path).preview([file("hard.txt")])
    (tmp_path / "directory").mkdir()
    with pytest.raises(ContextManifestError, match="context_nonregular_or_hardlink"):
        ManifestReader(tmp_path).preview([file("directory")])


def test_symlink_even_with_in_root_target_is_denied(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    try:
        (tmp_path / "link.txt").symlink_to(source)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable in fixture")
    with pytest.raises(ContextManifestError):
        ManifestReader(tmp_path).preview([file("link.txt")])


def test_preview_accept_stale_and_immutable_key_replay_without_rereading_changed_files(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("first\n", encoding="utf-8", newline="\n")
    reader = ManifestReader(tmp_path)
    preview = reader.preview([file()])
    store = ManifestStore(tmp_path / "state/runtime.sqlite3")
    with sqlite_connection(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM context_manifests").fetchone()[0] == 0
    spec = [file(expected_file_sha256=preview["entries"][0]["file_sha256"])]
    for confirmed in (False, 1, "true"):
        with pytest.raises(ContextManifestError, match="confirmation_required"):
            store.accept(reader, spec, budget_bytes=65536, confirmed=confirmed, idempotency_key="fixture-accept-key")
    accepted = store.accept(reader, spec, budget_bytes=65536, confirmed=True, idempotency_key="fixture-accept-key")
    assert ManifestStore(store.database).get(accepted["manifest_id"]) == accepted
    path.write_text("changed\n", encoding="utf-8")
    assert reader.check(accepted)[0]["status"] == "stale"
    assert store.accept(reader, spec, budget_bytes=65536, confirmed=True, idempotency_key="fixture-accept-key") == accepted
    with pytest.raises(ContextManifestError, match="stale_file"):
        store.accept(reader, spec, budget_bytes=65536, confirmed=True, idempotency_key=uuid4().hex)
    with pytest.raises(ContextManifestError, match="manifest_key_reused"):
        store.accept(reader, spec, budget_bytes=256, confirmed=True, idempotency_key="fixture-accept-key")
    assert store.get(accepted["manifest_id"])["entries"][0]["text"] == "first\n"


def test_accept_requires_preview_hash_and_missing_get_does_not_create_default(tmp_path):
    (tmp_path / "source.txt").write_text("fixture", encoding="utf-8")
    store, reader = ManifestStore(tmp_path / "state/runtime.sqlite3"), ManifestReader(tmp_path)
    with pytest.raises(ContextManifestError, match="preview_hash_required"):
        store.accept(reader, [file()], budget_bytes=65536, confirmed=True, idempotency_key="fixture-accept-key")
    assert store.get(uuid4().hex) is None


def test_new_ignore_rule_makes_snapshot_unavailable_without_rewriting_accepted_bytes(tmp_path):
    (tmp_path / "source.txt").write_text("fixture", encoding="utf-8")
    reader = ManifestReader(tmp_path)
    snapshot = reader.preview([file()])
    (tmp_path / ".gitignore").write_text("source.txt\n", encoding="utf-8")
    assert reader.check(snapshot) == [{"index": 0, "status": "unavailable", "reason": "context_path_ignored"}]
    assert snapshot["entries"][0]["text"] == "fixture"


def test_additive_context_migration_leaves_existing_selection_and_order_unchanged(tmp_path, monkeypatch):
    from doppel_agent.persistence import migrations
    from doppel_agent.persistence.work_orders import WorkOrderStore
    from doppel_agent.tasks.work_orders import plan_from_payload

    path = tmp_path / "runtime.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", {version: sql for version, sql in migrations.MIGRATIONS.items() if version < 11})
        orders = WorkOrderStore(path)
        order = orders.create(plan_from_payload({"title": "fixture", "tasks": [{"id": "one", "title": "one", "prompt": "fixture"}]}))[0]
        selection = orders.set_selection(order["work_order_id"], None, expected_revision=0, idempotency_key="old-selection-key")
    ManifestStore(path)
    reopened = WorkOrderStore(path)
    assert reopened.get(order["work_order_id"]) == order and reopened.selection() == selection
    with sqlite_connection(path) as db:
        assert db.execute("SELECT COUNT(*) FROM context_manifests").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM project_notes").fetchone()[0] == 0
