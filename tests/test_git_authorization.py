"""D1b S9 definitions: synthetic linked pointers, no Git/native command calls."""

from pathlib import Path

import pytest

from doppel_agent.workspace import git_metadata as metadata
from doppel_agent.workspace.git_authorization import capture_linked_metadata, check_linked_metadata, select_metadata_root
from doppel_agent.workspace.git_inspection import GitInspectionError


def linked(tmp_path):
    workspace, common = tmp_path / "project", tmp_path / "bare 中文 repo"
    workspace.mkdir()
    local = common / "worktrees" / "worktree 中文"
    local.mkdir(parents=True)
    marker = workspace / ".git"
    marker.write_text(f"gitdir: {local}\n", encoding="utf-8")
    (local / "commondir").write_text("../..\n", encoding="utf-8")
    (local / "gitdir").write_text(f"{marker}\n", encoding="utf-8")
    return workspace, common, local


def test_selection_opens_no_metadata_and_capture_reads_only_exact_pointer_binding(tmp_path, monkeypatch):
    workspace, common, local = linked(tmp_path)
    opened, original = [], metadata.os.open

    def record(path, *args, **kwargs):
        opened.append(Path(path))
        assert Path(path) in {workspace / ".git", local / "commondir", local / "gitdir"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", record)
    selected = select_metadata_root(common)
    assert not opened
    binding = capture_linked_metadata(workspace, selected)
    assert binding.common == common and binding.workspace == workspace and binding.local == local
    check_linked_metadata(binding)
    assert set(opened) == {workspace / ".git", local / "commondir", local / "gitdir"}


def test_selected_unrelated_root_does_not_follow_project_external_pointer(tmp_path, monkeypatch):
    workspace, common, local = linked(tmp_path)
    unrelated = tmp_path / "other"
    unrelated.mkdir()
    opened, original = [], metadata.os.open

    def record(path, *args, **kwargs):
        opened.append(Path(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", record)
    with pytest.raises(GitInspectionError, match="authorization_required"):
        capture_linked_metadata(workspace, select_metadata_root(unrelated))
    assert opened == [workspace / ".git"]


@pytest.mark.parametrize("change", ["gitfile", "common", "backpointer", "commondir"])
def test_binding_change_invalidates_authority_not_silently_recaptured(tmp_path, change):
    workspace, common, local = linked(tmp_path)
    binding = capture_linked_metadata(workspace, select_metadata_root(common))
    if change == "gitfile":
        (workspace / ".git").write_text(f"gitdir: {local}\r\n", encoding="utf-8")
    elif change == "common":
        common.rename(common.with_name("old root"))
        common.mkdir()
    elif change == "backpointer":
        (local / "gitdir").write_text(f"{tmp_path / 'other' / '.git'}\n", encoding="utf-8")
    else:
        (local / "commondir").write_text("../../other\n", encoding="utf-8")
    with pytest.raises(GitInspectionError):
        check_linked_metadata(binding)


def test_common_content_changes_do_not_expand_or_destroy_directory_identity_scope(tmp_path):
    workspace, common, _ = linked(tmp_path)
    binding = capture_linked_metadata(workspace, select_metadata_root(common))
    (common / "refs").mkdir()
    (common / "refs" / "fixture").write_text("metadata fixture never read", encoding="utf-8")
    check_linked_metadata(binding)


def test_selected_directory_replacement_after_native_review_is_refused(tmp_path):
    workspace, common, _ = linked(tmp_path)
    selected = select_metadata_root(common)
    common.rename(common.with_name("old root"))
    common.mkdir()
    with pytest.raises(GitInspectionError, match="authorization_changed"):
        capture_linked_metadata(workspace, selected)


def test_original_workspace_directory_identity_is_bound_before_native_confirmation(tmp_path):
    workspace, common, _ = linked(tmp_path)
    selected = select_metadata_root(common, workspace=workspace)
    workspace.rename(workspace.with_name("old project"))
    workspace.mkdir()
    with pytest.raises(GitInspectionError, match="authorization_changed"):
        capture_linked_metadata(workspace, selected)


def test_actual_metadata_entry_checks_bound_worktree_before_external_file_open(tmp_path, monkeypatch):
    workspace, common, local = linked(tmp_path)
    binding = capture_linked_metadata(workspace, select_metadata_root(common))
    reader = metadata.GitMetadataReader(workspace, authorized_metadata_roots=(common,), linked_binding=binding)
    # Simulates change after the service precheck but before actual private-view
    # discovery. A second valid worktree under the SAME common root is not granted.
    other = common / "worktrees" / "other"
    other.mkdir()
    (workspace / ".git").write_text(f"gitdir: {other}\n", encoding="utf-8")
    opened, original = [], metadata.os.open

    def record(path, *args, **kwargs):
        opened.append(Path(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", record)
    from time import monotonic

    guard = metadata._MetadataGuard((workspace, common), deadline=monotonic() + 10)
    with pytest.raises(GitInspectionError, match="authorization_changed"):
        reader._discover(guard)
    assert opened == [workspace / ".git"]


@pytest.mark.parametrize("value", ["relative", "//remote/share", "bad\x00path", "bad\npath"])
def test_selection_refuses_relative_network_or_control_paths(value):
    with pytest.raises(GitInspectionError):
        select_metadata_root(value)
