"""Application-owned, policy-filtered Git change inspection (no mutation tools).

All Git argv are fixed here, outside the actual worktree/config, and supervised
by the existing process owner. Raw workspace comparison intentionally does not
apply attributes, filters, CRLF conversion, rename heuristics or porcelain clean
semantics. A filtered/bounded view never establishes whole-repository clean.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from ..context.manifest import ContextManifestError, ManifestReader, relative_path
from ..owned_async import await_durable
from .git_inspection import GitEntry, GitInspectionError, _remember_spelling, object_id, parse_index, parse_tree
from .git_metadata import GitMetadataReader, GitMetadataView, _ref, _signature
from .process_supervisor import ProcessOutputLimitError, ProcessSupervisionError, ProcessSupervisor


MAX_ROWS = 1000
MAX_WALK_ENTRIES = 2000
MAX_WORKTREE_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_DIFF_BYTES = 256 * 1024
MAX_COMMANDS = 24
MAX_DIFF_LINE_PAIRS = 1_000_000


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _blob_oid(raw: bytes, format_name: str) -> str:
    return hashlib.new(format_name, b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _unified_text(before: bytes | None, after: bytes | None, left_name: str, right_name: str,
                  deadline: float) -> str:
    try:
        left, right = (before or b"").decode("utf-8", errors="strict"), (after or b"").decode("utf-8", errors="strict")
    except UnicodeError:
        raise GitInspectionError("git_diff_non_utf8") from None
    if "\x00" in left or "\x00" in right:
        raise GitInspectionError("git_diff_binary")
    if monotonic() >= deadline:
        raise GitInspectionError("git_inspection_deadline")
    if before == after:
        return ""
    def lines(value: str) -> list[str]:
        parts = value.split("\n")
        return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])

    left_lines, right_lines = lines(left), lines(right)
    # Bound SequenceMatcher's quadratic input before entering the owned worker;
    # do not claim that asyncio cancellation preempts CPU inside a thread.
    if len(left_lines) * len(right_lines) > MAX_DIFF_LINE_PAIRS:
        raise GitInspectionError("git_diff_complexity_limit")
    text, consumed = [], 0
    for chunk in difflib.unified_diff(left_lines, right_lines, fromfile=left_name, tofile=right_name):
        if monotonic() >= deadline:
            raise GitInspectionError("git_inspection_deadline")
        if chunk and chunk[0] in "+- " and not chunk.endswith("\n"):
            chunk += "\n\\ No newline at end of file\n"
        consumed += len(chunk.encode("utf-8"))
        if consumed > MAX_DIFF_BYTES:
            raise GitInspectionError("git_diff_byte_limit")
        text.append(chunk)
    return "".join(text)


def _git_executable(root: Path, explicit: Path | None) -> tuple[Path, tuple[int, ...]]:
    name = "git.exe" if os.name == "nt" else "git"
    candidates: list[Path] = []
    if explicit is not None:
        if not explicit.is_absolute() or explicit.name.casefold() != name:
            raise GitInspectionError("invalid_git_executable_binding")
        candidates.append(explicit)
    else:
        # No implicit cwd or PATHEXT .cmd/.bat search; never expand arbitrary
        # project-controlled shell/env values. An explicit binding is owner-only.
        for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
            folder = Path(directory.strip('"'))
            if directory and folder.is_absolute() and not folder.is_relative_to(root):
                candidates.append(folder / name)
    for candidate in candidates[:256]:
        if candidate.is_relative_to(root):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
            if resolved.is_relative_to(root) or not stat.S_ISREG(info.st_mode):
                continue
            if os.name != "nt" and not os.access(resolved, os.X_OK):
                continue
            return resolved, _signature(info)
        except (OSError, RuntimeError, ValueError):
            continue
    raise GitInspectionError("git_unavailable")


class _Files(ManifestReader):
    def __init__(self, root: Path, view: GitMetadataView):
        super().__init__(root)
        self.view = view
        self.cache: dict[str, bytes] = {}
        self.consumed = 0
        self.policies: set[str] = set()

    def _ignore_info(self, relative: str) -> os.stat_result:
        # Preserve ManifestReader's ancestor order and deny-before-descend.
        # Pin only policies actually visited, including absent policies.
        self.policies.add(relative)
        info = self.view.guard.info(self.root / relative)
        if info is None:
            raise FileNotFoundError
        return info

    def _read_regular(self, relative: str, maximum: int) -> bytes:
        info = self.view.guard.info(self.root / relative)
        if info is None:
            raise ContextManifestError("context_file_unavailable")
        if relative in self.cache:
            raw = self.cache[relative]
            if len(raw) > maximum:
                raise ContextManifestError("context_file_too_large")
            return raw
        if self.consumed + info.st_size > MAX_WORKTREE_BYTES:
            raise ContextManifestError("inspection_worktree_byte_limit")
        raw = super()._read_regular(relative, maximum)
        self.view.guard.info(self.root / relative)
        self.consumed += len(raw)
        self.cache[relative] = raw
        return raw

    def snapshot(self, path: str) -> tuple[bytes | None, str | None]:
        path = relative_path(path)
        self._check_ignores(path)
        info = self.view.guard.info(self.root / path)  # no-follow parents, including missing leaves
        if info is None:
            self.view.guard.info(self.root / path)
            return None, None
        raw = self._raw(path)
        return raw, "100755" if info.st_mode & 0o111 else "100644"

    def policy_hash(self) -> str:
        return hashlib.sha256(_canonical([
            [path, None if path not in self.cache else hashlib.sha256(self.cache[path]).hexdigest()]
            for path in sorted(self.policies)
        ])).hexdigest()


class _Plumbing:
    def __init__(self, view: GitMetadataView, executable: Path, signature: tuple[int, ...],
                 supervisor: ProcessSupervisor, operation_id: str):
        self.view, self.executable, self.signature = view, executable, signature
        self.supervisor, self.operation_id = supervisor, operation_id
        self.commands = 0

    async def command(self, argv: tuple[str, ...], *, limit: int = 4 * 1024 * 1024,
                      allow_codes: tuple[int, ...] = ()):
        self.commands += 1
        if self.commands > MAX_COMMANDS:
            raise GitInspectionError("git_command_limit")
        await await_durable(asyncio.to_thread(self.view.guard.check_unchanged))
        try:
            if _signature(self.executable.stat()) != self.signature:
                raise GitInspectionError("git_executable_changed")
            remaining = self.view.guard.deadline - monotonic()
            if remaining <= 0:
                raise GitInspectionError("git_inspection_deadline")
            result = await self.supervisor.run_binary(
                [str(self.executable), *self.view.fixed_options(), *argv],
                cwd=self.view.empty_worktree, run_id=self.operation_id,
                timeout_seconds=remaining, env=self.view.environment(), output_limit=limit,
                require_tree_ownership=True,
            )
            if _signature(self.executable.stat()) != self.signature:
                raise GitInspectionError("git_executable_changed")
        except ProcessSupervisionError:
            raise GitInspectionError("git_supervision_unavailable") from None
        except ProcessOutputLimitError:
            raise GitInspectionError("git_output_limit") from None
        except TimeoutError:
            raise GitInspectionError("git_inspection_deadline") from None
        except OSError:
            raise GitInspectionError("git_executable_unavailable") from None
        await await_durable(asyncio.to_thread(self.view.guard.check_unchanged))
        if result.exit_code != 0 and result.exit_code not in allow_codes:
            raise GitInspectionError("git_command_failed")  # never expose raw stderr/host/config data
        if result.supervision not in {"job_object", "process_group"}:
            raise GitInspectionError("git_supervision_unavailable")
        return result

    async def head(self) -> tuple[str | None, str | None]:
        if self.view.ref_storage == "files":
            return self.view.head_id, self.view.head_ref
        symbolic = await self.command(("symbolic-ref", "--quiet", "HEAD"), limit=4096, allow_codes=(1,))
        reference = None
        if symbolic.exit_code == 0:
            try:
                reference = _ref(symbolic.stdout.decode("utf-8", errors="strict").removesuffix("\n"))
            except UnicodeError:
                raise GitInspectionError("invalid_git_ref") from None
        resolved = await self.command(("rev-parse", "--verify", "--quiet", "HEAD"), limit=128, allow_codes=(1,))
        if resolved.exit_code == 0:
            try:
                return object_id(resolved.stdout.decode("ascii").removesuffix("\n"), self.view.plumbing_format), reference
            except UnicodeError:
                raise GitInspectionError("invalid_git_object_id") from None
        if reference is not None and reference.startswith("refs/heads/"):
            absent = await self.command(("show-ref", "--verify", "--quiet", reference), limit=128, allow_codes=(1,))
            if absent.exit_code == 1:
                return None, reference
        raise GitInspectionError("git_head_unavailable")

    async def blob(self, entry: GitEntry | None) -> bytes | None:
        if entry is None:
            return None
        if not entry.regular_file:
            raise GitInspectionError("git_metadata_only_diff")
        oid = object_id(entry.object_id, self.view.plumbing_format)
        kind = await self.command(("cat-file", "-t", oid), limit=128)
        if kind.stdout != b"blob\n":
            raise GitInspectionError("invalid_git_blob_type")
        size_result = await self.command(("cat-file", "-s", oid), limit=128)
        size = size_result.stdout.removesuffix(b"\n")
        if not size or not size.isdigit() or len(size) > 16 or int(size) > MAX_FILE_BYTES:
            raise GitInspectionError("git_blob_byte_limit")
        result = await self.command(("cat-file", "blob", oid), limit=MAX_FILE_BYTES)
        if len(result.stdout) != int(size) or _blob_oid(result.stdout, self.view.plumbing_format) != oid:
            raise GitInspectionError("git_blob_identity_mismatch")
        return result.stdout


def _entry(entry: GitEntry | None) -> dict[str, Any] | None:
    return None if entry is None else {"path": entry.path, "mode": entry.mode,
                                      "object_id": entry.object_id, "kind": entry.kind, "stage": entry.stage}


def _status_row(path: str, head: GitEntry | None, stages: list[GitEntry], files: _Files) -> dict[str, Any]:
    indexed = next((entry for entry in stages if entry.stage == 0), None)
    conflict = any(entry.stage != 0 for entry in stages)
    if conflict:
        staged = "unmerged"
    elif head is None and indexed is None:
        staged = "none"
    elif head is None:
        staged = "added"
    elif indexed is None:
        staged = "deleted"
    elif head.mode != indexed.mode:
        staged = "mode_or_type_changed"
    elif head.object_id != indexed.object_id:
        staged = "modified"
    elif head.path != indexed.path:
        staged = "case_renamed"
    else:
        staged = "same"
    row = {"path": path, "head": _entry(head), "index": _entry(indexed),
           "stages": [_entry(entry) for entry in stages if entry.stage != 0], "staged": staged,
           "worktree": "unknown", "worktree_sha256": None, "worktree_blob_oid": None,
           "worktree_mode": None, "mode_comparison": "not_applied_on_windows" if os.name == "nt" else "raw_execute_bits",
           "reason": None, "agent_generated": None}
    if any(not entry.regular_file for entry in [item for item in (head, *stages) if item is not None]):
        files._check_ignores(path)
        row.update(worktree="metadata_only", reason="links_submodules_trees_not_followed")
        return row
    try:
        raw, mode = files.snapshot(path)
        if raw is None:
            row["worktree"] = "missing"
        else:
            oid = _blob_oid(raw, files.view.plumbing_format)
            row.update(worktree_sha256=hashlib.sha256(raw).hexdigest(), worktree_blob_oid=oid, worktree_mode=mode)
            if conflict:
                row["worktree"] = "unmerged_workspace"
            elif indexed is None:
                row["worktree"] = "untracked"
            else:
                row["worktree"] = "same_raw" if oid == indexed.object_id else "different_raw"
    except ContextManifestError as exc:
        reason = str(exc)
        if reason == "context_path_ignored" or reason.startswith("context_ignore_"):
            raise
        row["reason"] = reason
    except GitInspectionError as exc:
        if str(exc) != "git_metadata_link_blocked":
            raise
        row["reason"] = "workspace_link_blocked"
    return row


def _collect(files: _Files, head_entries, index_entries, head_id: str | None, head_ref: str | None):
    heads = {entry.path.casefold(): entry for entry in head_entries.entries}
    indexes: dict[str, list[GitEntry]] = {}
    for entry in index_entries.entries:
        indexes.setdefault(entry.path.casefold(), []).append(entry)
    rows, excluded, coverage = [], head_entries.excluded_count + index_entries.excluded_count, []
    keys = sorted(heads.keys() | indexes.keys())
    if len(keys) > MAX_ROWS:
        coverage.append("tracked_row_limit")
    lookup: dict[str, tuple[GitEntry | None, list[GitEntry]]] = {}
    for key in keys[:MAX_ROWS]:
        head, stages = heads.get(key), indexes.get(key, [])
        path = stages[0].path if stages else head.path
        try:
            row = _status_row(path, head, stages, files)
        except ContextManifestError as exc:
            if str(exc) == "context_path_ignored":
                excluded += 1
                continue
            raise GitInspectionError("git_workspace_ignore_unavailable") from None
        rows.append(row)
        lookup[key] = head, stages
    submodules = {entry.path.casefold() for entry in (*head_entries.entries, *index_entries.entries)
                  if entry.mode == "160000"}
    pending, walked, spellings = [(files.root, "")], 0, {}
    while pending:
        directory, prefix = pending.pop()
        files.view.guard.directory(directory)
        try:
            with os.scandir(directory) as iterator:
                names = []
                for item in iterator:
                    walked += 1
                    files.view.guard.budget()
                    if walked > MAX_WALK_ENTRIES:
                        coverage.append("untracked_walk_limit")
                        break
                    names.append(item.name)
            if walked > MAX_WALK_ENTRIES:
                break
            # Stable enumeration in each directory; no traversal of links or
            # excluded/ignored/submodule directories, never inspect child .git.
            for name in sorted(names, reverse=True):
                path = f"{prefix}/{name}" if prefix else name
                try:
                    relative_path(path)
                except ContextManifestError:
                    if name != ".git":
                        excluded += 1
                    continue
                key = path.casefold()
                _remember_spelling(path, spellings)
                if key in submodules:
                    coverage.append("submodule_not_traversed")
                    continue
                try:
                    info = files.view.guard.info(files.root / path)
                    if info is None:
                        raise GitInspectionError("git_metadata_changed")
                    if stat.S_ISDIR(info.st_mode):
                        files._check_ignores(path + "/.doppel-inspection-probe")
                        pending.append((files.root / path, path))
                        continue
                    if key in heads or key in indexes:
                        continue
                    if len(rows) >= MAX_ROWS:
                        coverage.append("untracked_row_limit")
                        continue
                    files._check_ignores(path)
                    row = _status_row(path, None, [], files)
                    rows.append(row)
                    lookup[key] = None, []
                except ContextManifestError as exc:
                    if str(exc) == "context_path_ignored":
                        excluded += 1
                        continue
                    if str(exc).startswith("context_ignore_"):
                        raise GitInspectionError("git_workspace_ignore_unavailable") from None
                    coverage.append("untracked_unavailable")
                except GitInspectionError as exc:
                    if str(exc) != "git_metadata_link_blocked":
                        raise
                    coverage.append("untracked_link_blocked")
        except OSError:
            coverage.append("untracked_directory_unavailable")
    rows.sort(key=lambda row: (row["path"].casefold(), row["path"]))
    unknown = sum(row["worktree"] in {"unknown", "metadata_only"} for row in rows)
    if unknown:
        coverage.append("workspace_unknown")
    status = {"available": True, "reason": None, "head_id": head_id, "head_ref": head_ref,
              "object_format": files.view.object_format, "ref_storage": files.view.ref_storage,
              "index_sha256": files.view.index_sha256, "refs_sha256": files.view.refs_sha256,
              "linked_worktree": files.view.linked_worktree, "rows": rows,
              "excluded_count": excluded, "unknown_count": unknown, "walked_entries": min(walked, MAX_WALK_ENTRIES),
              "coverage_reasons": sorted(set(coverage)), "policy_sha256": files.policy_hash(),
              "comparison": "raw_workspace_no_filters_or_eol_conversion", "repository_clean": None,
              "agent_attribution": "not_inferred_from_repository_changes"}
    status["fingerprint"] = hashlib.sha256(_canonical(status)).hexdigest()
    status["captured_at"] = datetime.now(UTC).isoformat()
    return status, lookup


class GitInspector:
    """Borrow the owner's supervisor; no runtime/provider/DB/command grant here."""

    def __init__(self, workspace: Path, *, supervisor: ProcessSupervisor,
                 authorized_metadata_roots: tuple[Path, ...] = (), executable: Path | None = None,
                 linked_binding=None):
        self.reader = GitMetadataReader(workspace, authorized_metadata_roots=authorized_metadata_roots,
                                        linked_binding=linked_binding)
        self.supervisor, self.executable = supervisor, executable
        self._lock = asyncio.Lock()

    async def _query(self, action: Callable, operation_id: str | None):
        if operation_id is not None and (
            not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128 or "\x00" in operation_id
        ):
            raise GitInspectionError("invalid_git_inspection_operation")
        async with self._lock:
            manager = self.reader.view()
            entered: list[GitMetadataView] = []

            def enter():
                view = manager.__enter__()
                entered.append(view)  # known owner handle before cancellation of to_thread can return
                return view

            try:
                executable, signature = await await_durable(asyncio.to_thread(
                    _git_executable, self.reader.root, self.executable,
                ))
                view = await await_durable(asyncio.to_thread(enter))
                plumbing = _Plumbing(view, executable, signature, self.supervisor,
                                     operation_id or f"changes:{uuid4().hex}")
                head_id, head_ref = await plumbing.head()
                indexed = await plumbing.command(("ls-files", "--cached", "--stage", "-z"))
                index_entries = parse_index(indexed.stdout, view.plumbing_format)
                if head_id is None:
                    head_entries = parse_tree(b"", view.plumbing_format)
                else:
                    tree = await plumbing.command(("ls-tree", "-r", "-z", "--full-tree", head_id))
                    head_entries = parse_tree(tree.stdout, view.plumbing_format)
                files = _Files(self.reader.root, view)
                status, lookup = await await_durable(asyncio.to_thread(
                    _collect, files, head_entries, index_entries, head_id, head_ref,
                ))
                result = await action(plumbing, files, status, lookup)
                await await_durable(asyncio.to_thread(view.guard.check_unchanged))
                return result
            except GitInspectionError as exc:
                return {"available": False, "reason": str(exc), "repository_clean": None,
                        "agent_attribution": "not_inferred_from_repository_changes"}
            except (OSError, ContextManifestError):
                return {"available": False, "reason": "git_inspection_unavailable", "repository_clean": None,
                        "agent_attribution": "not_inferred_from_repository_changes"}
            finally:
                if entered:
                    await await_durable(asyncio.to_thread(manager.__exit__, None, None, None))

    async def status(self, *, operation_id: str | None = None) -> dict[str, Any]:
        async def result(_plumbing, _files, status, _lookup):
            return status
        return await self._query(result, operation_id)

    async def diff(self, path: str, *, plane: str = "worktree", expected_fingerprint: str | None = None,
                   conflict_stage: int | None = None, operation_id: str | None = None) -> dict[str, Any]:
        try:
            path = relative_path(path)
        except ContextManifestError:
            raise GitInspectionError("invalid_git_diff_path") from None
        if not isinstance(plane, str) or plane not in {"staged", "worktree", "combined"} or conflict_stage is not None and (
            type(conflict_stage) is not int or conflict_stage not in {1, 2, 3}
        ):
            raise GitInspectionError("invalid_git_diff_selection")
        if expected_fingerprint is not None and (
            not isinstance(expected_fingerprint, str) or len(expected_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in expected_fingerprint)
        ):
            raise GitInspectionError("invalid_git_inspection_fingerprint")

        async def result(plumbing, files, status, lookup):
            if expected_fingerprint is not None and expected_fingerprint != status["fingerprint"]:
                raise GitInspectionError("stale_git_inspection")
            key = path.casefold()
            if key not in lookup:
                raise GitInspectionError("git_path_not_in_inspection")
            row = next(row for row in status["rows"] if row["path"].casefold() == key)
            # Use the admitted spelling from the actual record, not a caller's
            # alternative case/alias, and never accept arbitrary object IDs.
            selected = row["path"]
            head, stages = lookup[key]
            indexed = next((entry for entry in stages if entry.stage == (conflict_stage or 0)), None)
            if row["staged"] == "unmerged" and plane != "combined" and conflict_stage is None:
                raise GitInspectionError("git_conflict_stage_required")
            if conflict_stage is not None and not any(entry.stage == conflict_stage for entry in stages):
                raise GitInspectionError("git_conflict_stage_unavailable")
            if plane == "staged":
                before, after = await plumbing.blob(head), await plumbing.blob(indexed)
                before_mode, after_mode = None if head is None else head.mode, None if indexed is None else indexed.mode
            else:
                before_entry = head if plane == "combined" else indexed
                before = await plumbing.blob(before_entry)
                if row["worktree"] in {"unknown", "metadata_only"}:
                    raise GitInspectionError("git_workspace_diff_unavailable")
                after, after_mode = await await_durable(asyncio.to_thread(files.snapshot, selected))
                before_mode = None if before_entry is None else before_entry.mode
            text = await await_durable(asyncio.to_thread(
                _unified_text, before, after,
                f"a/{head.path if plane != 'worktree' and head else selected}" if before is not None else "/dev/null",
                f"b/{selected}" if after is not None else "/dev/null", files.view.guard.deadline,
            ))
            return {"available": True, "reason": None, "path": selected, "plane": plane,
                    "conflict_stage": conflict_stage, "fingerprint": status["fingerprint"],
                    "before_sha256": None if before is None else hashlib.sha256(before).hexdigest(),
                    "after_sha256": None if after is None else hashlib.sha256(after).hexdigest(),
                    "before_exists": before is not None, "after_exists": after is not None,
                    "before_mode": before_mode, "after_mode": after_mode, "text": text,
                    "comparison": status["comparison"], "agent_generated": None}
        return await self._query(result, operation_id)
