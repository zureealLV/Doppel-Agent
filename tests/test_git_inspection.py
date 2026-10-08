"""S6 pure protocol regression definitions; execute with the whole version at S9."""

import pytest

from doppel_agent.workspace import git_inspection as git


OID = "a" * 40


def index(path="src/a.py", stage=0, *, mode="100644", oid=OID):
    return f"{mode} {oid} {stage}\t{path}".encode("utf-8") + b"\0"


def tree(path="src/a.py", *, mode="100644", kind="blob", oid=OID):
    return f"{mode} {kind} {oid}\t{path}".encode("utf-8") + b"\0"


def test_nul_paths_are_literal_not_shell_quoted_and_not_agent_attribution():
    path = 'src/中文 space "quote".py'
    result = git.parse_index(index(path))
    assert result.record_count == 1 and result.excluded_count == 0
    assert result.entries == (git.GitEntry(path, "100644", OID, "blob", 0),)
    assert result.entries[0].regular_file
    assert not hasattr(result, "agent_generated")
    assert git.parse_tree(tree(path)).entries[0].path == path


def test_all_unmerged_stages_are_retained_not_flattened_to_one_file():
    result = git.parse_index(index(stage=1) + index(stage=2) + index(stage=3))
    assert [entry.stage for entry in result.entries] == [1, 2, 3]
    assert result.record_count == 3
    assert [entry.stage for entry in git.parse_index(index(stage=2)).entries] == [2]


@pytest.mark.parametrize("payload", [index() + index(), index() + index(stage=2),
    index(stage=3) + index(stage=0), index(stage=1) + index(stage=1), index(stage=4),
    index(stage="00"), index(mode="040000", stage=2)])
def test_invalid_index_stage_sets_are_not_clean(payload):
    with pytest.raises(git.GitInspectionError, match="index_stage"):
        git.parse_index(payload)


@pytest.mark.parametrize("parse,record", [(git.parse_index, index), (git.parse_tree, tree)])
def test_secret_and_static_exclusions_count_without_names(parse, record):
    result = parse(record(".env") + record(".git/config") + record("node_modules/a.js") + record())
    assert result.excluded_count == 3 and result.record_count == 4
    assert [entry.path for entry in result.entries] == ["src/a.py"]
    assert ".env" not in repr(result) and "node_modules" not in repr(result)


@pytest.mark.parametrize("path", ["../x", "/absolute", "C:/x", "a\\b", "a\tx",
    "a\nx", "a\x7fx", "a//b", "CON.txt", "a.", "a ", "~x", "a/../b"])
@pytest.mark.parametrize("parse,record", [(git.parse_index, index), (git.parse_tree, tree)])
def test_unsafe_paths_fail_without_returning_raw_path(parse, record, path):
    with pytest.raises(git.GitInspectionError) as error:
        parse(record(path))
    assert str(error.value) == "invalid_git_path"


@pytest.mark.parametrize("parse,record", [(git.parse_index, index), (git.parse_tree, tree)])
@pytest.mark.parametrize("first,second", [("a.py", "A.py"), ("Dir/a", "dir/b"),
    ("a/Sub/c", "a/sub/d")])
def test_component_case_aliases_fail_before_windows_file_lookup(parse, record, first, second):
    with pytest.raises(git.GitInspectionError, match="ambiguous_git_path"):
        parse(record(first) + record(second))


def test_duplicate_tree_paths_and_mismatched_kind_are_rejected():
    with pytest.raises(git.GitInspectionError, match="ambiguous_git_path"):
        git.parse_tree(tree() + tree())
    with pytest.raises(git.GitInspectionError, match="invalid_git_tree_kind"):
        git.parse_tree(tree(mode="160000", kind="blob"))


def test_links_gitlinks_and_sparse_directories_are_metadata_not_regular_files():
    payload = index("link", mode="120000") + index("submodule", mode="160000") + index("sparse/", mode="040000")
    entries = git.parse_index(payload).entries
    assert [entry.kind for entry in entries] == ["blob", "commit", "tree"]
    assert all(not entry.regular_file for entry in entries)
    assert entries[2].path == "sparse"


def test_sha256_is_explicit_and_zero_index_object_never_a_valid_tree_blob():
    oid = "b" * 64
    assert git.parse_index(index(oid=oid), "sha256").entries[0].object_id == oid
    assert git.parse_tree(tree(oid=oid), "sha256").object_format == "sha256"
    with pytest.raises(git.GitInspectionError, match="invalid_git_object_id"):
        git.parse_tree(tree(oid=oid))
    assert git.parse_index(index(oid="0" * 40)).entries[0].object_id == "0" * 40
    with pytest.raises(git.GitInspectionError, match="invalid_git_object_id"):
        git.parse_tree(tree(oid="0" * 40))


@pytest.mark.parametrize("oid", ["a" * 39, "A" * 40, "a" * 41, "HEAD", "--help", "a:secret"])
def test_object_identity_is_full_lowercase_hash_not_a_git_revision_expression(oid):
    with pytest.raises(git.GitInspectionError, match="invalid_git_object_id"):
        git.object_id(oid, "sha1")


@pytest.mark.parametrize("parse", [git.parse_index, git.parse_tree])
@pytest.mark.parametrize("payload", [b"partial", b"\0", b"x\0\0", b"not-a-record\0",
    b"100644 " + OID.encode() + b" 0\tbad\xff\0"])
def test_malformed_or_non_utf8_protocol_is_not_lossily_decoded(parse, payload):
    with pytest.raises(git.GitInspectionError):
        parse(payload)


def test_limits_apply_even_when_all_records_would_be_excluded(monkeypatch):
    monkeypatch.setattr(git, "MAX_PROTOCOL_RECORDS", 1)
    with pytest.raises(git.GitInspectionError, match="record_limit"):
        git.parse_index(index(".env") * 2)
    monkeypatch.setattr(git, "MAX_PROTOCOL_BYTES", 2)
    with pytest.raises(git.GitInspectionError, match="byte_limit"):
        git.parse_tree(b"xxx")


def test_empty_protocol_is_valid_empty_metadata_not_a_repo_clean_receipt():
    result = git.parse_index(b"")
    assert result.entries == () and result.record_count == 0
    assert not hasattr(result, "clean")
    with pytest.raises(git.GitInspectionError, match="unsupported_git_object_format"):
        git.parse_index(b"", "unknown")
