"""S6 scripted transport/owned fixture definitions, not observed Git parity."""

import asyncio
import hashlib
import os
import struct
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from doppel_agent.owned_async import await_durable
from doppel_agent.workspace import git_reader as reader
from doppel_agent.workspace.git_inspection import GitInspectionError
from doppel_agent.workspace.git_metadata import GitMetadataReader
from doppel_agent.workspace.process_supervisor import BinaryProcessResult, ProcessOutputLimitError


def blob(value, format_name="sha1"):
    return hashlib.new(format_name, b"blob " + str(len(value)).encode("ascii") + b"\0" + value).hexdigest()


def setup(tmp_path, *, head=None, index=None, working=None, format_name="sha1", unborn=False):
    root = tmp_path / "project"
    root.mkdir()
    git = root / ".git"
    (git / "objects").mkdir(parents=True)
    (git / "refs").mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n" if unborn else "f" * (40 if format_name == "sha1" else 64) + "\n", encoding="ascii", newline="\n")
    raw_index = b"DIRC" + struct.pack(">II", 2, 0)
    (git / "index").write_bytes(raw_index + hashlib.new(format_name, raw_index).digest())
    for path, content in (working or {}).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    binary = tmp_path / "bin" / ("git.exe" if os.name == "nt" else "git")
    binary.parent.mkdir()
    binary.write_bytes(b"scripted executable fixture, never executed")
    binary.chmod(0o700)
    supervisor = Scripted(head or {}, index or {}, format_name)
    inspector = reader.GitInspector(root, supervisor=supervisor, executable=binary)
    return root, inspector, supervisor


class Scripted:
    def __init__(self, head, index, format_name):
        self.head, self.index, self.format_name = head, index, format_name
        self.calls, self.contents = [], {}
        self.failure = None
        self.started, self.release, self.cleaning = None, None, None
        self.active = 0
        self.reftable = False
        for entries in (head, index):
            for _path, value in entries.items():
                mode, content, _stage = value
                self.contents[blob(content, format_name)] = content

    async def run_binary(self, argv, **kwargs):
        self.calls.append({"argv": tuple(argv), **kwargs})
        command_index = next(i for i, arg in enumerate(argv) if arg in {
            "ls-files", "ls-tree", "cat-file", "symbolic-ref", "rev-parse", "show-ref",
        })
        command = tuple(argv[command_index:])
        if self.started is not None and command[0] == "ls-files":
            self.active += 1
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cleaning.set()
                await await_durable(self.release.wait())
                raise
            finally:
                self.active -= 1
        if self.failure is not None:
            return BinaryProcessResult(tuple(argv), 128, b"", b"forbidden raw host path/key fixture", self.failure)
        payload = b""
        if command[0] in {"ls-files", "ls-tree"}:
            entries = self.index if command[0] == "ls-files" else self.head
            for path, (mode, content, stage) in entries.items():
                middle = str(stage) if command[0] == "ls-files" else ("commit" if mode == "160000" else "blob")
                fields = (mode, blob(content, self.format_name), middle) if command[0] == "ls-files" else (
                    mode, middle, blob(content, self.format_name))
                payload += f"{' '.join(fields)}\t{path}\0".encode("utf-8")
        elif command[0] == "cat-file":
            oid = command[-1]
            assert oid in self.contents, "no arbitrary/unadmitted blob request"
            payload = {"-t": b"blob\n", "-s": str(len(self.contents[oid])).encode("ascii") + b"\n",
                       "blob": self.contents[oid]}[command[1]]
        elif command[0] == "symbolic-ref":
            payload = b"refs/heads/main\n"
        elif command[0] == "rev-parse":
            payload = ("f" * (40 if self.format_name == "sha1" else 64) + "\n").encode("ascii")
        if len(payload) > kwargs["output_limit"]:
            raise ProcessOutputLimitError("fixture overflow")
        return BinaryProcessResult(tuple(argv), 0, payload, b"", "job_object" if os.name == "nt" else "process_group")


def regular(content, stage=0):
    return "100644", content, stage


def test_staged_metadata_raw_workspace_untracked_and_exclusions_never_infer_agent_or_clean(tmp_path):
    root, inspector, supervisor = setup(tmp_path,
        head={"source.py": regular(b"old\n"), ".env": regular(b"forbidden fixture")},
        index={"source.py": regular(b"index\n"), ".env": regular(b"forbidden fixture")},
        working={"source.py": b"working\n", "new.py": b"new\n", ".env": b"forbidden fixture"})
    result = asyncio.run(inspector.status())
    assert result["available"] and result["repository_clean"] is None, result
    rows = {row["path"]: row for row in result["rows"]}
    assert set(rows) == {"source.py", "new.py"}
    assert rows["source.py"]["staged"] == "modified" and rows["source.py"]["worktree"] == "different_raw"
    assert rows["new.py"]["worktree"] == "untracked"
    assert result["excluded_count"] > 0 and result["agent_attribution"] == "not_inferred_from_repository_changes"
    assert all(row["agent_generated"] is None for row in rows.values())
    assert result["comparison"] == "raw_workspace_no_filters_or_eol_conversion"
    assert all(call["cwd"] != root and call["require_tree_ownership"] for call in supervisor.calls)
    assert all(call["env"]["GIT_DIR"] != str(root / ".git") for call in supervisor.calls)
    assert all("git status" not in " ".join(call["argv"]) and "diff" not in call["argv"] for call in supervisor.calls)
    assert all(not Path(call["env"]["GIT_DIR"]).parent.exists() for call in supervisor.calls)


@pytest.mark.parametrize("plane,before", [("staged", b"old\n"), ("worktree", b"index\n"), ("combined", b"old\n")])
def test_selected_planes_bind_only_admitted_blobs_and_actual_raw_hashes(tmp_path, plane, before):
    _, inspector, supervisor = setup(tmp_path, head={"a.py": regular(b"old\n")},
        index={"a.py": regular(b"index\n")}, working={"a.py": b"working\n"})
    result = asyncio.run(inspector.diff("a.py", plane=plane))
    after = b"index\n" if plane == "staged" else b"working\n"
    assert result["available"] and result["plane"] == plane
    assert result["before_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["after_sha256"] == hashlib.sha256(after).hexdigest()
    assert "+" + after.decode().strip() in result["text"]
    assert result["agent_generated"] is None
    object_commands = [call["argv"] for call in supervisor.calls if "cat-file" in call["argv"]]
    assert object_commands and all(argv[-1] in {blob(b"old\n"), blob(b"index\n")} for argv in object_commands)
    assert all("--filters" not in argv and "--textconv" not in argv for argv in object_commands)


def test_fingerprint_rejects_later_user_edit_before_any_blob_extraction(tmp_path):
    root, inspector, supervisor = setup(tmp_path, head={"a.py": regular(b"old\n")},
        index={"a.py": regular(b"old\n")}, working={"a.py": b"old\n"})

    async def scenario():
        status = await inspector.status()
        (root / "a.py").write_bytes(b"later user edit\n")
        supervisor.calls.clear()
        return await inspector.diff("a.py", expected_fingerprint=status["fingerprint"])

    result = asyncio.run(scenario())
    assert not result["available"] and result["reason"] == "stale_git_inspection"
    assert not any("cat-file" in call["argv"] for call in supervisor.calls)
    assert (root / "a.py").read_bytes() == b"later user edit\n"


def test_dynamic_directory_deny_stops_before_nested_ignore_or_content_read(tmp_path, monkeypatch):
    root, inspector, _ = setup(tmp_path, working={".gitignore": b"cache/\n!cache/a\n",
        "cache/a": b"not opened", "cache/.gitignore": b"not opened", "ok": b"allowed"})
    original = reader._Files._read_regular

    def guarded(self, relative, maximum):
        assert not relative.startswith("cache/")
        return original(self, relative, maximum)

    monkeypatch.setattr(reader._Files, "_read_regular", guarded)
    result = asyncio.run(inspector.status())
    assert result["available"] and not any(row["path"].startswith("cache/") for row in result["rows"])
    assert result["excluded_count"] > 0 and result["policy_sha256"]
    assert (root / "cache/a").read_bytes() == b"not opened"


def test_binary_and_non_utf8_are_explicit_diff_unavailable_not_replacement_text(tmp_path):
    _, inspector, _ = setup(tmp_path, index={"a": regular(b"old\n")}, working={"a": b"\0binary"})
    result = asyncio.run(inspector.diff("a"))
    assert result["reason"] == "git_diff_binary"


def test_conflict_stage_is_preserved_and_must_be_explicit_for_worktree_plane(tmp_path):
    _, inspector, _ = setup(tmp_path, index={"a": regular(b"ours\n", stage=2)}, working={"a": b"resolution draft\n"})
    assert asyncio.run(inspector.diff("a"))["reason"] == "git_conflict_stage_required"
    result = asyncio.run(inspector.diff("a", conflict_stage=2))
    assert result["available"] and result["before_sha256"] == hashlib.sha256(b"ours\n").hexdigest()
    assert "-ours" in result["text"] and "+resolution draft" in result["text"]


def test_limits_are_explicit_coverage_not_a_truncated_clean_view(tmp_path, monkeypatch):
    _, inspector, _ = setup(tmp_path, index={"a": regular(b"a"), "b": regular(b"b")}, working={"a": b"a", "b": b"b"})
    monkeypatch.setattr(reader, "MAX_ROWS", 1)
    result = asyncio.run(inspector.status())
    assert result["available"] and len(result["rows"]) == 1
    assert "tracked_row_limit" in result["coverage_reasons"] and result["repository_clean"] is None


def test_program_binding_cannot_be_workspace_executable_or_shell_script(tmp_path):
    root, inspector, supervisor = setup(tmp_path)
    program = root / ("git.exe" if os.name == "nt" else "git")
    program.write_bytes(b"must never execute")
    inspector.executable = program
    assert asyncio.run(inspector.status())["reason"] == "git_unavailable"
    inspector.executable = root / "git.cmd"
    assert asyncio.run(inspector.status())["reason"] == "invalid_git_executable_binding"
    assert supervisor.calls == []


def test_raw_stderr_and_host_fixture_are_not_returned_on_failed_command(tmp_path):
    _, inspector, supervisor = setup(tmp_path)
    supervisor.failure = "job_object"
    result = asyncio.run(inspector.status())
    assert result["reason"] == "git_command_failed"
    assert "forbidden" not in str(result) and "host path" not in str(result)


def test_repeated_cancel_drains_process_before_private_view_cleanup(tmp_path):
    _, inspector, supervisor = setup(tmp_path)

    async def scenario():
        supervisor.started, supervisor.release, supervisor.cleaning = asyncio.Event(), asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(inspector.status())
        try:
            await asyncio.wait_for(supervisor.started.wait(), 3)
            private = Path(supervisor.calls[0]["env"]["GIT_DIR"]).parent
            task.cancel()
            await asyncio.wait_for(supervisor.cleaning.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and supervisor.active == 1 and private.exists()
        finally:
            supervisor.release.set()
            outcome = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert supervisor.active == 0 and not private.exists()

    asyncio.run(scenario())


def test_cancelled_offloop_enter_cannot_leak_unreturned_private_view(tmp_path, monkeypatch):
    _, inspector, supervisor = setup(tmp_path)
    ready, release, private = threading.Event(), threading.Event(), []
    original = GitMetadataReader.view

    @contextmanager
    def gated(self):
        with original(self) as view:
            private.append(view.git_dir.parent)
            ready.set()
            if not release.wait(5):
                raise RuntimeError("fixture enter gate timed out")
            yield view

    monkeypatch.setattr(GitMetadataReader, "view", gated)

    async def scenario():
        task = asyncio.create_task(inspector.status())
        try:
            async with asyncio.timeout(3):
                while not ready.is_set():
                    await asyncio.sleep(0.01)
            task.cancel()
            task.cancel()
            assert private[0].exists()
        finally:
            release.set()
            outcome = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert not private[0].exists() and supervisor.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("format_name", ["sha1", "sha256"])
def test_reftable_is_resolved_through_private_stack_not_dummy_head(tmp_path, format_name):
    root, inspector, supervisor = setup(tmp_path, format_name=format_name)
    directory = root / ".git" / "reftable"
    directory.mkdir()
    name = "00000001-00000001-FIXTURE.ref"
    (directory / "tables.list").write_text(name + "\n", encoding="ascii")
    header = b"REFT" + bytes([2]) + b"\0" * 3 + struct.pack(">QQ", 1, 1) + (b"s256" if format_name == "sha256" else b"sha1")
    (directory / name).write_bytes(header)  # only synthetic header; real Git gate is separate
    (root / ".git" / "HEAD").write_bytes(b"ref: refs/heads/.invalid\n")
    result = asyncio.run(inspector.status())
    assert result["available"] and result["ref_storage"] == "reftable"
    assert result["head_id"] == "f" * (40 if format_name == "sha1" else 64)
    assert result["head_ref"] == "refs/heads/main" and result["refs_sha256"]
    assert any("symbolic-ref" in call["argv"] for call in supervisor.calls)
    assert all(call["env"]["GIT_DIR"] != str(root / ".git") for call in supervisor.calls)


def test_diff_text_limits_and_no_newline_annotation_are_honest(monkeypatch):
    text = reader._unified_text(b"old", b"new", "a/a", "b/a", float("inf"))
    assert "-old\n\\ No newline at end of file\n" in text and "+new\n\\ No newline" in text
    with pytest.raises(GitInspectionError, match="git_diff_non_utf8"):
        reader._unified_text(b"\xff", b"new", "a/a", "b/a", float("inf"))
    monkeypatch.setattr(reader, "MAX_DIFF_LINE_PAIRS", 1)
    with pytest.raises(GitInspectionError, match="git_diff_complexity_limit"):
        reader._unified_text(b"a\nb\n", b"c\nd\n", "a/a", "b/a", float("inf"))
