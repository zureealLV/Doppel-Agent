"""Keep the public gallery bilingual, resolvable and tied to original captures."""

import hashlib
import json
import re
import struct
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "docs/images/screenshots-v015-rc1.json"


def jpeg_size(data):
    """Read capture dimensions without an imaging dependency or re-encoding."""
    assert data[:2] == b"\xff\xd8"
    position = 2
    while position + 9 <= len(data):
        assert data[position] == 255
        marker = data[position + 1]
        length = int.from_bytes(data[position + 2:position + 4], "big")
        assert length >= 2
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[position + 5:position + 9])
            return [width, height]
        position += 2 + length
    raise AssertionError("Missing JPEG size marker")


@pytest.mark.parametrize("name", ["README.md", "README_CN.md", "docs/images/README.md"])
def test_presentation_local_links_exist(name):
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    for href in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
        parsed = urlsplit(href)
        if parsed.scheme or not parsed.path:
            continue
        assert (path.parent / unquote(parsed.path)).resolve().exists(), href


@pytest.mark.parametrize("name", ["README.md", "README_CN.md"])
def test_main_readmes_use_current_gallery_not_legacy_images(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    gallery = json.loads(GALLERY.read_text(encoding="utf-8"))
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert images == [f"docs/images/{item['file']}" for item in gallery["images"]]
    assert "Current release: v0.14.4" not in text
    assert "当前版本：`v0.14.4`" not in text
    assert "S0–S10" in text and "N1–N6" in text
    assert gallery["release"] in text


def test_original_gallery_bytes_match_provenance():
    gallery = json.loads(GALLERY.read_text(encoding="utf-8"))
    assert gallery["capture_kind"] == "native-window-original-jpeg"
    assert gallery["submitted_runs"] == 0
    for item in gallery["images"]:
        data = (GALLERY.parent / item["file"]).read_bytes()
        assert data[:3] == b"\xff\xd8\xff"
        assert jpeg_size(data) == item["size"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
