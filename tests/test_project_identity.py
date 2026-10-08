from pathlib import Path

import pytest

from doppel_agent.projects.service import current_project


def test_identity_does_not_create_state_or_read_project_files(tmp_path):
    project = tmp_path / "项目 with spaces"
    project.mkdir()
    (project / ".env").write_text("NEVER_READ_SECRET", encoding="utf-8")
    before = sorted(item.name for item in project.iterdir())
    identity = current_project(project)
    assert identity.name == "项目 with spaces"
    assert identity.path == str(project)
    assert identity.switching_available is False
    assert sorted(item.name for item in project.iterdir()) == before


def test_identity_rejects_relative_workspace():
    with pytest.raises(ValueError, match="must be absolute"):
        current_project(Path("relative-project"))


def test_identity_is_metadata_not_a_filesystem_probe(tmp_path):
    missing = tmp_path / "not-created"
    identity = current_project(missing)
    assert identity.name == "not-created"
    assert not missing.exists()
