import sqlite3
import pytest

from doppel_agent.projects.catalog import ProjectCatalog, local_project_path


def test_catalog_registers_real_directory_without_creating_project_state(tmp_path):
    project = tmp_path / "项目 with spaces"
    project.mkdir()
    (project / ".env").write_text("NOT_A_CATALOG_FIELD", encoding="utf-8")
    catalog = ProjectCatalog(tmp_path / "app" / "projects.sqlite3")
    record = catalog.register(project)
    assert record["name"] == project.name
    assert record["opened_at_ns"] == 0
    assert catalog.resolve(record["project_id"]) == project.resolve()
    assert not (project / ".doppel-agent").exists()
    assert "NOT_A_CATALOG_FIELD" not in str(catalog.list())


def test_catalog_deduplicates_and_persists_open_time(tmp_path):
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    first = catalog.register(tmp_path)
    second = catalog.register(tmp_path / ".")
    assert first["project_id"] == second["project_id"]
    catalog.mark_opened(first["project_id"])
    reopened = ProjectCatalog(catalog.database)
    assert len(reopened.list()) == 1
    assert reopened.get(first["project_id"])["opened_at_ns"] > 0


def test_catalog_is_bounded_to_twenty_records(tmp_path):
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    for index in range(25):
        path = tmp_path / f"project-{index}"
        path.mkdir()
        catalog.register(path)
    assert len(catalog.list()) == 20


@pytest.mark.parametrize("value", ["relative", "", "//remote/share", "\\\\server\\share", "nul\0path"])
def test_catalog_rejects_invalid_or_network_paths(value):
    with pytest.raises(ValueError):
        local_project_path(value)


def test_catalog_rejects_files_and_missing_directories(tmp_path):
    file = tmp_path / "file.txt"
    file.write_text("text", encoding="utf-8")
    with pytest.raises(ValueError):
        local_project_path(file)
    with pytest.raises(OSError):
        local_project_path(tmp_path / "missing")


def test_catalog_stale_entries_are_listable_but_cannot_be_opened(tmp_path):
    project = tmp_path / "gone"
    project.mkdir()
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    record = catalog.register(project)
    project.rmdir()
    assert catalog.list()[0]["project_id"] == record["project_id"]
    with pytest.raises(OSError):
        catalog.resolve(record["project_id"])


def test_catalog_rejects_unknown_and_sql_shaped_identities(tmp_path):
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    for key in ["a" * 32, "' OR 1=1 --", "C:/other-project", None]:
        with pytest.raises(ValueError):
            catalog.get(key)


def test_catalog_detects_changed_target_in_persisted_record(tmp_path):
    old, target = tmp_path / "old", tmp_path / "target"
    old.mkdir()
    target.mkdir()
    catalog = ProjectCatalog(tmp_path / "catalog.sqlite3")
    record = catalog.register(old)
    with sqlite3.connect(catalog.database) as connection:
        connection.execute("UPDATE recent_projects SET path=? WHERE project_id=?", (str(target), record["project_id"]))
    with pytest.raises(ValueError, match="changed"):
        catalog.resolve(record["project_id"])
