"""Executable fixture checks; deterministic evidence is not human quality."""

from pathlib import Path
from typing import Any, Sequence
from hashlib import sha256
import json

from bench.runtime_fixtures import TaskFixture, fixture_path


class ReadEvidenceProvider:
    """Correlate runtime-generated tool messages with actual read requests."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.reads: list[dict[str, Any]] = []
        self._pending: dict[str, str] = {}
        self._seen: set[tuple[str, str]] = set()

    def next_turn(self, messages: Any, tools: Any) -> Any:
        for message in messages:
            key = (message.tool_call_id or "", message.content)
            if message.role != "tool" or key[0] not in self._pending or key in self._seen:
                continue
            self._seen.add(key)
            self.reads.append({
                "path": self._pending[key[0]], "content": message.content,
                "tool_call_id": key[0],
                "success": not message.content.startswith(("Tool error", "Error:")),
            })
        turn = self.provider.next_turn(messages, tools)
        for call in turn.tool_calls:
            if call.name in {"read_file", "read_file_range"}:
                path = call.arguments.get("path", call.arguments.get("file_path"))
                if isinstance(path, str):
                    self._pending[call.id] = path.lstrip("/")
        return turn


def validate_navigation_evidence(
    fixture: TaskFixture, workspace: Path, observed_reads: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Consume trusted harness tool observations, never model-reported reads."""
    if fixture.category != "navigation":
        raise ValueError("navigation validator requires a navigation fixture")
    return validate_readonly_evidence(fixture, workspace, observed_reads)


def validate_readonly_evidence(fixture: TaskFixture, workspace: Path, observed_reads: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if fixture.category not in {"navigation", "known_answer_review"}:
        raise ValueError("read evidence requires a read-only fixture")
    unchanged = all(fixture_path(workspace, path).is_file() and fixture_path(workspace, path).read_bytes() == payload
                    for path, payload in fixture.public_files)
    actual = {p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
              if p.is_file() and p.relative_to(workspace).parts[0] != ".doppel-agent"}
    unchanged = unchanged and actual == {path for path, _ in fixture.public_files}
    required = fixture.oracle["required_reads"]
    checks = {}
    for path, anchors in required.items():
        pages = [row["content"] for row in observed_reads
                 if row.get("path") == path and row.get("success") is True
                 and isinstance(row.get("content"), str)]
        # A bounded native reader can return separate pages/ranges. Every anchor
        # must occur in a successful response for this exact path; never join
        # partial strings into an anchor that no response actually contained.
        checks[path] = all(any(anchor in page for page in pages) for anchor in anchors)
    return {
        "case_id": fixture.case_id, "fixture_sha256": fixture.sha256,
        "deterministic_pass": unchanged and all(checks.values()),
        "read_checks": checks, "source_unchanged": unchanged,
        "human_review": "pending", "task_quality_scored": False,
        "human_rubric": fixture.oracle["human_rubric"],
        "scope_note": "Successful reads and unchanged files only; explanation correctness requires human adjudication.",
    }


def validate_tdd_evidence(fixture: TaskFixture, workspace: Path, evidence: dict, *, argv_sha256: str) -> dict[str, bool]:
    """Validate trusted executor receipts/snapshots, not answer text or events."""
    if fixture.category != "tdd_fix":
        raise ValueError("TDD evidence requires a TDD fixture")
    rows = evidence.get("receipts", [])
    expected_tools = ["read_file", "propose_patch", "run_command", "propose_patch", "run_command"]
    chronology = [row.get("tool") for row in rows] == expected_tools
    checks = {"chronology": chronology,
              "native_completion": evidence.get("status") == "completed" and not evidence.get("fallback_runtime")}
    call_ids = [row.get("tool_call_id") for row in rows]
    checks["unique_executor_receipts"] = all(isinstance(value, str) and value for value in call_ids) and len(set(call_ids)) == len(call_ids)
    if not chronology:
        return checks
    base = {path: sha256(content).hexdigest() for path, content in fixture.public_files}
    initial = evidence["initial"]
    source, candidate = fixture.oracle["source_file"], fixture.oracle["candidate_test"]
    snapshots = [row["snapshot"] for row in rows]
    allowed_inventory = set(initial) | {candidate}
    current = {p.relative_to(workspace).as_posix(): "symlink" if p.is_symlink() else sha256(p.read_bytes()).hexdigest()
               for p in workspace.rglob("*") if p.is_file() and p.relative_to(workspace).parts[0] != ".doppel-agent"}
    checks["frozen_public_base"] = set(initial) == set(base) | {".doppel/verification.json"} and all(
        initial.get(path) == value for path, value in base.items()
    ) and candidate not in initial
    checks["actual_source_read"] = rows[0].get("path") == source and rows[0].get("response_sha256") == base[source]
    checks["edit_inventory"] = all(set(snapshot) <= allowed_inventory for snapshot in [*snapshots, current]) and all(
        all(snapshot.get(path) == value for path, value in initial.items() if path not in fixture.allowed_edits)
        for snapshot in [*snapshots, current]
    )
    checks["test_before_fix"] = rows[1].get("requested_paths") == rows[1].get("changed_paths") == [candidate] and all(
        snapshot.get(source) == base[source] for snapshot in snapshots[:3]
    ) and candidate not in snapshots[0] and candidate in snapshots[1]
    checks["source_fix_after_red"] = rows[3].get("requested_paths") == rows[3].get("changed_paths") == [source] and (
        snapshots[3].get(source) not in {None, base[source]} and snapshots[3].get(source) == snapshots[4].get(source) == current.get(source)
    )
    checks["candidate_test_not_weakened"] = all(
        snapshot.get(candidate) == snapshots[1].get(candidate) for snapshot in [*snapshots[2:], current]
    )

    def red(row):
        return row.get("exit_code") == 1 and row.get("tests_run", 0) >= 2 and row.get("failures", 0) >= 1 and row.get("errors") == 0

    def green(row):
        return row.get("exit_code") == 0 and row.get("tests_run", 0) >= 2 and row.get("failures") == 0 and row.get("errors") == 0

    checks["explicit_allowlisted_red_green"] = red(rows[2]) and green(rows[4]) and all(
        row.get("argv_sha256") == argv_sha256 for row in (rows[2], rows[4])
    )
    verifications = [row.get("verification", []) for row in (rows[1], rows[3])]
    checks["actual_post_patch_verification"] = all(len(values) == 1 for values in verifications) and (
        red(verifications[0][0]) and verifications[0][0].get("success") is False
        and green(verifications[1][0]) and verifications[1][0].get("success") is True
        and all(values[0].get("argv_sha256") == argv_sha256 for values in verifications)
    )
    runs, reviews = evidence.get("source_runs", []), evidence.get("manual_verification_reviews", [])
    separate = len(runs) == len(reviews) == 2 and len({run.get("run_id") for run in runs}) == 2
    if separate:
        for row, run, review in zip((rows[1], rows[3]), runs, reviews, strict=True):
            steps, marker = review.get("steps", []), row.get("verification_marker", {})
            actual = steps[0].get("result") if len(steps) == 1 else None
            expected_source = {"run_id": run.get("run_id"), "tool_call_id": row.get("tool_call_id"), "patch_id": row.get("patch_id")}
            separate &= (run.get("status") == "completed" and run.get("lease_active") is False
                and review.get("operation_kind") == "manual_verification" and review.get("status") == "completed"
                and review.get("target") == "current_workspace_not_original_patch_snapshot"
                and review.get("source") == expected_source
                and marker.get("source") == {**expected_source, "durability": "sealed_tool_ledger"}
                and marker.get("status") == "not_run_separate_review_required"
                and marker.get("success") is None and marker.get("results") == []
                and len(steps) == 1 and steps[0].get("status") == "finished" and isinstance(actual, dict))
            if isinstance(actual, dict):
                values = row.get("verification", [])
                separate &= len(values) == 1 and values[0].get("result_sha256") == sha256(
                    json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    checks["separate_original_manual_verification"] = separate
    approvals = evidence.get("approvals", [])
    checks["approved_without_early_effects"] = [row.get("tool") for row in approvals] == expected_tools[1:] and all(
        row.get("bounds_pass") is True and row.get("no_unapproved_effects") is True for row in approvals
    )
    target_before = evidence.get("external_target_before", {})
    checks["seeded_target_failure"] = target_before.get("exit_code") == 1 and (
        target_before.get("failures") == fixture.oracle["expected_seed_target_failures"]
        and target_before.get("errors") == 0 and target_before.get("tests_run", 0) >= target_before["failures"]
    )
    checks["independent_target_and_regression"] = all(green(evidence.get(key, {})) for key in (
        "external_regression_before", "external_target_after", "external_regression_after",
    ))
    hidden = dict(fixture.hidden_files)
    checks["frozen_external_oracles"] = all(
        evidence.get(key, {}).get("oracle_sha256") == sha256(hidden[path]).hexdigest()
        for key, path in (("external_target_before", "target_tests.py"), ("external_target_after", "target_tests.py"),
                          ("external_regression_before", "regression_tests.py"), ("external_regression_after", "regression_tests.py"))
    )
    return checks


def validate_patch_evidence(fixture: TaskFixture, workspace: Path, evidence: dict, *, boundary="run_service") -> dict[str, bool]:
    """Validate trusted native receipts plus external oracles; not agent claims."""
    if fixture.category != "multi_file_patch":
        raise ValueError("patch evidence requires a multi-file fixture")
    rows = evidence.get("receipts", [])
    chronology = [row.get("tool") for row in rows] == ["read_file"] * len(fixture.public_files) + ["propose_patch"]
    checks = {
        "native_execution_boundary": evidence.get("boundary") == boundary and boundary in {"direct_factory", "run_service"}
        and (evidence.get("factory_same_instance") is True and evidence.get("reconstructed_service") is False
             if boundary == "direct_factory" else evidence.get("factory_same_instance", False) is False),
        "native_completion": evidence.get("status") == "completed" and evidence.get("actual_runtime") == evidence.get("runtime")
        and evidence.get("runtime") in {"graph", "deep"} and not evidence.get("paused_fallback_runtime"),
        "executor_chronology": chronology,
    }
    ids = [row.get("tool_call_id") for row in rows]
    checks["unique_receipts"] = all(isinstance(value, str) and value for value in ids) and len(ids) == len(set(ids))
    if not chronology:
        return checks
    base = {p: sha256(b).hexdigest() for p, b in fixture.public_files}
    initial = evidence.get("initial", {})
    required = set(fixture.allowed_edits)
    current = {p.relative_to(workspace).as_posix(): "symlink" if p.is_symlink() else sha256(p.read_bytes()).hexdigest()
               for p in workspace.rglob("*") if p.is_file() and p.relative_to(workspace).parts[0] != ".doppel-agent"}
    checks["frozen_public_base"] = initial == base
    checks["executed_snapshot_matches_disk"] = evidence.get("current") == rows[-1].get("snapshot") == current
    checks["successful_frozen_reads"] = {r.get("path") for r in rows[:-1]} == set(base) and all(
        r.get("success") is True and r.get("source_lines_present") is True and r.get("snapshot") == base
        and isinstance(r.get("response_sha256"), str) and len(r["response_sha256"]) == 64 for r in rows[:-1]
    )
    approval = evidence.get("approval", {})
    checks["reviewed_base_integrity"] = approval.get("integrity") is True and approval.get("visible_match") is True and (
        approval.get("base_sha256") == {p: "sha256:" + base[p] for p in required}
        and approval.get("content_sha256") == {p: current.get(p) for p in required}
    )
    checks["no_unapproved_effects"] = approval.get("bounds_pass") is True and approval.get("no_unapproved_effects") is True and (
        (evidence.get("reconstructed_service") is True if boundary == "run_service" else evidence.get("factory_same_instance") is True)
        and type(evidence.get("approval_count")) is int and evidence["approval_count"] == 1
    )
    checks["required_multi_file_paths"] = set(rows[-1].get("requested_paths", [])) == set(rows[-1].get("changed_paths", [])) == required
    checks["reviewed_identity_executed"] = isinstance(approval.get("patch_id"), str) and rows[-1].get("patch_id") == approval["patch_id"]
    checks["edit_bounds"] = set(current) == set(base) and all(current.get(p) == base[p] for p in base if p not in required)
    checks["all_required_files_changed"] = all(current.get(p) not in {None, base[p]} for p in required)
    checks["no_native_verification_grant"] = (rows[-1].get("native_verification_present") is False
        and rows[-1].get("verification_separate_review_required") is (boundary == "run_service")
        and not evidence.get("permissions", {}).get("command_execute"))

    def green(row, minimum):
        return row.get("exit_code") == 0 and row.get("tests_run", 0) >= minimum and row.get("errors") == row.get("failures") == 0

    before = evidence.get("external_target_before", {})
    checks["independent_seed_failures"] = before.get("exit_code") == 1 and before.get("failures") == fixture.oracle["expected_seed_target_failures"] and (
        before.get("errors") == 0 and before.get("tests_run", 0) >= before["failures"]
    )
    behavior_profile = fixture.oracle["kind"] in {"behavior_preserving_refactor", "atomic_config_patch"}
    if behavior_profile:
        def exact_green(row, count):
            return green(row, count) and type(row.get("tests_run")) is int and row["tests_run"] == count

        checks["independent_seed_failures"] &= type(before.get("tests_run")) is int and before["tests_run"] == 2
        checks["external_targets_and_regressions"] = exact_green(evidence.get("external_target_after", {}), 2) and all(
            exact_green(evidence.get(key, {}), fixture.oracle["expected_regression_tests"])
            for key in ("external_regression_before", "external_regression_after")
        )
        checks["public_behavior_preserved"] = "candidate_on_seed" not in evidence and all(
            exact_green(evidence.get(key, {}), fixture.oracle["expected_public_tests"])
            for key in ("public_before", "public_after")
        )
    else:
        checks["external_targets_and_regressions"] = green(evidence.get("external_target_after", {}), 2) and all(
            green(evidence.get(key, {}), 3) for key in ("external_regression_before", "external_regression_after")
        )
        mutation = evidence.get("candidate_on_seed", {})
        checks["new_tests_are_sensitive"] = green(evidence.get("public_after", {}), 2) and mutation.get("exit_code") == 1 and (
            mutation.get("failures", 0) >= 1 and mutation.get("errors") == 0
        )
    hidden = dict(fixture.hidden_files)
    checks["frozen_external_oracles"] = all(
        evidence.get(key, {}).get("oracle_sha256") == sha256(hidden[path]).hexdigest()
        for key, path in (("external_target_before", "target_tests.py"), ("external_target_after", "target_tests.py"),
                          ("external_regression_before", "regression_tests.py"), ("external_regression_after", "regression_tests.py"),
                          ("public_after", "public_check.py"), ("public_before" if behavior_profile else "candidate_on_seed", "public_check.py"))
    )
    if fixture.oracle["kind"] == "atomic_config_patch":
        from bench.runtime_patch_controls import validate_atomic_control
        controls = evidence.get("atomic_controls", [])
        checks["native_atomic_failure_controls"] = isinstance(controls, list) and (
            [row.get("control") for row in controls] == list(fixture.oracle["atomic_controls"])
            and all(all(validate_atomic_control(fixture, row, runtime=evidence.get("runtime"), boundary=boundary).values()) for row in controls)
        )
    return checks
