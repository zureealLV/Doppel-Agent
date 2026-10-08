"""D1b S9 native-bridge definitions: fake windows/chooser, no GUI operation."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from doppel_agent.desktop import WindowApi


def host(tmp_path):
    selected = tmp_path / "metadata"
    selected.mkdir()
    calls = []
    kernel = SimpleNamespace(workspace=tmp_path, url="http://127.0.0.1:12345/", ready=True)
    kernel._native_git_metadata = lambda action, *args: calls.append((action, args)) or {"fixture": action}
    window = SimpleNamespace(get_current_url=lambda: "http://127.0.0.1:12345/runtime/",
        create_file_dialog=lambda *args, **kwargs: (str(selected),),
        create_confirmation_dialog=lambda title, message: calls.append(("confirm", message)) or True)
    view = SimpleNamespace(windows=[window], FileDialog=SimpleNamespace(FOLDER="folder"))
    api = WindowApi(kernel, catalog_path=tmp_path / "never-create-catalog.sqlite3")
    return api, view, kernel, selected, calls


def test_authority_comes_from_native_selection_and_dialog_not_javascript_boolean_or_path(tmp_path):
    api, view, kernel, selected, calls = host(tmp_path)
    with patch.dict("sys.modules", {"webview": view}):
        response = api.git_metadata_authorize()
        assert response["ok"] and calls[0][0] == "confirm" and calls[1][0] == "grant"
        assert json.dumps(str(kernel.workspace), ensure_ascii=True) in calls[0][1]
        assert json.dumps(str(selected), ensure_ascii=True) in calls[0][1]
        assert calls[1][1][0].path == selected
        with pytest.raises(TypeError):
            api.git_metadata_authorize(str(selected), True)
        assert api.git_metadata_authorization()["ok"] and calls[-1][0] == "read"
        assert api.git_metadata_revoke()["ok"] and calls[-1][0] == "revoke"
    assert not (tmp_path / "never-create-catalog.sqlite3").exists() and api._switcher is None


@pytest.mark.parametrize("confirmation", [False, None, 1, "yes"])
def test_native_confirmation_requires_exact_true(tmp_path, confirmation):
    api, view, _, _, calls = host(tmp_path)
    view.windows[0].create_confirmation_dialog = lambda *args: confirmation
    with patch.dict("sys.modules", {"webview": view}):
        assert api.git_metadata_authorize() == {"ok": True, "cancelled": True}
    assert not calls


def test_chooser_cancel_is_not_a_grant_or_catalog_read(tmp_path):
    api, view, _, _, calls = host(tmp_path)
    view.windows[0].create_file_dialog = lambda *a, **k: None
    with patch.dict("sys.modules", {"webview": view}):
        assert api.git_metadata_authorize() == {"ok": True, "cancelled": True}
    assert not calls


@pytest.mark.parametrize("operation", ["git_metadata_authorize", "git_metadata_authorization", "git_metadata_revoke"])
def test_untrusted_or_busy_bridge_cannot_choose_read_or_change_scope(tmp_path, operation):
    api, view, _, _, calls = host(tmp_path)
    view.windows[0].get_current_url = lambda: "https://evil.invalid/"
    with patch.dict("sys.modules", {"webview": view}):
        assert not getattr(api, operation)()["ok"]
        api._action_lock.acquire()
        try:
            assert not getattr(api, operation)()["ok"]
        finally:
            api._action_lock.release()
    assert not calls


@pytest.mark.parametrize("change", ["origin", "window", "kernel", "close"])
def test_dialog_completion_rechecks_original_origin_window_kernel_and_admission(tmp_path, change):
    api, view, kernel, _, calls = host(tmp_path)
    window = view.windows[0]

    def confirm(*args):
        if change == "origin":
            window.get_current_url = lambda: "https://evil.invalid/"
        elif change == "window":
            view.windows[0] = SimpleNamespace(get_current_url=window.get_current_url)
        elif change == "kernel":
            api._switcher = SimpleNamespace(kernel=SimpleNamespace(ready=True, workspace=tmp_path))
        else:
            kernel.ready = False
        return True

    window.create_confirmation_dialog = confirm
    with patch.dict("sys.modules", {"webview": view}):
        assert not api.git_metadata_authorize()["ok"]
    assert not calls
