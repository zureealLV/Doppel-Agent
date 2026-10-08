"""Bounded, owner-scoped metadata views for read-only Git plumbing.

Never opens repository config, credentials, hooks or working-file contents.
The source object store is borrowed only after bounded no-link checks; identities
are checked again by the command owner. This is application-level checking, not
an OS sandbox against a hostile concurrent filesystem actor. No Git is invoked
by this module. The private view must outlive every supervised command/reader.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Iterator, Sequence

if TYPE_CHECKING:
    from .git_authorization import LinkedMetadataBinding

from .git_inspection import GitInspectionError, ObjectFormat, object_id
from .service import WorkspaceService


MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_METADATA_ENTRIES = 40_000
MAX_OBJECT_ENTRIES = 20_000


def _signature(info: os.stat_result) -> tuple[int, ...]:
    # Windows path/fd ctime have different semantics from Python 3.12.
    stamp = getattr(info, "st_birthtime_ns", info.st_ctime_ns) if os.name == "nt" else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, stamp, getattr(info, "st_file_attributes", 0))


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _line(raw: bytes, reason: str) -> str:
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise GitInspectionError(reason) from None
    # One optional final LF/CRLF, never embedded control data or extra lines.
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise GitInspectionError(reason)
    return value


def _ref(value: str) -> str:
    parts = value.split("/")
    if (
        not value.startswith("refs/") or len(value) > 2048
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "~^:?*[\\" for char in value)
        or ".." in value or "@{" in value
        or any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in parts)
    ):
        raise GitInspectionError("invalid_git_ref")
    return value


def _oid(value: str) -> tuple[str, ObjectFormat]:
    format_name: ObjectFormat = "sha256" if len(value) == 64 else "sha1"
    return object_id(value, format_name), format_name


@dataclass(frozen=True)
class IndexMetadata:
    object_format: ObjectFormat
    checksum: str
    shared_index: str | None
    sparse: bool


def inspect_index(raw: bytes) -> IndexMetadata:
    """Validate checksum/structural bounds; leave full index semantics to Git.

    Read only names inside index metadata, not referenced working-file content.
    Locate a split-index link by parsing entry framing rather than substring
    search (a path or optional extension can contain arbitrary 'link' bytes).
    """
    if not isinstance(raw, bytes) or not 32 <= len(raw) <= MAX_INDEX_BYTES or raw[:4] != b"DIRC":
        raise GitInspectionError("invalid_git_index")
    matches = [name for name, size in (("sha1", 20), ("sha256", 32))
               if len(raw) >= 12 + size and hashlib.new(name, raw[:-size]).digest() == raw[-size:]]
    if len(matches) != 1:
        raise GitInspectionError("invalid_git_index_checksum")
    format_name = matches[0]
    hash_bytes = 20 if format_name == "sha1" else 32
    end = len(raw) - hash_bytes
    version, count = struct.unpack_from(">II", raw, 4)
    if version not in {2, 3, 4} or count > 20_000:
        raise GitInspectionError("unsupported_git_index")
    offset, previous_name = 12, b""
    for _ in range(count):
        start = offset
        fixed = 40 + hash_bytes + 2
        if offset + fixed > end:
            raise GitInspectionError("invalid_git_index_entry")
        flags = struct.unpack_from(">H", raw, offset + fixed - 2)[0]
        offset += fixed
        if flags & 0x4000:
            if version == 2 or offset + 2 > end:
                raise GitInspectionError("invalid_git_index_entry")
            offset += 2
        strip = 0
        if version == 4:
            if offset >= end:
                raise GitInspectionError("invalid_git_index_entry")
            byte = raw[offset]
            offset += 1
            strip = byte & 0x7f
            steps = 1
            while byte & 0x80:
                if offset >= end or steps >= 5:
                    raise GitInspectionError("invalid_git_index_entry")
                byte = raw[offset]
                offset += 1
                strip = ((strip + 1) << 7) | (byte & 0x7f)
                steps += 1
            if strip > len(previous_name):
                raise GitInspectionError("invalid_git_index_entry")
        terminator = raw.find(b"\0", offset, min(end, offset + 2049))
        if terminator < 0:
            raise GitInspectionError("invalid_git_index_entry")
        name = raw[offset:terminator]
        if version == 4:
            name = previous_name[:len(previous_name) - strip] + name
        if len(name) > 2048:
            raise GitInspectionError("git_index_path_limit")
        previous_name = name
        offset = terminator + 1
        if version != 4:
            padded = start + ((offset - start + 7) // 8) * 8
            if padded > end or any(raw[offset:padded]):
                raise GitInspectionError("invalid_git_index_padding")
            offset = padded
    shared, sparse, seen = None, False, set()
    while offset < end:
        if offset + 8 > end:
            raise GitInspectionError("invalid_git_index_extension")
        signature = raw[offset:offset + 4]
        size = struct.unpack_from(">I", raw, offset + 4)[0]
        offset += 8
        if offset + size > end or signature in seen:
            raise GitInspectionError("invalid_git_index_extension")
        seen.add(signature)
        if signature == b"link":
            if size < hash_bytes:
                raise GitInspectionError("invalid_git_split_index")
            shared = raw[offset:offset + hash_bytes].hex()
            if not shared.strip("0"):
                shared = None  # zero shared identity means no shared base file
        elif signature == b"sdir":
            sparse = True
        elif not 65 <= signature[0] <= 90:
            raise GitInspectionError("unsupported_git_index_extension")
        offset += size
    return IndexMetadata(format_name, raw[-hash_bytes:].hex(), shared, sparse)


class _MetadataGuard:
    def __init__(self, roots: Sequence[Path], *, deadline: float):
        self.roots = tuple(roots)
        self.deadline = deadline
        self.observed: dict[Path, tuple[int, ...] | None] = {}
        self.bytes_read = 0

    def budget(self) -> None:
        if monotonic() > self.deadline:
            raise GitInspectionError("git_metadata_deadline")
        if len(self.observed) > MAX_METADATA_ENTRIES:
            raise GitInspectionError("git_metadata_entry_limit")

    def _observe(self, path: Path, info: os.stat_result | None) -> None:
        value = None if info is None else _signature(info)
        if path in self.observed and self.observed[path] != value:
            raise GitInspectionError("git_metadata_changed")
        self.observed[path] = value
        self.budget()

    def info(self, path: Path) -> os.stat_result | None:
        self.budget()
        anchors = [root for root in self.roots if path.is_relative_to(root)]
        if not anchors:
            raise GitInspectionError("git_metadata_authorization_required")
        anchor = max(anchors, key=lambda root: len(root.parts))
        current = anchor
        components = (None, *path.relative_to(anchor).parts)
        for index, part in enumerate(components):
            if part is not None:
                if part in {".", ".."}:
                    raise GitInspectionError("invalid_git_metadata_path")
                current /= part
            try:
                info = current.lstat()
            except FileNotFoundError:
                self._observe(current, None)
                return None
            except OSError:
                raise GitInspectionError("git_metadata_unavailable") from None
            if _linked(info):
                raise GitInspectionError("git_metadata_link_blocked")
            if index != len(components) - 1 and not stat.S_ISDIR(info.st_mode):
                raise GitInspectionError("git_metadata_nonregular")
            self._observe(current, info)
        return info

    def directory(self, path: Path) -> None:
        info = self.info(path)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise GitInspectionError("git_metadata_directory_unavailable")

    def read(self, path: Path, maximum: int, *, optional: bool = False) -> bytes | None:
        before = self.info(path)
        if before is None:
            if optional:
                return None
            raise GitInspectionError("git_metadata_unavailable")
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise GitInspectionError("git_metadata_nonregular_or_hardlink")
        if before.st_size > maximum or self.bytes_read + before.st_size > MAX_METADATA_BYTES:
            raise GitInspectionError("git_metadata_byte_limit")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if _signature(opened) != _signature(before):
                    raise GitInspectionError("git_metadata_changed")
                raw = handle.read(maximum + 1)
                if len(raw) > maximum or self.bytes_read + len(raw) > MAX_METADATA_BYTES:
                    raise GitInspectionError("git_metadata_byte_limit")
                after_fd = os.fstat(handle.fileno())
                if _signature(opened) != _signature(after_fd) or opened.st_ctime_ns != after_fd.st_ctime_ns:
                    raise GitInspectionError("git_metadata_changed")
            after = self.info(path)
            if after is None or _signature(opened) != _signature(after):
                raise GitInspectionError("git_metadata_changed")
        except GitInspectionError:
            raise
        except OSError:
            raise GitInspectionError("git_metadata_unavailable") from None
        self.bytes_read += len(raw)
        return raw

    def check_unchanged(self) -> None:
        for path in tuple(self.observed):
            self.info(path)

    def inspect_objects(self, objects: Path) -> None:
        self.directory(objects)
        for name in ("alternates", "http-alternates"):
            if self.info(objects / "info" / name) is not None:
                raise GitInspectionError("git_object_alternates_blocked")
        pending, count = [(objects, 0)], 0
        while pending:
            directory, depth = pending.pop()
            self.directory(directory)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        count += 1
                        self.budget()
                        if count > MAX_OBJECT_ENTRIES:
                            raise GitInspectionError("git_object_entry_limit")
                        path = directory / entry.name
                        info = self.info(path)
                        if info is None:
                            raise GitInspectionError("git_metadata_changed")
                        if stat.S_ISDIR(info.st_mode):
                            if depth >= 4:
                                raise GitInspectionError("git_object_depth_limit")
                            pending.append((path, depth + 1))
                        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise GitInspectionError("git_metadata_nonregular_or_hardlink")
            except GitInspectionError:
                raise
            except OSError:
                raise GitInspectionError("git_metadata_unavailable") from None


@dataclass(frozen=True)
class GitMetadataView:
    git_dir: Path
    empty_worktree: Path
    objects: Path
    head_id: str | None
    head_ref: str | None
    object_format: ObjectFormat | None
    index_sha256: str | None
    linked_worktree: bool
    sparse_index: bool
    guard: _MetadataGuard
    ref_storage: str = "files"
    private_root: Path | None = None
    refs_sha256: str | None = None

    @property
    def plumbing_format(self) -> ObjectFormat:
        # This is the private empty view's choice, not an observed repository
        # format when both HEAD objects and an index are absent.
        return self.object_format or "sha1"

    def environment(self) -> dict[str, str]:
        # Do not enumerate/read credential values merely to discard them.
        env = {}
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            value = os.environ.get(name)
            if value is not None:
                env[name] = value
        private = self.private_root or self.git_dir.parent
        env.update({
            "HOME": str(private / "home"), "XDG_CONFIG_HOME": str(private / "home"),
            "GIT_DIR": str(self.git_dir), "GIT_WORK_TREE": str(self.empty_worktree),
            "GIT_OBJECT_DIRECTORY": str(self.objects), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ATTR_NOSYSTEM": "1", "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C", "LANG": "C",
        })
        return env

    def fixed_options(self) -> tuple[str, ...]:
        private = self.private_root or self.git_dir.parent
        return ("--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false",
                "-c", "core.untrackedCache=false", "-c", f"core.hooksPath={private / 'hooks'}",
                "-c", f"core.attributesFile={os.devnull}", "-c", f"core.excludesFile={os.devnull}",
                "-c", "protocol.allow=never", "-c", "credential.helper=")


class GitMetadataReader:
    def __init__(self, workspace: Path, *, authorized_metadata_roots: Sequence[Path] = (),
                 linked_binding: LinkedMetadataBinding | None = None):
        self.root = WorkspaceService(workspace).root
        self.linked_binding = linked_binding
        roots: list[Path] = []
        if not isinstance(authorized_metadata_roots, (tuple, list)) or len(authorized_metadata_roots) > 8:
            raise GitInspectionError("git_metadata_authorization_limit")
        # Roots are an owner-only binding, NOT a public request field. Do not
        # open any externally pointed file merely to discover its common root.
        for value in authorized_metadata_roots:
            if not isinstance(value, (str, os.PathLike)):
                raise GitInspectionError("invalid_git_metadata_authorization")
            path = Path(value)
            # A common Git dir may be a bare repository named other than .git.
            # The owner binding and exact worktrees/backpointer relationship,
            # not an arbitrary directory-name suffix, establish the scope.
            if not path.is_absolute() or ".." in path.parts or path.parent == path:
                raise GitInspectionError("invalid_git_metadata_authorization")
            try:
                info = path.lstat()
                if _linked(info) or not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path:
                    raise GitInspectionError("invalid_git_metadata_authorization")
            except (OSError, RuntimeError, ValueError):
                raise GitInspectionError("invalid_git_metadata_authorization") from None
            if path not in roots:
                roots.append(path)
        self.authorized_roots = tuple(roots)

    def _bound_pointer(self, guard, path, raw):
        if self.linked_binding is None:
            return
        expected = next((row for row in self.linked_binding.pointers if row[0] == path), None)
        if (expected is None or hashlib.sha256(raw).hexdigest() != expected[1]
                or guard.observed.get(path) != expected[2]):
            raise GitInspectionError("git_metadata_authorization_changed")

    def _discover(self, guard: _MetadataGuard) -> tuple[Path, Path, bool]:
        marker = self.root / ".git"
        info = guard.info(marker)
        if info is None:
            raise GitInspectionError("not_git_repository")
        if stat.S_ISDIR(info.st_mode):
            if self.linked_binding is not None:
                raise GitInspectionError("git_metadata_authorization_changed")
            if guard.info(marker / "commondir") is not None:
                raise GitInspectionError("git_metadata_authorization_required")
            return marker, marker, False
        raw = guard.read(marker, 4096)
        self._bound_pointer(guard, marker, raw)
        value = _line(raw, "invalid_git_worktree_pointer")
        if not value.startswith("gitdir: "):
            raise GitInspectionError("invalid_git_worktree_pointer")
        pointer = Path(value[8:])
        candidate = Path(os.path.abspath(pointer if pointer.is_absolute() else self.root / pointer))
        common = candidate.parent.parent
        name = candidate.name
        if self.linked_binding is not None and (self.root != self.linked_binding.workspace
                or candidate != self.linked_binding.local or common != self.linked_binding.common):
            raise GitInspectionError("git_metadata_authorization_changed")
        if (candidate.parent.name != "worktrees" or not 1 <= len(name) <= 255
                or name in {".", ".."} or name.endswith((".", " "))
                or any(ord(char) < 32 or ord(char) == 127 or char in "/\\:" for char in name)
                or common not in self.authorized_roots):
            raise GitInspectionError("git_metadata_authorization_required")
        guard.directory(candidate)
        if self.linked_binding is not None:
            for path, expected in self.linked_binding.directories:
                if not any(path.is_relative_to(root) for root in guard.roots):
                    continue  # Ancestors above authorized roots were owner-checked before entry.
                current = guard.info(path)
                identity = None if current is None else (current.st_dev, current.st_ino, current.st_mode,
                    getattr(current, "st_file_attributes", 0))
                if identity != expected:
                    raise GitInspectionError("git_metadata_authorization_changed")
        raw_common = guard.read(candidate / "commondir", 128)
        self._bound_pointer(guard, candidate / "commondir", raw_common)
        if _line(raw_common, "invalid_git_commondir") != "../..":
            raise GitInspectionError("invalid_git_commondir")
        raw_back = guard.read(candidate / "gitdir", 4096)
        self._bound_pointer(guard, candidate / "gitdir", raw_back)
        back = _line(raw_back, "invalid_git_worktree_backpointer")
        if not Path(back).is_absolute() or Path(os.path.abspath(back)) != marker:
            raise GitInspectionError("invalid_git_worktree_backpointer")
        return candidate, common, True

    @staticmethod
    def _tables(guard: _MetadataGuard, directory: Path) -> tuple[dict[str, bytes], ObjectFormat | None]:
        guard.directory(directory)
        raw_list = guard.read(directory / "tables.list", 64 * 1024, optional=True)
        if raw_list is None:
            raise GitInspectionError("git_reftable_stack_unavailable")
        try:
            names = raw_list.decode("ascii", errors="strict").splitlines()
        except UnicodeError:
            raise GitInspectionError("invalid_git_reftable_stack") from None
        if len(names) > 128 or len(set(name.casefold() for name in names)) != len(names):
            raise GitInspectionError("git_reftable_stack_limit")
        files, format_name = {"tables.list": raw_list}, None
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}\.(?:ref|log)", name):
                raise GitInspectionError("invalid_git_reftable_name")
            raw = guard.read(directory / name, 8 * 1024 * 1024)
            if len(raw) < 24 or raw[:4] != b"REFT" or raw[4] not in {1, 2}:
                raise GitInspectionError("invalid_git_reftable_header")
            observed: ObjectFormat = "sha1"
            if raw[4] == 2:
                if len(raw) < 28 or raw[24:28] not in {b"sha1", b"s256"}:
                    raise GitInspectionError("invalid_git_reftable_hash")
                observed = "sha256" if raw[24:28] == b"s256" else "sha1"
            if format_name is not None and observed != format_name:
                raise GitInspectionError("git_object_format_mismatch")
            format_name = observed
            files[name] = raw
        return files, format_name

    @staticmethod
    def _head(guard: _MetadataGuard, git_dir: Path, common: Path) -> tuple[str | None, str | None, ObjectFormat | None]:
        # Missing loose/packed refs is not evidence of unborn HEAD for reftable.
        # Support needs an isolated table view; do not silently misclassify it.
        if guard.info(common / "reftable") is not None:
            raise GitInspectionError("git_ref_storage_requires_isolated_tables")
        value = _line(guard.read(git_dir / "HEAD", 4096), "invalid_git_head")
        head_ref, seen, packed, packed_format = None, set(), None, None
        for _ in range(8):
            if not value.startswith("ref: "):
                oid, format_name = _oid(value)
                if packed_format is not None and packed_format != format_name:
                    raise GitInspectionError("git_object_format_mismatch")
                return oid, head_ref, format_name
            reference = _ref(value[5:])
            if reference in seen:
                raise GitInspectionError("git_ref_cycle")
            seen.add(reference)
            if head_ref is None:
                head_ref = reference
            owner = git_dir if reference.startswith(("refs/bisect/", "refs/worktree/", "refs/rewritten/")) else common
            raw = guard.read(owner / reference, 4096, optional=True)
            if raw is not None:
                value = _line(raw, "invalid_git_ref")
                continue
            if packed is None:
                packed = {}
                raw_packed = guard.read(common / "packed-refs", 1024 * 1024, optional=True)
                if raw_packed is not None:
                    try:
                        lines = raw_packed.decode("utf-8", errors="strict").splitlines()
                    except UnicodeError:
                        raise GitInspectionError("invalid_git_packed_refs") from None
                    aliases, previous = set(), False
                    if len(lines) > 20_000:
                        raise GitInspectionError("git_packed_ref_limit")
                    for line in lines:
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("^"):
                            _, format_name = _oid(line[1:])
                            if not previous or packed_format != format_name:
                                raise GitInspectionError("invalid_git_packed_refs")
                            previous = False
                            continue
                        parts = line.split(" ")
                        if len(parts) != 2:
                            raise GitInspectionError("invalid_git_packed_refs")
                        oid, format_name = _oid(parts[0])
                        ref = _ref(parts[1])
                        if ref.casefold() in aliases or packed_format is not None and packed_format != format_name:
                            raise GitInspectionError("invalid_git_packed_refs")
                        aliases.add(ref.casefold())
                        packed[ref], packed_format, previous = oid, format_name, True
            if reference in packed:
                value = packed[reference]
                continue
            if reference.startswith("refs/heads/") and len(seen) == 1:
                return None, head_ref, packed_format  # unborn, not a clean receipt
            raise GitInspectionError("git_ref_unavailable")
        raise GitInspectionError("git_ref_depth_limit")

    @contextmanager
    def view(self) -> Iterator[GitMetadataView]:
        """Owner holds this context through all command and worker drain.

        Preparation performs bounded local metadata IO, not a Git command. It
        must be run inside the application's owned off-loop operation at A2b.
        """
        guard = _MetadataGuard((self.root, *self.authorized_roots), deadline=monotonic() + 10)
        git_dir, common, linked = self._discover(guard)
        tables, local_tables, raw_head = None, None, None
        if guard.info(common / "reftable") is not None:
            tables, head_format = self._tables(guard, common / "reftable")
            if git_dir != common and guard.info(git_dir / "reftable") is not None:
                local_tables, local_format = self._tables(guard, git_dir / "reftable")
                if local_format is not None:
                    if head_format is not None and local_format != head_format:
                        raise GitInspectionError("git_object_format_mismatch")
                    head_format = local_format
            raw_head = guard.read(git_dir / "HEAD", 4096)
            head_line = _line(raw_head, "invalid_git_head")
            if head_line != "ref: refs/heads/.invalid":
                if head_line.startswith("ref: "):
                    _ref(head_line[5:])
                else:
                    _, observed = _oid(head_line)
                    if head_format is not None and observed != head_format:
                        raise GitInspectionError("git_object_format_mismatch")
                    head_format = observed
            head, reference = None, None  # actual HEAD must be resolved from private tables
        else:
            head, reference, head_format = self._head(guard, git_dir, common)
        index = guard.read(git_dir / "index", MAX_INDEX_BYTES, optional=True)
        index_info = None if index is None else inspect_index(index)
        format_name = head_format if index_info is None else index_info.object_format
        if head_format is not None and format_name != head_format:
            raise GitInspectionError("git_object_format_mismatch")
        shared = None
        if index_info is not None and index_info.shared_index is not None:
            shared = guard.read(git_dir / f"sharedindex.{index_info.shared_index}", MAX_INDEX_BYTES)
            shared_info = inspect_index(shared)
            if (shared_info.checksum != index_info.shared_index
                    or shared_info.object_format != format_name or shared_info.shared_index is not None):
                raise GitInspectionError("invalid_git_split_index")
        objects = common / "objects"
        guard.inspect_objects(objects)
        guard.check_unchanged()
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        if any(temp_root.is_relative_to(root) for root in (self.root, *self.authorized_roots)):
            raise GitInspectionError("git_private_temp_scope_unavailable")
        with tempfile.TemporaryDirectory(prefix="doppel-git-inspection-", dir=temp_root) as directory:
            private = Path(directory)
            private_common = private / "git"
            isolated = private_common / "worktrees" / "current" if tables is not None and linked else private_common
            empty = private / "worktree"
            for path in (private_common / "refs", private_common / "objects", isolated / "refs", empty, private / "home", private / "hooks"):
                path.mkdir(parents=True, exist_ok=True)
            chosen_format = format_name or "sha1"
            extended = chosen_format == "sha256" or bool(index_info and index_info.sparse) or tables is not None
            config = "[core]\n\trepositoryformatversion = " + ("1" if extended else "0") + "\n\tbare = false\n"
            if extended:
                config += "[extensions]\n"
                if chosen_format == "sha256":
                    config += "\tobjectFormat = sha256\n"
                if index_info and index_info.sparse:
                    config += "\tsparseIndex = true\n"
                if tables is not None:
                    config += "\trefStorage = reftable\n"
            (private_common / "config").write_text(config, encoding="utf-8")
            refs_hash = None
            if tables is not None:
                digest = hashlib.sha256()
                for role, target, files in (("common", private_common, tables), ("worktree", isolated, local_tables)):
                    if files is None:
                        continue
                    destination = target / "reftable"
                    destination.mkdir(exist_ok=True)
                    digest.update(role.encode("ascii") + b"\0")
                    for name, raw in files.items():
                        (destination / name).write_bytes(raw)
                        digest.update(name.encode("ascii") + b"\0" + hashlib.sha256(raw).digest())
                    (target / "HEAD").write_bytes(b"ref: refs/heads/.invalid\n")
                    (target / "refs" / "heads").write_bytes(b"this repository uses reftable\n")
                refs_hash = digest.hexdigest()
                (isolated / "HEAD").write_bytes(raw_head)
                if isolated != private_common:
                    (isolated / "commondir").write_bytes(b"../..\n")
            else:
                (isolated / "HEAD").write_text((head or "ref: refs/heads/doppel-unborn") + "\n", encoding="ascii")
            if index is not None:
                (isolated / "index").write_bytes(index)
            if shared is not None:
                (isolated / f"sharedindex.{index_info.shared_index}").write_bytes(shared)
            yield GitMetadataView(
                isolated, empty, objects, head, reference, format_name,
                None if index is None else hashlib.sha256(index).hexdigest(), linked,
                bool(index_info and index_info.sparse), guard,
                "reftable" if tables is not None else "files", private, refs_hash,
            )
