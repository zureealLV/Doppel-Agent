"""Real owned Windows junction regressions, not simulated file-symlink proof."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from bench.runtime_fixtures import FIXTURE_ROOT, fixture_path, freeze_review_inputs


def create_owned_junction(root: Path, link: Path, target: Path):
    assert link.absolute().is_relative_to(root.resolve()) and not link.exists()
    assert target.resolve().is_relative_to(root.resolve()) and target.is_dir()
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "actual Windows junction gate requires the existing PowerShell"
    environment = {key: os.environ[key] for key in (
        "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP",
    ) if key in os.environ}
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = f"New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)} -ErrorAction Stop | Out-Null"
    subprocess.run([shell, "-NoProfile", "-Command", command], cwd=root, env=environment,
                   check=True, capture_output=True, text=True)
    assert link.lstat().st_file_attributes & 0x400 and not link.is_symlink()
    assert link.resolve() == target.resolve()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows NTFS junction gate")
@pytest.mark.parametrize("location", ["existing_target", "missing_nested_target", "base_junction"])
def test_fixture_path_refuses_real_in_root_junction_even_when_not_a_symlink(tmp_path, location):
    real = tmp_path / "real"
    real.mkdir()
    (real / "source.txt").write_bytes(b"owned frozen bytes\r\n")
    link = tmp_path / "alias"
    create_owned_junction(tmp_path, link, real)
    try:
        base = link if location == "base_junction" else tmp_path
        relative = "source.txt" if location == "base_junction" else "alias/source.txt"
        if location == "missing_nested_target":
            relative = "alias/not-created/new.txt"
        with pytest.raises(ValueError, match="fixture path.*symlink"):
            fixture_path(base, relative)
        assert (real / "source.txt").read_bytes() == b"owned frozen bytes\r\n"
        assert not (real / "not-created").exists()
    finally:
        # Non-recursive unlink of this explicit owned mount point only; not its target.
        assert link.lstat().st_file_attributes & 0x400
        assert link.resolve().is_relative_to(tmp_path.resolve())
        link.rmdir()
        assert real.is_dir() and (real / "source.txt").exists()


def test_fixture_path_keeps_uncreated_materialization_destinations(tmp_path):
    target = fixture_path(tmp_path, "new-parent/nested/source.py")
    assert target == tmp_path / "new-parent/nested/source.py"
    assert not target.exists()
    target.parent.mkdir(parents=True)
    target.write_bytes(b"owned public bytes\n")
    assert fixture_path(tmp_path, "new-parent/nested/source.py").read_bytes() == b"owned public bytes\n"


@pytest.mark.skipif(os.name != "nt", reason="actual Windows NTFS junction gate")
def test_freeze_review_inputs_rejects_native_parent_junction_before_any_file_read(tmp_path, monkeypatch):
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    for case_id in ("review-01", "review-02", "review-03", "review-04"):
        destination = reviews / case_id
        if case_id != "review-01":
            shutil.copytree(FIXTURE_ROOT / case_id, destination)
        else:
            (destination / "public-real").mkdir(parents=True)
            shutil.copyfile(FIXTURE_ROOT / case_id / "public/service.py", destination / "public-real/service.py")
            shutil.copyfile(FIXTURE_ROOT / case_id / "answer_key.json", destination / "answer_key.json")
    link = reviews / "review-01/public"
    create_owned_junction(tmp_path, link, reviews / "review-01/public-real")
    reads = []
    original = Path.read_bytes

    def record_read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", record_read)
    try:
        with pytest.raises(ValueError, match="fixture path.*reparse"):
            freeze_review_inputs(root=reviews)
        assert reads == []  # No aliased public source OR hidden answer key was read.
    finally:
        assert link.lstat().st_file_attributes & 0x400
        assert link.resolve().is_relative_to(tmp_path.resolve())
        link.rmdir()
