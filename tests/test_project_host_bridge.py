from types import SimpleNamespace
from unittest.mock import patch

from doppel_agent.desktop import WindowApi


def host_fixture(tmp_path, *, url="http://127.0.0.1:12345/runtime/"):
    calls = []
    window = SimpleNamespace(get_current_url=lambda: url,
                             create_file_dialog=lambda *_args, **_kwargs: calls.append("choose") or None)
    kernel = SimpleNamespace(workspace=tmp_path, url="http://127.0.0.1:12345/", ready=True)
    api = WindowApi(kernel, catalog_path=tmp_path / "app" / "projects.sqlite3")
    view = SimpleNamespace(windows=[window], FileDialog=SimpleNamespace(FOLDER="folder"))
    return api, view, calls


def test_untrusted_webview_cannot_open_picker_or_read_recent_projects(tmp_path):
    api, view, calls = host_fixture(tmp_path, url="https://evil.invalid/")
    with patch.dict("sys.modules", {"webview": view}):
        assert not api.project_list()["ok"]
        assert not api.project_choose()["ok"]
        assert not api.project_switch("a" * 32, True)["ok"]
    assert not calls and not (tmp_path / "app").exists()


def test_picker_cancellation_does_not_switch_or_register_a_new_project(tmp_path):
    api, view, calls = host_fixture(tmp_path)
    with patch.dict("sys.modules", {"webview": view}):
        assert api.project_choose() == {"ok": True, "cancelled": True}
        assert len(api.project_list()["projects"]) == 1
    assert calls == ["choose"]


def test_picker_registration_does_not_create_target_runtime_state(tmp_path):
    project = tmp_path / "new"
    project.mkdir()
    api, view, calls = host_fixture(tmp_path)
    view.windows[0].create_file_dialog = lambda *_args, **_kwargs: (str(project),)
    with patch.dict("sys.modules", {"webview": view}):
        selected = api.project_choose()
    assert selected["ok"] and selected["project"]["name"] == "new"
    assert not (project / ".doppel-agent").exists()
    assert not calls


def test_switch_confirmation_is_required_before_catalog_or_resources(tmp_path):
    api, view, _calls = host_fixture(tmp_path)
    with patch.dict("sys.modules", {"webview": view}):
        assert not api.project_switch("a" * 32)["ok"]
        assert not api.project_switch("a" * 32, "yes")["ok"]
    assert not (tmp_path / "app").exists()


def test_host_rejects_concurrent_window_actions_during_project_switch(tmp_path):
    api, view, _calls = host_fixture(tmp_path)
    api._action_lock.acquire()
    try:
        with patch.dict("sys.modules", {"webview": view}):
            assert not api.project_choose()["ok"]
            assert not api.project_switch("a" * 32, True)["ok"]
            assert api.window_action("close") is False
    finally:
        api._action_lock.release()
