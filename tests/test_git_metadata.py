"""S6 isolated metadata definitions; fake files only, no Git invocation.

These are construction definitions, not executed acceptance receipts.
"""

import hashlib
import os
import struct
from pathlib import Path

import pytest

from doppel_agent.workspace import git_metadata as metadata
from doppel_agent.workspace.git_inspection import GitInspectionError


def index_bytes(*, object_format="sha1", version=2, entries=(), extensions=()):
    hash_size = 20 if object_format == "sha1" else 32
    raw = bytearray(b"DIRC" + struct.pack(">II", version, len(entries)))
    previous = b""
    for name in entries:
        start = len(raw)
        name = name.encode("utf-8")
        raw.extend(b"\0" * 40 + b"\x01" * hash_size + struct.pack(">H", len(name)))
        if version == 4:
            assert len(previous) < 128
            raw.extend(bytes([len(previous)]))  # replace whole previous path
            previous = name
        raw.extend(name + b"\0")
        if version != 4:
            raw.extend(b"\0" * ((-(len(raw) - start)) % 8))
    for signature, payload in extensions:
        raw.extend(signature + struct.pack(">I", len(payload)) + payload)
    return bytes(raw) + hashlib.new(object_format, raw).digest()


def repository(tmp_path, *, object_format="sha1", head=True, with_index=True):
    root = tmp_path / "project"
    root.mkdir()
    git = root / ".git"
    (git / "objects").mkdir(parents=True)
    (git / "refs" / "heads").mkdir(parents=True)
    oid = "a" * (40 if object_format == "sha1" else 64)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
    if head:
        (git / "refs" / "heads" / "main").write_text(oid + "\n", encoding="ascii")
    if with_index:
        (git / "index").write_bytes(index_bytes(object_format=object_format))
    return root, git, oid


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_private_view_has_only_reviewed_metadata_not_original_config_worktree_or_hooks(tmp_path, monkeypatch, object_format):
    root, git, oid = repository(tmp_path, object_format=object_format)
    outside = tmp_path / "credential-fixture"
    outside.write_text("fixture forbidden", encoding="utf-8")
    (git / "config").write_text(f"[include]\npath={outside}\n[core]\nfsmonitor=evil\n", encoding="utf-8")
    (git / "config.worktree").write_text("[credential]\nhelper=evil\n", encoding="ascii")
    (git / "hooks").mkdir()
    (git / "hooks" / "post-checkout").write_text("never execute", encoding="ascii")
    real_open, opened = metadata.os.open, []

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        assert Path(path) not in {outside, git / "config", git / "config.worktree"}
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", record_open)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(outside))
    monkeypatch.setenv("GIT_COMMON_DIR", str(git))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "evil")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "include.path")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(outside))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SSH_ASKPASS", "evil")
    with metadata.GitMetadataReader(root).view() as view:
        private = view.git_dir.parent
        assert view.head_id == oid and view.head_ref == "refs/heads/main"
        assert view.object_format == object_format and view.plumbing_format == object_format
        assert view.git_dir != git and view.empty_worktree != root
        assert view.objects == git / "objects"
        assert (view.git_dir / "index").read_bytes() == (git / "index").read_bytes()
        assert (view.git_dir / "HEAD").read_text(encoding="ascii").strip() == oid
        config = (view.git_dir / "config").read_text(encoding="utf-8")
        assert "include" not in config and "evil" not in config and str(outside) not in config
        env = view.environment()
        assert env["GIT_DIR"] == str(view.git_dir) and env["GIT_WORK_TREE"] == str(view.empty_worktree)
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull and env["GIT_CONFIG_SYSTEM"] == os.devnull
        assert env["GIT_CONFIG_COUNT"] == "0" and env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_NO_LAZY_FETCH"] == "1"
        for name in ("GIT_COMMON_DIR", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_EXTERNAL_DIFF",
                     "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "SSH_ASKPASS", "PATH"):
            assert name not in env
        assert env["HOME"] == str(private / "home")
        assert "protocol.allow=never" in view.fixed_options()
        assert f"core.hooksPath={private / 'hooks'}" in view.fixed_options()
        view.guard.check_unchanged()
    assert not private.exists()
    assert git / "HEAD" in opened and git / "config" not in opened


def test_unborn_without_index_retains_unobserved_format_not_asserted_clean_sha1(tmp_path):
    root, git, _ = repository(tmp_path, head=False, with_index=False)
    with metadata.GitMetadataReader(root).view() as view:
        assert view.head_id is None and view.head_ref == "refs/heads/main"
        assert view.object_format is None and view.index_sha256 is None
        assert view.plumbing_format == "sha1"  # only private empty-view choice
        assert not (view.git_dir / "index").exists()
        assert not hasattr(view, "clean")


def test_unborn_sha256_index_infers_format_without_opening_repository_config(tmp_path):
    root, _, _ = repository(tmp_path, head=False, object_format="sha256")
    with metadata.GitMetadataReader(root).view() as view:
        assert view.head_id is None and view.object_format == "sha256"
        assert "objectFormat = sha256" in (view.git_dir / "config").read_text(encoding="utf-8")


def test_packed_ref_resolution_and_peeled_tag_are_bounded_metadata(tmp_path):
    root, git, oid = repository(tmp_path, head=False)
    (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{oid} refs/heads/main\n{oid} refs/tags/v1\n^{oid}\n", encoding="ascii")
    with metadata.GitMetadataReader(root).view() as view:
        assert view.head_id == oid
        assert not (view.git_dir / "packed-refs").exists()


@pytest.mark.parametrize("head", ["ref: ../outside\n", "ref: refs/heads/a..b\n", "ref: refs/heads/.secret\n",
    "ref: refs/heads/a.lock\n", "HEAD\n", "ref: refs/heads/main\nextra\n", "a" * 40 + "\x00"])
def test_head_never_accepts_host_paths_or_revision_expressions(tmp_path, head):
    root, git, _ = repository(tmp_path)
    (git / "HEAD").write_text(head, encoding="ascii")
    with pytest.raises(GitInspectionError):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("malformed HEAD must not yield a view")


def test_ref_cycle_and_mixed_head_index_formats_are_not_empty_repositories(tmp_path):
    root, git, _ = repository(tmp_path)
    (git / "refs" / "heads" / "main").write_text("ref: refs/heads/main\n", encoding="ascii")
    with pytest.raises(GitInspectionError, match="git_ref_cycle"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("cycle accepted")
    (git / "refs" / "heads" / "main").write_text("a" * 64 + "\n", encoding="ascii")
    with pytest.raises(GitInspectionError, match="git_object_format_mismatch"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("mixed format accepted")


def test_missing_reftable_stack_is_not_false_unborn(tmp_path):
    root, git, _ = repository(tmp_path, head=False)
    (git / "reftable").mkdir()
    with pytest.raises(GitInspectionError, match="git_reftable_stack_unavailable"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("reftable misclassified unborn")


@pytest.mark.parametrize("name", ["../outside.ref", "nested/table.ref", "table.ref\x00", "table.exe"])
def test_reftable_stack_names_reject_before_table_open(tmp_path, monkeypatch, name):
    root, git, _ = repository(tmp_path)
    tables = git / "reftable"
    tables.mkdir()
    (tables / "tables.list").write_bytes((name + "\n").encode("ascii"))
    real_open, opened = metadata.os.open, []

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", record_open)
    with pytest.raises(GitInspectionError, match="invalid_git_reftable_name"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("untrusted table name accepted")
    assert opened == [tables / "tables.list"]


def linked_repository(tmp_path):
    root, common, oid = repository(tmp_path)
    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    worktree = common / "worktrees" / "owned-checkout"
    worktree.mkdir(parents=True)
    (linked_root / ".git").write_text(f"gitdir: {worktree.as_posix()}\n", encoding="utf-8")
    (worktree / "commondir").write_text("../..\n", encoding="ascii")
    (worktree / "gitdir").write_text((linked_root / ".git").as_posix() + "\n", encoding="utf-8")
    (worktree / "HEAD").write_text("ref: refs/heads/main\n", encoding="ascii")
    (worktree / "index").write_bytes(index_bytes())
    return root, common, linked_root, worktree, oid


def test_linked_reftable_copies_common_and_local_stacks_under_private_common(tmp_path):
    _, common, root, worktree, _ = linked_repository(tmp_path)
    header = b"REFT" + bytes([2]) + b"\0" * 3 + struct.pack(">QQ", 1, 1) + b"sha1"
    for source in (common, worktree):
        tables = source / "reftable"
        tables.mkdir()
        (tables / "tables.list").write_bytes(b"0x1-0x1-FIXTURE.ref\n")
        (tables / "0x1-0x1-FIXTURE.ref").write_bytes(header)
        (source / "HEAD").write_bytes(b"ref: refs/heads/.invalid\n")
    with metadata.GitMetadataReader(root, authorized_metadata_roots=(common,)).view() as view:
        private = view.private_root
        assert private is not None and view.git_dir == private / "git" / "worktrees" / "current"
        assert (view.git_dir / "commondir").read_bytes() == b"../..\n"
        assert (private / "git" / "reftable" / "0x1-0x1-FIXTURE.ref").read_bytes() == header
        assert (view.git_dir / "reftable" / "0x1-0x1-FIXTURE.ref").read_bytes() == header
        assert "refStorage = reftable" in (private / "git" / "config").read_text(encoding="utf-8")
        assert view.head_id is None and view.head_ref is None and view.refs_sha256
        (worktree / "reftable" / "tables.list").write_bytes(b"")
        with pytest.raises(GitInspectionError, match="git_metadata_changed"):
            view.guard.check_unchanged()
    assert not private.exists()


def test_external_worktree_requires_owner_binding_before_external_metadata_open(tmp_path, monkeypatch):
    _, common, root, _, oid = linked_repository(tmp_path)
    real_open, opened = metadata.os.open, []

    def record_open(path, *args, **kwargs):
        opened.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", record_open)
    with pytest.raises(GitInspectionError, match="git_metadata_authorization_required"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("external metadata implicitly authorized")
    assert opened == [root / ".git"]
    with metadata.GitMetadataReader(root, authorized_metadata_roots=(common,)).view() as view:
        assert view.linked_worktree and view.head_id == oid
        assert view.objects == common / "objects" and view.git_dir != common


def test_authorized_unicode_space_worktree_identifier_is_not_artificially_restricted_to_ascii(tmp_path):
    _, common, root, worktree, oid = linked_repository(tmp_path)
    renamed = worktree.with_name("中文 Worktree")
    worktree.rename(renamed)
    (root / ".git").write_text(f"gitdir: {renamed.as_posix()}\n", encoding="utf-8")
    with metadata.GitMetadataReader(root, authorized_metadata_roots=(common,)).view() as view:
        assert view.linked_worktree and view.head_id == oid


def test_owner_bound_bare_common_metadata_directory_does_not_require_dot_git_basename(tmp_path):
    _, common, root, worktree, oid = linked_repository(tmp_path)
    bare = common.with_name("bare-repository.git")
    common.rename(bare)
    (root / ".git").write_text(f"gitdir: {(bare / 'worktrees' / worktree.name).as_posix()}\n", encoding="utf-8")
    with metadata.GitMetadataReader(root, authorized_metadata_roots=(bare,)).view() as view:
        assert view.linked_worktree and view.head_id == oid
        assert view.objects == bare / "objects"


@pytest.mark.parametrize("target,contents", [("commondir", "../../../external\n"), ("gitdir", "/wrong/backpointer\n")])
def test_owner_authorization_does_not_authorize_different_common_or_checkout_pointer(tmp_path, target, contents):
    _, common, root, worktree, _ = linked_repository(tmp_path)
    (worktree / target).write_text(contents, encoding="ascii")
    with pytest.raises(GitInspectionError):
        with metadata.GitMetadataReader(root, authorized_metadata_roots=(common,)).view():
            pytest.fail("different ownership accepted")


@pytest.mark.parametrize("name", ["alternates", "http-alternates"])
def test_object_alternates_are_rejected_without_reading_pointed_content(tmp_path, monkeypatch, name):
    root, git, _ = repository(tmp_path)
    (git / "objects" / "info").mkdir()
    alternate = git / "objects" / "info" / name
    alternate.write_text("/forbidden/external\n", encoding="ascii")
    real_open = metadata.os.open

    def refuse_alternate(path, *args, **kwargs):
        assert Path(path) != alternate
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", refuse_alternate)
    with pytest.raises(GitInspectionError, match="git_object_alternates_blocked"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("alternate accepted")


def test_metadata_hardlinks_rejected_before_open(tmp_path, monkeypatch):
    root, git, _ = repository(tmp_path)
    extra = tmp_path / "head-alias"
    try:
        os.link(git / "HEAD", extra)
    except OSError:
        pytest.skip("fixture filesystem cannot create a hardlink")
    real_open = metadata.os.open

    def refuse_head(path, *args, **kwargs):
        assert Path(path) != git / "HEAD"
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(metadata.os, "open", refuse_head)
    with pytest.raises(GitInspectionError, match="nonregular_or_hardlink"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("hardlink accepted")


def test_object_store_symlink_rejected_before_follow(tmp_path):
    root, git, _ = repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (git / "objects" / "alias").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("fixture cannot create symlink; Windows junction gate also required at S9")
    with pytest.raises(GitInspectionError, match="git_metadata_link_blocked"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("object symlink accepted")


def test_original_metadata_mutation_invalidates_view_and_temporary_view_is_cleaned(tmp_path):
    root, git, _ = repository(tmp_path)
    with metadata.GitMetadataReader(root).view() as view:
        private = view.git_dir.parent
        (git / "HEAD").write_text("b" * 40 + "\n", encoding="ascii")
        with pytest.raises(GitInspectionError, match="git_metadata_changed"):
            view.guard.check_unchanged()
    assert not private.exists()


def test_private_view_cleanup_on_owner_operation_failure(tmp_path):
    root, _, _ = repository(tmp_path)
    private = None
    with pytest.raises(RuntimeError, match="fixture owner failure"):
        with metadata.GitMetadataReader(root).view() as view:
            private = view.git_dir.parent
            raise RuntimeError("fixture owner failure")
    assert private is not None and not private.exists()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("version", [2, 3, 4])
def test_index_versions_and_checksum_format_with_literal_link_substring(object_format, version):
    raw = index_bytes(object_format=object_format, version=version, entries=("src/link-name", "src/other"),
                      extensions=((b"TEST", b"link-not-a-real-extension"),))
    result = metadata.inspect_index(raw)
    assert result.object_format == object_format and result.shared_index is None and not result.sparse


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_split_index_base_is_checksum_bound_copied_and_not_found_by_path_substring(tmp_path, object_format):
    root, git, _ = repository(tmp_path, object_format=object_format)
    shared = index_bytes(object_format=object_format, entries=("src/a",))
    base = metadata.inspect_index(shared).checksum
    (git / f"sharedindex.{base}").write_bytes(shared)
    (git / "index").write_bytes(index_bytes(object_format=object_format,
        extensions=((b"link", bytes.fromhex(base) + struct.pack(">IIQI", 0, 1, 0, 0) * 2),)))
    with metadata.GitMetadataReader(root).view() as view:
        assert (view.git_dir / f"sharedindex.{base}").read_bytes() == shared
        assert view.object_format == object_format


def test_checksum_and_mandatory_extension_fail_closed_and_sparse_extension_preserved():
    raw = index_bytes()
    with pytest.raises(GitInspectionError, match="invalid_git_index_checksum"):
        metadata.inspect_index(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(GitInspectionError, match="unsupported_git_index_extension"):
        metadata.inspect_index(index_bytes(extensions=((b"xxxx", b""),)))
    assert metadata.inspect_index(index_bytes(extensions=((b"sdir", b""),))).sparse


def test_object_entry_and_metadata_byte_limits_are_explicit_not_truncated_clean(tmp_path, monkeypatch):
    root, git, _ = repository(tmp_path)
    for name in ("one", "two"):
        (git / "objects" / name).write_bytes(b"not opened by preparation")
    monkeypatch.setattr(metadata, "MAX_OBJECT_ENTRIES", 1)
    with pytest.raises(GitInspectionError, match="git_object_entry_limit"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("unbounded objects accepted")
    monkeypatch.setattr(metadata, "MAX_METADATA_BYTES", 1)
    with pytest.raises(GitInspectionError, match="git_metadata_byte_limit"):
        with metadata.GitMetadataReader(root).view():
            pytest.fail("metadata budget ignored")
