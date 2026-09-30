"""Executable fixture checks; deterministic evidence is not human quality."""

from pathlib import Path
from typing import Any, Sequence

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
