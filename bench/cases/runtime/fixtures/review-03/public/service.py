"""Attachment retrieval for a document workspace."""

from pathlib import Path


def read_attachment(root: Path, filename: str) -> bytes:
    target = root / filename
    return target.read_bytes()
