"""A safety patch release must not advertise stale package/API versions."""

import json
import re
import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from doppel_agent import __version__
from doppel_agent.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def frontend_version(python_version):
    """The project publishes stable or rc releases in PEP 440 and npm SemVer."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:rc(\d+))?", python_version)
    assert match is not None, "update the release mapping before adding a new release kind"
    base, candidate = match.groups()
    return base if candidate is None else f"{base}-rc.{candidate}"


@pytest.mark.parametrize("python_version,semver", [("0.14.4", "0.14.4"), ("0.15.0rc1", "0.15.0-rc.1")])
def test_release_naming_mapping_is_explicit(python_version, semver):
    assert frontend_version(python_version) == semver


@pytest.mark.parametrize("surface", ["python", "package", "frontend", "api"])
def test_release_version_is_consistent(surface, tmp_path):
    actual = {
        "python": lambda: version("doppel-agent"),
        "package": lambda: tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "frontend": lambda: json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["version"],
        "api": lambda: create_app(tmp_path).version,
    }[surface]()
    assert actual == (frontend_version(__version__) if surface == "frontend" else __version__)


def test_frontend_lockfile_version_matches_manifest():
    manifest = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == lock["packages"][""]["version"] == manifest["version"]


def test_main_readme_is_english_and_has_current_release():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "A local coding agent" in text
    assert f"v{__version__}" in text
    assert f"v{frontend_version(__version__)}" in text
    assert "README_CN.md" in text


def test_chinese_readme_is_preserved_with_current_release():
    path = ROOT / "README_CN.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "一个在本机运行的编程 Agent" in text
    assert f"v{__version__}" in text
    assert f"v{frontend_version(__version__)}" in text
