"""Public benchmark workspaces; hidden answer keys never enter Agent input."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
from dataclasses import dataclass
import json
import re
from collections.abc import Mapping
from types import MappingProxyType

from bench.runtime_freeze import freeze_json


REVIEW_CASE_IDS = ("review-01", "review-02", "review-03", "review-04")
FIXTURE_ROOT = Path(__file__).resolve().parent / "cases" / "runtime" / "fixtures"
TASK_CASE_IDS = ("nav-04", "tdd-01", "tdd-02", "tdd-03", "tdd-04", "patch-01", "patch-02", "patch-03", "approval-01", "approval-02", "mcp-01", "mcp-02", "cancel-01")
TDD_ARGV = ("{python}", "-B", "-m", "unittest", "-q", "test_candidate", "test_public")
APPROVAL_COMMAND_ARGV = ("{python}", "-B", "command.py")
CANCEL_COMMAND_ARGV = ("{python}", "-B", "parent.py", "{external_trace}")


@dataclass(frozen=True)
class TaskFixture:
    case_id: str
    category: str
    version: str
    source_commit: str
    public_files: tuple[tuple[str, bytes], ...]
    allowed_edits: tuple[str, ...]
    oracle: Mapping
    sha256: str
    hidden_files: tuple[tuple[str, bytes], ...] = ()
    source_kind: str = "git_snapshot"

    def __post_init__(self):
        object.__setattr__(self, "oracle", freeze_json(self.oracle))
        object.__setattr__(self, "public_files", tuple((path, bytes(payload)) for path, payload in self.public_files))
        object.__setattr__(self, "hidden_files", tuple((path, bytes(payload)) for path, payload in self.hidden_files))
        object.__setattr__(self, "allowed_edits", tuple(self.allowed_edits))


def fixture_path(base: Path, relative: str) -> Path:
    """All fixture paths are portable POSIX names below their explicit root."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("invalid fixture path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(relative).is_absolute():
        raise ValueError("fixture path must stay relative to its root")
    target = base.joinpath(*parts)
    if not target.resolve().is_relative_to(base.resolve()) or any(
        item.is_symlink() for item in (target, *target.parents) if item == base or item.is_relative_to(base)
    ):
        raise ValueError("fixture path must not escape or use symlinks")
    return target


def freeze_review_inputs(*, root: Path = FIXTURE_ROOT):
    """Read/hash-bind all public review sources and external keys exactly once.

    Returned mappings and bytes cannot be mutated. Only the first mapping is
    Agent-visible; the second is exclusively for external human adjudication.
    """
    public_sources, keys = {}, {}
    for case_id in REVIEW_CASE_IDS:
        folder = Path(root) / case_id
        public = fixture_path(folder, "public/service.py").read_bytes()
        hidden = fixture_path(folder, "answer_key.json").read_bytes()
        key = json.loads(hidden)
        lines = public.decode("utf-8").splitlines()
        if not isinstance(key, dict) or set(key) != {"case_id", "path", "findings"} or key["case_id"] != case_id or key["path"] != "service.py" or (
            not isinstance(key["findings"], list) or not key["findings"] or any(
                not isinstance(row, dict) or set(row) != {"id", "line", "signature", "trigger", "impact", "minimal_fix"}
                or type(row["line"]) is not int or not 1 <= row["line"] <= len(lines)
                or any(not isinstance(row[field], str) or not row[field].strip() for field in ("id", "signature", "trigger", "impact", "minimal_fix"))
                or row["signature"] not in lines[row["line"] - 1]
                for row in key["findings"]
            )
        ) or len({row["id"] for row in key["findings"]}) != len(key["findings"]):
            raise ValueError("review key must identify distinct exact frozen source findings")
        public_sources[case_id], keys[case_id] = public, hidden
    return MappingProxyType(public_sources), MappingProxyType(keys)


def load_task_fixture(case_id: str, *, root: Path = FIXTURE_ROOT) -> TaskFixture:
    """Freeze public bytes and external oracle metadata before materializing."""
    if case_id not in TASK_CASE_IDS:
        raise ValueError("unsupported task fixture")
    folder = root / case_id
    raw = (folder / "fixture.json").read_bytes()
    data = json.loads(raw)
    fields = {
        "case_id", "fixture_version", "category", "source_commit", "public_files", "allowed_edits", "oracle",
    }
    if isinstance(data, dict) and data.get("fixture_version") in {"1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"}:
        fields.add("source_kind")
    if not isinstance(data, dict) or set(data) != fields or data["case_id"] != case_id or data["fixture_version"] not in {"1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"}:
        raise ValueError("invalid task fixture metadata")
    if data["category"] not in {"navigation", "tdd_fix", "multi_file_patch", "approval_resume", "mcp_workflow", "concurrent_cancel"} or not isinstance(data["oracle"], dict):
        raise ValueError("invalid task fixture category/oracle")
    expected_category = ("multi_file_patch" if case_id.startswith("patch-") else "tdd_fix" if case_id.startswith("tdd-")
                         else "approval_resume" if case_id.startswith("approval-") else "mcp_workflow" if case_id.startswith("mcp-")
                         else "concurrent_cancel" if case_id == "cancel-01" else "navigation")
    if data["category"] != expected_category:
        raise ValueError("fixture category must match its registered case")
    if not isinstance(data["source_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):
        raise ValueError("fixture source must identify an exact commit")
    manifest = data["public_files"]
    edits = data["allowed_edits"]
    if not isinstance(manifest, dict) or not manifest or not isinstance(edits, list) or any(
        not isinstance(path, str) for path in edits
    ) or len(set(edits)) != len(edits):
        raise ValueError("invalid public fixture files or edit bounds")
    if data["category"] == "navigation":
        if data["fixture_version"] != "1.0" or data.get("source_kind", "git_snapshot") != "git_snapshot":
            raise ValueError("navigation fixture profile must remain version 1.0/git_snapshot")
        if edits:
            raise ValueError("source navigation cannot grant edits")
        oracle = data["oracle"]
        required = oracle.get("required_reads")
        rubric = oracle.get("human_rubric")
        if oracle.get("kind") != "navigation_evidence" or not isinstance(required, dict) or not required or any(
            path not in manifest or not isinstance(anchors, list) or not anchors or any(
                not isinstance(anchor, str) or not anchor for anchor in anchors
            ) for path, anchors in required.items()
        ) or not isinstance(rubric, list) or not rubric or any(not isinstance(item, str) or not item for item in rubric):
            raise ValueError("invalid navigation oracle")
    hidden = []
    if data["category"] == "concurrent_cancel":
        oracle = data["oracle"]
        if data["fixture_version"] != "1.7" or data.get("source_kind") != "synthetic_control" or set(oracle) != {
            "kind", "allowed_argv", "deadline_seconds", "hidden_files",
        } or oracle["kind"] != "supervised_process_tree" or (
            oracle["allowed_argv"] != [list(CANCEL_COMMAND_ARGV)] or edits
            or set(manifest) != {"parent.py", "child.py", "README.md", "LICENSE.txt"}
            or type(oracle["deadline_seconds"]) is not int or oracle["deadline_seconds"] != 10
        ):
            raise ValueError("invalid cancellation fixture bounds/oracle")
        inventory = oracle["hidden_files"]
        if not isinstance(inventory, dict) or set(inventory) != {"termination_contract.json"}:
            raise ValueError("invalid cancellation hidden inventory")
        payload = fixture_path(folder / "oracle", "termination_contract.json").read_bytes()
        if sha256(payload).hexdigest() != inventory["termination_contract.json"]:
            raise ValueError("hidden fixture hash mismatch")
        if json.loads(payload) != {"lifetime_seconds": 45, "deadline_seconds": 10, "termination_cases": ["cancel", "deadline"]}:
            raise ValueError("invalid frozen termination control")
        hidden.append(("termination_contract.json", payload))
    if data["category"] == "mcp_workflow":
        oracle = data["oracle"]
        count = 1 if case_id == "mcp-01" else 2
        if data["fixture_version"] != "1.6" or data.get("source_kind") != "synthetic_control" or set(oracle) != {
            "kind", "allowed_argv", "hidden_files", "expected_remote_calls",
        } or oracle["kind"] != "local_mcp_gateway" or (
            oracle["allowed_argv"] != [] or edits or set(manifest) != {"server.py", "README.md", "LICENSE.txt"}
            or type(oracle["expected_remote_calls"]) is not int or oracle["expected_remote_calls"] != count
        ):
            raise ValueError("invalid MCP fixture bounds/oracle")
        inventory = oracle["hidden_files"]
        if not isinstance(inventory, dict) or set(inventory) != {"expected.json"}:
            raise ValueError("invalid MCP hidden inventory")
        payload = fixture_path(folder / "oracle", "expected.json").read_bytes()
        if sha256(payload).hexdigest() != inventory["expected.json"]:
            raise ValueError("hidden fixture hash mismatch")
        hidden.append(("expected.json", payload))
        expected = json.loads(payload)
        calls = [("echo", {"text": "matrix-canary"})] if count == 1 else [("structured", {"value": 7}), ("failure", {"text": "expected"})]
        if not isinstance(expected, list) or len(expected) != count or any(
            not isinstance(row, dict) or set(row) != {"name", "arguments", "content", "structured_content", "is_error", "model_view"}
            or (row["name"], row["arguments"]) != call or type(row["is_error"]) is not bool
            or not isinstance(row["content"], list) or not isinstance(row["structured_content"], dict)
            or not isinstance(row["model_view"], str) or not row["model_view"]
            for row, call in zip(expected, calls, strict=True)
        ):
            raise ValueError("invalid MCP expected-result contract")
    if data["category"] == "approval_resume":
        command = case_id == "approval-02"
        oracle = data["oracle"]
        if data["fixture_version"] != "1.5" or data.get("source_kind") != "synthetic_control" or set(oracle) != {
            "kind", "decision", "allowed_argv", "hidden_files",
        } or oracle["kind"] != ("command_rejection" if command else "approval_exactly_once") or (
            oracle["decision"] != ("reject" if command else "approve")
            or oracle["allowed_argv"] != ([list(APPROVAL_COMMAND_ARGV)] if command else [])
            or set(manifest) != {"command.py" if command else "note.txt", "README.md", "LICENSE.txt"}
            or edits != ([] if command else ["note.txt"])
        ):
            raise ValueError("invalid approval fixture bounds/oracle")
        inventory = oracle["hidden_files"]
        if not isinstance(inventory, dict) or set(inventory) != {"expected_outcome.json" if command else "reference_changes.json"}:
            raise ValueError("invalid approval hidden inventory")
        for path, expected in sorted(inventory.items()):
            payload = fixture_path(folder / "oracle", path).read_bytes()
            if sha256(payload).hexdigest() != expected:
                raise ValueError("hidden fixture hash mismatch")
            hidden.append((path, payload))
        control = json.loads(hidden[0][1])
        if control != ({"path": "outcome.txt", "content": "executed\n"} if command else {"note.txt": "approved\n"}):
            raise ValueError("invalid frozen approval control")
    if data["category"] == "multi_file_patch":
        oracle = data["oracle"]
        refactor = case_id == "patch-02"
        atomic = case_id == "patch-03"
        oracle_fields = {
            "kind", "required_paths", "allowed_argv", "hidden_files", "expected_seed_target_failures",
        }
        if refactor or atomic:
            oracle_fields |= {"expected_public_tests", "expected_regression_tests"}
        if atomic:
            oracle_fields.add("atomic_controls")
        expected_public = {"service.py", "helpers.py", "test_public.py", "LICENSE.txt"} if refactor else {
            "service.py", "test_public.py", "README.md", "LICENSE.txt",
        }
        expected_edits = {"service.py", "helpers.py"} if refactor else {"service.py", "test_public.py", "README.md"}
        if atomic:
            expected_public = {"service.py", "settings.json", "test_public.py", "LICENSE.txt"}
            expected_edits = {"service.py", "settings.json"}
        if data["fixture_version"] != ("1.4" if atomic else "1.3" if refactor else "1.2") or (
            data.get("source_kind") != ("synthetic_refactor" if refactor else "synthetic_seed")
        ) or set(oracle) != oracle_fields or oracle["kind"] != (
            "atomic_config_patch" if atomic else "behavior_preserving_refactor" if refactor else "reviewed_multi_file"
        ) or set(manifest) != expected_public or set(edits) != expected_edits or oracle["required_paths"] != edits or (
            oracle["allowed_argv"] != [] or type(oracle["expected_seed_target_failures"]) is not int
            or oracle["expected_seed_target_failures"] != 2
        ):
            raise ValueError("invalid multi-file patch bounds/oracle")
        if (refactor or atomic) and any(type(oracle[field]) is not int or oracle[field] != count for field, count in (
            ("expected_public_tests", 4 if atomic else 5), ("expected_regression_tests", 3 if atomic else 5),
        )):
            raise ValueError("invalid frozen patch test counts")
        if atomic and oracle["atomic_controls"] != ["second_replace_failure", "stale_base"]:
            raise ValueError("invalid atomic patch control inventory")
        expected = oracle["hidden_files"]
        if not isinstance(expected, dict) or set(expected) != {
            "reference_changes.json", "target_tests.py", "regression_tests.py", "public_check.py",
        }:
            raise ValueError("invalid patch hidden inventory")
        for path, hash_value in sorted(expected.items()):
            payload = fixture_path(folder / "oracle", path).read_bytes()
            if sha256(payload).hexdigest() != hash_value:
                raise ValueError("hidden fixture hash mismatch")
            hidden.append((path, payload))
        changes = json.loads(dict(hidden)["reference_changes.json"])
        if not isinstance(changes, dict) or set(changes) != set(edits) or any(not isinstance(v, str) for v in changes.values()):
            raise ValueError("invalid reference patch bounds")
    if data["category"] == "tdd_fix":
        oracle = data["oracle"]
        if data["fixture_version"] != "1.1" or data.get("source_kind") != "synthetic_seed" or set(oracle) != {
            "kind", "source_file", "candidate_test", "allowed_argv", "hidden_files", "expected_seed_target_failures",
        } or oracle["kind"] != "tdd_red_green" or oracle["source_file"] != "service.py" or oracle["candidate_test"] != "test_candidate.py" or (
            set(manifest) != {"service.py", "test_public.py"} or set(edits) != {"service.py", "test_candidate.py"}
        ) or oracle["allowed_argv"] != [list(TDD_ARGV)] or (
            not isinstance(oracle["expected_seed_target_failures"], int)
            or isinstance(oracle["expected_seed_target_failures"], bool)
            or not 1 <= oracle["expected_seed_target_failures"] <= 100
        ):
            raise ValueError("invalid TDD fixture bounds/oracle")
        expected_hidden = oracle["hidden_files"]
        if not isinstance(expected_hidden, dict) or set(expected_hidden) != {
            "target_tests.py", "regression_tests.py", "scripted_test.py", "reference_source.py",
        }:
            raise ValueError("invalid TDD hidden file inventory")
        for path, expected in sorted(expected_hidden.items()):
            payload = fixture_path(folder / "oracle", path).read_bytes()
            if sha256(payload).hexdigest() != expected:
                raise ValueError("hidden fixture hash mismatch")
            hidden.append((path, payload))
    for path in edits:
        fixture_path(folder / "public", path)
    files = []
    for path, expected in sorted(manifest.items()):
        if any(marker in path.lower() for marker in ("answer_key", "oracle", "reference_solution", "fixture.json")):
            raise ValueError("hidden fixture data cannot be public")
        source = fixture_path(folder / "public", path)
        if not source.is_file() or not isinstance(expected, str):
            raise ValueError("fixture source must be a regular file with a hash")
        payload = source.read_bytes()
        if sha256(payload).hexdigest() != expected:
            raise ValueError("public fixture hash mismatch")
        files.append((path, payload))
    if data["category"] == "navigation" and any(
        not all(anchor in dict(files)[path].decode("utf-8") for anchor in anchors)
        for path, anchors in data["oracle"]["required_reads"].items()
    ):
        raise ValueError("navigation anchors must match frozen source")
    digest_bytes = raw + b"\0" + b"\0".join(path.encode() + b"\0" + content for path, content in files)
    if hidden:
        digest_bytes += b"\0hidden\0" + b"\0".join(path.encode() + b"\0" + content for path, content in hidden)
    digest = sha256(digest_bytes).hexdigest()
    return TaskFixture(case_id, data["category"], data["fixture_version"], data["source_commit"],
                       tuple(files), tuple(edits), data["oracle"], digest, tuple(hidden), data.get("source_kind", "git_snapshot"))


def materialize_task_case(fixture: TaskFixture, workspace: Path) -> str:
    """Copy only pre-frozen public bytes; keys/oracles remain outside tools."""
    workspace.mkdir(parents=True, exist_ok=False)
    for path, payload in fixture.public_files:
        target = fixture_path(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return fixture.sha256


def materialize_review_case(
    case_id: str, workspace: Path, *, public_source: bytes | None = None,
) -> str:
    """Copy exactly one audited public file into a new isolated workspace."""
    if case_id not in REVIEW_CASE_IDS:
        raise ValueError("unsupported review case")
    source = FIXTURE_ROOT / case_id / "public" / "service.py"
    payload = source.read_bytes() if public_source is None else public_source
    workspace.mkdir(parents=True, exist_ok=False)
    (workspace / "service.py").write_bytes(payload)
    return sha256(payload).hexdigest()
