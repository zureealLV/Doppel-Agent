"""Build entry safety controls; no subprocess, dependency download or app launch."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def builder(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/build_candidate.py"
    spec = importlib.util.spec_from_file_location("candidate_build_controls", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return module


@pytest.mark.parametrize("target", [".dist/DoppelAgent", ".artifacts", "../outside-candidate"])
def test_candidate_rejects_daily_root_or_escaped_output(builder, monkeypatch, tmp_path, target):
    daily = tmp_path / ".dist/DoppelAgent"
    daily.mkdir(parents=True)
    sentinel = daily / "keep.bin"
    sentinel.write_bytes(b"daily must survive")
    monkeypatch.setattr("sys.argv", ["build_candidate.py", "--output", target])
    with pytest.raises(SystemExit) as exc:
        builder.main()
    assert exc.value.code == 2
    assert sentinel.read_bytes() == b"daily must survive"
    assert not (tmp_path / ".artifacts").exists()


def test_candidate_rejects_existing_output_without_overwriting(builder, monkeypatch, tmp_path):
    output = tmp_path / ".artifacts/prior"
    output.mkdir(parents=True)
    sentinel = output / "build-manifest.json"
    sentinel.write_bytes(b"previous immutable receipt")
    monkeypatch.setattr("sys.argv", ["build_candidate.py", "--output", str(output)])
    with pytest.raises(SystemExit) as exc:
        builder.main()
    assert exc.value.code == 2
    assert sentinel.read_bytes() == b"previous immutable receipt"


def test_candidate_inputs_exclude_generated_metadata_but_track_source(builder, tmp_path):
    source = tmp_path / "src/doppel_agent/core.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    generated = tmp_path / "src/doppel_agent.egg-info/PKG-INFO"
    generated.parent.mkdir()
    generated.write_text("generated one", encoding="utf-8")
    before = builder.source_inputs()
    assert set(before) == {"src/doppel_agent/core.py"}
    generated.write_text("generated two", encoding="utf-8")
    assert builder.source_inputs() == before
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert builder.source_inputs() != before
