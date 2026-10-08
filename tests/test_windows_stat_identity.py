"""Path/handle timestamps must share semantics without weakening fd checks."""

import os
from time import monotonic
from types import SimpleNamespace

import pytest

from doppel_agent.context.manifest import ManifestReader
from doppel_agent.workspace.git_metadata import _signature
from doppel_agent.workspace.git_inspection import GitInspectionError
from doppel_agent.workspace.verification_config import _identity
from doppel_agent.workspace import git_metadata, verification_config


SIGNATURES = (ManifestReader._signature, _signature, _identity)


def info(**changes):
    fields = dict(st_dev=1, st_ino=2, st_mode=0o100600, st_nlink=1, st_size=3,
                  st_mtime_ns=4, st_ctime_ns=5, st_birthtime_ns=5, st_file_attributes=0)
    return SimpleNamespace(**(fields | changes))


@pytest.mark.skipif(os.name != "nt", reason="Windows stat/fstat compatibility")
@pytest.mark.parametrize("signature", SIGNATURES)
def test_path_and_handle_creation_identity_match_after_rewrite(signature):
    # Windows 3.12 path ctime is birthtime, fd ctime is metadata change time.
    assert signature(info()) == signature(info(st_ctime_ns=90))


@pytest.mark.parametrize("signature", SIGNATURES)
@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns"])
def test_identity_or_content_change_still_rejected(signature, field):
    assert signature(info()) != signature(info(**{field: 99}))


@pytest.mark.skipif(os.name != "nt", reason="Windows birthtime identity")
@pytest.mark.parametrize("signature", SIGNATURES)
def test_different_creation_time_still_rejected(signature):
    assert signature(info()) != signature(info(st_birthtime_ns=99))


@pytest.mark.parametrize("reader", ["manifest", "git", "verification"])
def test_handle_change_time_still_detected_during_read(tmp_path, monkeypatch, reader):
    path = tmp_path / "sample.txt"
    path.write_bytes(b"fixture")
    actual = os.fstat
    calls = 0

    def changing_fd(descriptor):
        nonlocal calls
        result = actual(descriptor)
        calls += 1
        fields = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
        if calls == 2:
            # Same identity/bytes/mtime/birthtime; only the fd change clock moved.
            fields["st_ctime_ns"] += 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(os, "fstat", changing_fd)
    with pytest.raises((GitInspectionError, ValueError), match="changed|unavailable"):
        if reader == "manifest":
            ManifestReader(tmp_path)._read_regular("sample.txt", 1024)
        elif reader == "git":
            git_metadata._MetadataGuard((tmp_path,), deadline=monotonic() + 5).read(path, 1024)
        else:
            verification_config.capture(tmp_path, path)
