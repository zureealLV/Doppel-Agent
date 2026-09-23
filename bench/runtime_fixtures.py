"""Public benchmark workspaces; hidden answer keys never enter Agent input."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


REVIEW_CASE_IDS = ("review-01", "review-02", "review-03", "review-04")
FIXTURE_ROOT = Path(__file__).resolve().parent / "cases" / "runtime" / "fixtures"


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
