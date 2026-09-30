"""Public benchmark workspaces; hidden answer keys never enter Agent input."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
from dataclasses import dataclass
import json
import re


REVIEW_CASE_IDS = ("review-01", "review-02", "review-03", "review-04")
FIXTURE_ROOT = Path(__file__).resolve().parent / "cases" / "runtime" / "fixtures"
TASK_CASE_IDS = ("nav-04", "tdd-01", "tdd-02", "tdd-03", "tdd-04")
TDD_ARGV = ("{python}", "-B", "-m", "unittest", "-q", "test_candidate", "test_public")


@dataclass(frozen=True)
class TaskFixture:
    case_id: str
    category: str
    version: str
    source_commit: str
    public_files: tuple[tuple[str, bytes], ...]
    allowed_edits: tuple[str, ...]
    oracle: dict
    sha256: str
    hidden_files: tuple[tuple[str, bytes], ...] = ()
    source_kind: str = "git_snapshot"


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
    if isinstance(data, dict) and data.get("fixture_version") == "1.1":
        fields.add("source_kind")
    if not isinstance(data, dict) or set(data) != fields or data["case_id"] != case_id or data["fixture_version"] not in {"1.0", "1.1"}:
        raise ValueError("invalid task fixture metadata")
    if data["category"] not in {"navigation", "tdd_fix"} or not isinstance(data["oracle"], dict):
        raise ValueError("invalid task fixture category/oracle")
    if not isinstance(data["source_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):
        raise ValueError("fixture source must identify an exact commit")
    manifest = data["public_files"]
    edits = data["allowed_edits"]
    if not isinstance(manifest, dict) or not manifest or not isinstance(edits, list) or any(
        not isinstance(path, str) for path in edits
    ) or len(set(edits)) != len(edits):
        raise ValueError("invalid public fixture files or edit bounds")
    if data["category"] == "navigation":
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
