"""S6 B1 isolated source definitions; no actual user data/provider/Git.

These fixtures execute only at S9; construction does not claim them passed.
"""

import json
import os
from dataclasses import replace

import pytest

from doppel_agent.persistence.tool_ledger import ToolExecutionLedger
from doppel_agent.tools import patch_tool
from doppel_agent.workspace import patching
from doppel_agent.workspace.patch_receipts import PatchReceipt
from doppel_agent.workspace.patching import PatchConflictError, PatchService


def fixture(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    (root / "file.py").write_bytes(b"old\r\n")
    return root, PatchService(root), ToolExecutionLedger(tmp_path / "ledger.sqlite3")


def test_durable_intent_precedes_replace_and_seal_has_no_preimage_in_result(tmp_path, monkeypatch):
    root, service, ledger = fixture(tmp_path)
    proposal = service.prepare([{"path": "file.py", "content": "new\n"}])
    real_replace, observations = patching.os.replace, []

    def guarded_replace(source, target):
        rows = ledger.list_patch_receipts("run")
        assert rows[0]["operation_status"] == "running"
        assert rows[0]["receipt"]["status"] == "prepared" and not rows[0]["confirmed_applied"]
        assert (root / "file.py").read_bytes() == b"old\r\n"
        observations.append(rows)
        return real_replace(source, target)

    monkeypatch.setattr(patching.os, "replace", guarded_replace)
    arguments = {"_doppel_patch": proposal.as_dict()}
    result, replayed = ledger.execute_patch_once("run", "call", arguments, service, proposal)
    assert not replayed and len(observations) == 1
    assert "before_content" not in json.loads(result)["patch_receipt"]["files"][0]
    receipt = ledger.read_applied_patch("run", "call")
    assert receipt.files[0].before_content == "old\r\n"
    rows = ledger.list_patch_receipts("run")
    assert rows[0]["confirmed_applied"] and "before_content" not in rows[0]["receipt"]["files"][0]
    assert ledger.list_patch_receipts("foreign-run") == []
    monkeypatch.setattr(patching.os, "replace", real_replace)
    (root / "file.py").write_bytes(b"later user\n")
    assert ledger.execute_patch_once("run", "call", arguments, service, proposal) == (result, True)
    assert (root / "file.py").read_bytes() == b"later user\n"
    with pytest.raises(ValueError, match="different input"):
        ledger.execute_patch_once("run", "call", {"_doppel_patch": proposal.as_dict(), "changed": True}, service, proposal)


def test_new_file_inverse_deletes_and_later_user_edit_is_never_rebased(tmp_path):
    root, service, ledger = fixture(tmp_path)
    proposal = service.prepare([{"path": "empty.txt", "content": ""}])
    assert proposal.unified_diff == ""  # existence is explicit in the receipt
    ledger.execute_patch_once("run", "create", proposal.as_dict(), service, proposal)
    receipt = ledger.read_applied_patch("run", "create")
    inverse = service.prepare_inverse(receipt)
    assert inverse.changes[0].content is None and inverse.patch_id != proposal.patch_id
    (root / "empty.txt").write_bytes(b"user owns this now")
    with pytest.raises(PatchConflictError, match="user_change_conflict"):
        service.prepare_inverse(receipt)
    with pytest.raises(PatchConflictError, match="stale patch base"):
        service.apply(inverse)
    assert (root / "empty.txt").read_bytes() == b"user owns this now"


def test_exact_inverse_restores_bytes_and_can_restore_an_inverse_deletion(tmp_path):
    root, service, ledger = fixture(tmp_path)
    original_mode = (root / "file.py").stat().st_mode & 0o777
    proposal = service.prepare([{"path": "file.py", "content": "new"}, {"path": "created", "content": "made"}])
    ledger.execute_patch_once("run", "original", proposal.as_dict(), service, proposal)
    inverse = service.prepare_inverse(ledger.read_applied_patch("run", "original"))
    assert "\\ No newline at end of file" in inverse.unified_diff
    ledger.execute_patch_once("undo", "new-approval", inverse.as_dict(), service, inverse, tool_name="inverse_patch")
    assert (root / "file.py").read_bytes() == b"old\r\n" and not (root / "created").exists()
    assert (root / "file.py").stat().st_mode & 0o777 == original_mode
    redo = service.prepare_inverse(ledger.read_applied_patch("undo", "new-approval"))
    service.apply(redo)
    assert (root / "created").read_bytes() == b"made"
    assert (root / "file.py").read_bytes() == b"new"


def test_failed_multifile_effect_preserves_concurrent_user_edit_and_is_not_inverseable(tmp_path, monkeypatch):
    root, service, ledger = fixture(tmp_path)
    (root / "second").write_bytes(b"second old")
    proposal = service.prepare([{"path": "file.py", "content": "agent"}, {"path": "second", "content": "second agent"}])
    real_replace, count = patching.os.replace, 0

    def fail_after_user_edit(source, target):
        nonlocal count
        count += 1
        if count == 2:
            (root / "file.py").write_bytes(b"concurrent user change")
            raise OSError("fixture failure with private host details")
        return real_replace(source, target)

    monkeypatch.setattr(patching.os, "replace", fail_after_user_edit)
    with pytest.raises(OSError):
        ledger.execute_patch_once("run", "partial", proposal.as_dict(), service, proposal)
    assert (root / "file.py").read_bytes() == b"concurrent user change"
    assert (root / "second").read_bytes() == b"second old"
    row = ledger.list_patch_receipts("run")[0]
    assert not row["confirmed_applied"] and row["operation_status"] == "failed"
    assert [file["outcome"] for file in row["receipt"]["files"]] == ["preserved", "unchanged"]
    with pytest.raises(ValueError, match="applied_receipt_unavailable"):
        ledger.read_applied_patch("run", "partial")
    with pytest.raises(ValueError, match="previous tool execution failed"):
        ledger.execute_patch_once("run", "partial", proposal.as_dict(), service, proposal)
    assert not list(root.glob("*.tmp"))


def test_failed_final_seal_leaves_indeterminate_intent_and_cannot_repeat_effect(tmp_path, monkeypatch):
    root, service, ledger = fixture(tmp_path)
    proposal = service.prepare([{"path": "file.py", "content": "effect"}])

    def fail_seal(*args, **kwargs):
        raise OSError("fixture persistence failure")

    monkeypatch.setattr(ledger, "_finish_patch", fail_seal)
    with pytest.raises(OSError):
        ledger.execute_patch_once("run", "uncertain", proposal.as_dict(), service, proposal)
    assert (root / "file.py").read_bytes() == b"effect"
    row = ledger.list_patch_receipts("run")[0]
    assert row["receipt"]["status"] == "prepared" and row["operation_status"] == "running"
    assert not row["confirmed_applied"]
    with pytest.raises(RuntimeError, match="indeterminate"):
        ledger.execute_patch_once("run", "uncertain", proposal.as_dict(), service, proposal)


def test_intent_write_failure_is_pre_effect(tmp_path, monkeypatch):
    root, service, ledger = fixture(tmp_path)
    proposal = service.prepare([{"path": "file.py", "content": "never written"}])

    def fail_intent(*args):
        raise OSError("fixture persistence failure")

    monkeypatch.setattr(ledger, "_patch_intent", fail_intent)
    with pytest.raises(OSError):
        ledger.execute_patch_once("run", "pre-effect", proposal.as_dict(), service, proposal)
    assert (root / "file.py").read_bytes() == b"old\r\n" and ledger.list_patch_receipts("run") == []


@pytest.mark.parametrize("path", [".env", "credentials.json", "../other", "NUL", "bad."])
def test_patch_secret_device_and_unsafe_paths_are_not_opened(tmp_path, path):
    _, service, _ = fixture(tmp_path)
    with pytest.raises((ValueError, PermissionError)):
        service.prepare([{"path": path, "content": "never"}])


def test_hardlink_and_dynamic_ignore_are_rejected_before_content(tmp_path):
    root, service, _ = fixture(tmp_path)
    os.link(root / "file.py", root / "alias")
    with pytest.raises(ValueError, match="nonregular_or_hardlink"):
        service.prepare([{"path": "alias", "content": "never"}])
    (root / ".doppelignore").write_bytes(b"ignored\n")
    (root / "ignored").write_bytes(b"forbidden fixture")
    with pytest.raises(ValueError, match="context_path_ignored"):
        service.prepare([{"path": "ignored", "content": "never"}])


def test_no_partial_write_on_stale_later_base_and_receipt_validation_is_strict(tmp_path):
    root, service, _ = fixture(tmp_path)
    (root / "second").write_bytes(b"old")
    proposal = service.prepare([{"path": "file.py", "content": "agent"}, {"path": "second", "content": "agent"}])
    (root / "second").write_bytes(b"user")
    with pytest.raises(PatchConflictError):
        service.apply(proposal)
    assert (root / "file.py").read_bytes() == b"old\r\n"
    single = service.prepare([{"path": "file.py", "content": "agent"}])
    receipt = service.apply(single).receipt
    value = receipt.as_dict()
    value["files"][0]["before_content"] = "forged"
    with pytest.raises(ValueError, match="invalid_patch_receipt"):
        PatchReceipt.from_dict(value)
    with pytest.raises(ValueError, match="requires_applied_receipt"):
        service.prepare_inverse(replace(receipt, status="prepared", files=tuple(
            replace(file, outcome="pending") for file in receipt.files
        )))


def test_case_component_aliases_binary_and_large_bases_fail_closed(tmp_path):
    root, service, _ = fixture(tmp_path)
    (root / "Sources").mkdir()
    with pytest.raises(ValueError, match="case_alias"):
        service.prepare([{"path": "sources/new.py", "content": "never"}])
    (root / "binary").write_bytes(b"a\0b")
    with pytest.raises(ValueError, match="binary"):
        service.prepare([{"path": "binary", "content": "never"}])
    (root / "large").write_bytes(b"x" * 65)
    bounded = PatchService(root, max_total_bytes=64)
    with pytest.raises(ValueError, match="base_too_large"):
        bounded.prepare([{"path": "large", "content": "never"}])


def test_later_mode_change_blocks_prepared_inverse_without_overwrite(tmp_path):
    root, service, _ = fixture(tmp_path)
    receipt = service.apply(service.prepare([{"path": "file.py", "content": "agent"}])).receipt
    inverse = service.prepare_inverse(receipt)
    mode = (root / "file.py").stat().st_mode & 0o777
    try:
        os.chmod(root / "file.py", mode & ~0o222)
        with pytest.raises(PatchConflictError):
            service.apply(inverse)
        assert (root / "file.py").read_bytes() == b"agent"
    finally:
        os.chmod(root / "file.py", mode)


def test_approval_edit_cannot_refresh_same_hash_after_user_mode_change(tmp_path):
    root, _, _ = fixture(tmp_path)
    tool = patch_tool(root)
    prepared = tool.approval_preparer({"changes": [{"path": "file.py", "content": "agent"}]})
    mode = (root / "file.py").stat().st_mode & 0o777
    try:
        os.chmod(root / "file.py", mode & ~0o222)
        with pytest.raises(ValueError, match="workspace base changed"):
            tool.approval_editor({"changes": [{"path": "file.py", "content": "edited agent"}]}, prepared)
    finally:
        os.chmod(root / "file.py", mode)


def test_legacy_rows_are_not_backfilled_as_attributed_patches(tmp_path):
    _, _, ledger = fixture(tmp_path)
    ledger.execute_once("old-run", "old-call", "propose_patch", {}, lambda: '{"patch_id":"historical"}')
    assert ledger.list_patch_receipts("old-run") == []
    with pytest.raises(ValueError, match="applied_receipt_unavailable"):
        ledger.read_applied_patch("old-run", "old-call")
