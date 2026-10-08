"""Native window for the same loopback console used by the browser UI."""

from __future__ import annotations

import argparse
import ctypes
import json
import socket
import sys
import threading
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from urllib.parse import urlsplit

from .web.server import ConsoleServer as ConsoleServer


# Chromium's default restricted-port list, checked 2026-10-04:
# https://raw.githubusercontent.com/chromium/chromium/main/net/base/port_util.cc
# Pick another OS-assigned loopback port; never disable WebView's restrictions.
_CHROMIUM_RESTRICTED_PORTS = frozenset({
    0, 1, 7, 9, 11, 13, 15, 17, 19, 20, 21, 22, 23, 25, 37, 42, 43, 53, 69, 77,
    79, 87, 95, 101, 102, 103, 104, 109, 110, 111, 113, 115, 117, 119, 123, 135,
    137, 139, 143, 161, 179, 389, 427, 465, 512, 513, 514, 515, 526, 530, 531,
    532, 540, 548, 554, 556, 563, 587, 601, 636, 989, 990, 993, 995, 1719, 1720,
    1723, 2049, 3659, 4045, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668,
    6669, 6697, 10080,
})


@dataclass
class _BrowserSocketLifetime:
    resource: object = None
    factory_attempted: bool = False
    factory_returned: bool = False
    usable: bool = False
    close_attempted: bool = False
    close_returned: bool = False
    cleanup_uncertain: bool = False


class BrowserSocketCleanupError(RuntimeError):
    def __init__(self, source):
        super().__init__('desktop listener allocation or cleanup unproved')
        self.source = source  # Exact original private candidate/attempt, never bridge/report payload.


def _close_original_browser_socket(source: _BrowserSocketLifetime) -> None:
    if source.close_attempted:
        if source.close_returned:
            return
        raise BrowserSocketCleanupError(source)
    if not source.usable:
        source.cleanup_uncertain = True
        raise BrowserSocketCleanupError(source)  # No invented close of opaque/unusable allocation.
    source.close_attempted = True  # BEFORE original close, including effect-then-throw.
    try:
        source.resource.close()
        source.close_returned = True  # Python close returned, not physical port/worker proof.
    except BaseException:
        source.cleanup_uncertain = True
        raise BrowserSocketCleanupError(source) from None


def _bind_browser_api_socket() -> socket.socket:
    """Keep the accepted socket bound, without a close/rebind port-selection race."""
    for _ in range(64):
        source = _BrowserSocketLifetime(factory_attempted=True)  # BEFORE original socket factory.
        try:
            candidate = source.resource = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            source.factory_returned = True
            if not all(callable(getattr(candidate, name, None))
                       for name in ('setsockopt', 'bind', 'getsockname', 'listen', 'close')):
                raise ValueError('original socket identity unavailable')
            source.usable = True
        except BaseException:
            source.cleanup_uncertain = True  # Opaque/unusable allocation isn't no-resource authority.
            raise BrowserSocketCleanupError(source) from None
        try:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.bind(("127.0.0.1", 0))
            address = candidate.getsockname()
            if (type(address) is not tuple or len(address) != 2 or address[0] != '127.0.0.1'
                    or type(address[1]) is not int or not 1 <= address[1] <= 65535):
                raise ValueError('original loopback listener address unavailable')
            restricted = address[1] in _CHROMIUM_RESTRICTED_PORTS
            if not restricted:
                candidate.listen(128)
        except BaseException:
            _close_original_browser_socket(source)
            raise
        if restricted:
            # Outside the setup handler: failed close is never attempted again.
            _close_original_browser_socket(source)
            continue
        return candidate
    raise RuntimeError("Could not allocate a browser-compatible loopback port")


def _apply_windows_rounding(window) -> None:
    """Ask modern DWM for native rounded corners without transparent padding."""
    if sys.platform != "win32":
        return
    try:
        handle = int(window.native.Handle.ToInt64())
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            handle,
            33,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        # Older Windows versions keep a square, solid-color frameless window.
        pass


@dataclass
class _NativeWindowLifetime:
    window: object
    destroy_attempted: bool = False
    destroy_returned: bool = False
    destroy_uncertain: bool = False
    closed_observed: threading.Event = field(default_factory=threading.Event)


class WindowApi:
    def __init__(self, kernel=None, catalog_path: Path | None = None) -> None:
        self.maximized = False
        self._kernel = kernel
        self._catalog_path = catalog_path
        self._switcher = None
        self._catalog = None
        self._catalog_failed = threading.Event()
        self._unresolved_catalog_sources: dict[int, object] = {}
        self._action_lock = threading.Lock()
        self._closing = threading.Event()
        self._projects_closed = threading.Event()
        self._window_lifetime: _NativeWindowLifetime | None = None
        self._project_origin = kernel.url if kernel is not None else None

    def _bind_native_window(self, window) -> None:
        if self._window_lifetime is None:
            self._window_lifetime = _NativeWindowLifetime(window)
        elif self._window_lifetime.window is not window:
            raise RuntimeError('original native window identity changed')

    def _native_closed(self, window) -> None:
        # SDK event passes its original window by this parameter name. It may
        # arrive on another thread; never wait on the JS action/cleanup lock.
        source = self._window_lifetime
        if source is not None and source.window is window:
            self._closing.set()  # Fence controls; unexpected closed event never implies project cleanup.
            source.closed_observed.set()  # Event observation, not worker/port/owner proof.

    def _trusted_project_window(self):
        import webview

        if self._closing.is_set():
            raise RuntimeError('desktop is closing')
        if self._catalog_failed.is_set():
            raise RuntimeError('native project catalog is unavailable')
        if not webview.windows or self._project_origin is None:
            raise RuntimeError("native project management is unavailable")
        window = webview.windows[0]
        current = urlsplit(window.get_current_url() or "")
        expected = urlsplit(self._project_origin)
        if current.username or current.password or (current.scheme, current.hostname, current.port) != (
            expected.scheme, expected.hostname, expected.port
        ):
            raise RuntimeError("native project origin is not trusted")
        return window

    def _projects(self):
        from .projects.catalog import ProjectCatalog, default_catalog_path
        from .projects.desktop_kernel import DesktopKernel
        from .projects.switching import ProjectSwitcher

        if self._kernel is None:
            raise RuntimeError("native project management is unavailable")
        if self._switcher is None:
            catalog = ProjectCatalog(self._catalog_path or default_catalog_path(),
                                     failure=self._mark_catalog_failed,
                                     cleanup_failure=self._retain_catalog_cleanup)
            self._catalog = catalog  # Original source BEFORE registration/mark_opened can fail.
            record = catalog.register(self._kernel.workspace)
            catalog.mark_opened(record["project_id"])
            self._switcher = ProjectSwitcher(catalog, self._kernel, DesktopKernel)
        return self._switcher

    def _mark_catalog_failed(self) -> None:
        if self._catalog_failed.is_set():
            return
        self._catalog_failed.set()
        kernel = self._switcher.kernel if self._switcher is not None else self._kernel
        callback = getattr(kernel, '_mark_legacy_uncertain', None)
        if callback is not None:
            callback()  # SAME original selected kernel's passive provider admission latch, no IO.

    def _retain_catalog_cleanup(self, source) -> None:
        self._unresolved_catalog_sources[id(source)] = source
        self._mark_catalog_failed()
        kernel = self._switcher.kernel if self._switcher is not None else self._kernel
        callback = getattr(kernel, '_retain_project_catalog_cleanup', None)
        if callback is not None:
            callback(source)  # Failed constructor also retained before public catalog assignment.

    def project_list(self) -> dict:
        with self._action_lock:
            try:
                self._trusted_project_window()
                switcher = self._projects()
                return {"ok": True, "projects": switcher.catalog.list()}
            except Exception:
                return {"ok": False, "error": "recent projects are unavailable"}

    def project_choose(self) -> dict:
        import webview

        if not self._action_lock.acquire(blocking=False):
            return {"ok": False, "error": "project operation is busy"}
        try:
            window = self._trusted_project_window()
            switcher = self._projects()
            selected = window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            self._trusted_project_window()
            if not selected:
                return {"ok": True, "cancelled": True}
            if not isinstance(selected, (list, tuple)) or len(selected) != 1 or not isinstance(selected[0], str):
                raise ValueError("invalid folder selection")
            return {"ok": True, "project": switcher.catalog.register(selected[0])}
        except Exception:
            return {"ok": False, "error": "select an existing local project directory"}
        finally:
            self._action_lock.release()

    def project_switch(self, project_id: str, cancel_and_drain: bool = False) -> dict:
        # Admission requires an explicit UI decision, not merely picking a folder.
        if cancel_and_drain is not True:
            return {"ok": False, "error": "confirm cancellation and cleanup before switching"}
        if not self._action_lock.acquire(blocking=False):
            return {"ok": False, "error": "project operation is busy"}
        try:
            self._trusted_project_window()
            result = self._projects().switch(project_id)
            if result.get("url"):
                self._project_origin = result["url"]
            return result
        except Exception:
            return {"ok": False, "error": "project switch is unavailable"}
        finally:
            self._action_lock.release()

    def _close_projects(self) -> None:
        self._closing.set()
        with self._action_lock:
            self._close_projects_original()

    def _close_projects_original(self) -> None:
        if self._projects_closed.is_set():
            return
        if self._switcher is not None:
            self._switcher.close()
        elif self._kernel is not None:
            self._kernel.close()
        # Kernel holds the same source too. Never infer unresolved catalog/SQL
        # disposal from a returned mock/foreign kernel close alone.
        for source in self._unresolved_catalog_sources.values():
            source.check_resource_cleanup()
        self._projects_closed.set()  # Original close returned; not OS window-destroy proof.

    def _native_closing(self) -> bool:
        # WinForms closing is synchronous; False vetoes actual FormClosing.
        # Do not wait for a JS action lock: destroy can synchronously reenter here.
        if self._projects_closed.is_set():
            return True
        self._closing.set()
        if not self._action_lock.acquire(blocking=False):
            return False
        try:
            self._close_projects_original()
            return True
        except BaseException:
            return False  # Leave original window/source/owner alive; no unhandled event exception.
        finally:
            self._action_lock.release()

    def _current_git_kernel(self):
        kernel = self._switcher.kernel if self._switcher is not None else self._kernel
        if kernel is None or not kernel.ready:
            raise RuntimeError("native_git_metadata_unavailable")
        return kernel

    def _recheck_git_window(self, window, kernel):
        if self._trusted_project_window() is not window or self._current_git_kernel() is not kernel:
            raise RuntimeError("native_git_metadata_unavailable")

    def git_metadata_authorize(self) -> dict:
        # No browser path/bool parameter can authorize external metadata. The
        # actual selected native directory identity is reviewed in a native dialog.
        import webview
        from .workspace.git_authorization import select_metadata_root

        if not self._action_lock.acquire(blocking=False):
            return {"ok": False, "error": "native_git_metadata_busy"}
        try:
            window = self._trusted_project_window()
            kernel = self._current_git_kernel()
            chosen = window.create_file_dialog(webview.FileDialog.FOLDER, allow_multiple=False)
            self._recheck_git_window(window, kernel)
            if not chosen:
                return {"ok": True, "cancelled": True}
            if not isinstance(chosen, (list, tuple)) or len(chosen) != 1 or not isinstance(chosen[0], str):
                raise ValueError("invalid_native_git_metadata_selection")
            selected = select_metadata_root(chosen[0], workspace=kernel.workspace)  # Directory checks ONLY before confirmation.
            # Escape controls/bidi/ambiguous path glyphs in the security prompt.
            workspace_label = json.dumps(str(kernel.workspace), ensure_ascii=True)
            root_label = json.dumps(str(selected.path), ensure_ascii=True)
            confirmed = window.create_confirmation_dialog("Git metadata read authorization",
                f"Current project: {workspace_label}\nMetadata directory: {root_label}\n\n"
                "Allow read-only inspection for this project's exact linked worktree?\n"
                "May read refs, index and object contents. Does not grant Git writes, arbitrary commands, "
                "config/hooks/credentials, other projects or network access.\n"
                "Not saved; ends on revoke, pointer/identity change, project switch or close.")
            self._recheck_git_window(window, kernel)
            if confirmed is not True:
                return {"ok": True, "cancelled": True}
            return {"ok": True, "authorization": kernel._native_git_metadata("grant", selected)}
        except Exception:
            return {"ok": False, "error": "native_git_metadata_unavailable"}
        finally:
            self._action_lock.release()

    def _git_metadata_control(self, action):
        if not self._action_lock.acquire(blocking=False):
            return {"ok": False, "error": "native_git_metadata_busy"}
        try:
            self._trusted_project_window()
            kernel = self._current_git_kernel()
            return {"ok": True, "authorization": kernel._native_git_metadata(action)}
        except Exception:
            return {"ok": False, "error": "native_git_metadata_unavailable"}
        finally:
            self._action_lock.release()

    def git_metadata_authorization(self) -> dict:
        return self._git_metadata_control("read")

    def git_metadata_revoke(self) -> dict:
        return self._git_metadata_control("revoke")

    def window_action(self, action: str) -> bool:
        if action not in {'minimize', 'maximize', 'restore', 'close'}:
            raise ValueError('unknown window action')
        if self._closing.is_set() and action != 'close':
            return False
        if not self._action_lock.acquire(blocking=False):
            return False
        try:
            return self._window_action(action)
        finally:
            self._action_lock.release()

    def _window_action(self, action: str) -> bool:
        import webview

        if self._window_lifetime is None:
            if not webview.windows:
                return False
            self._bind_native_window(webview.windows[0])
        source = self._window_lifetime
        window = source.window  # Never replace original identity from a changed global list.
        if action == "minimize":
            window.minimize()
        elif action == "maximize":
            if self.maximized:
                window.restore()
            else:
                window.maximize()
            self.maximized = not self.maximized
        elif action == "restore":
            window.restore()
            self.maximized = False
        elif action == "close":
            self._closing.set()
            try:
                self._close_projects_original()
            except BaseException:
                return False  # Never destroy while original cleanup is unproved.
            if source.destroy_attempted:
                return source.destroy_returned and not source.destroy_uncertain
            if source.closed_observed.is_set():
                return True  # Same original event already observed; no new destroy effect.
            source.destroy_attempted = True  # BEFORE original invocation, including lost return.
            try:
                window.destroy()
                source.destroy_returned = True  # Request returned, NOT physical window closure.
            except BaseException:
                source.destroy_uncertain = True
                return False  # Exact original window/attempt retained; fixed bridge result, no retry.
        else:
            raise ValueError("unknown window action")
        return True


def launch_desktop(workspace: Path, *, project_catalog: Path | None = None) -> None:
    try:
        import webview
        import_module("uvicorn")
    except ImportError as exc:
        raise RuntimeError("Desktop GUI requires: python -m pip install -e '.[agent,desktop]'") from exc

    from .projects.desktop_kernel import DesktopKernel

    workspace = workspace.expanduser().resolve(strict=True)
    kernel = DesktopKernel(workspace)
    kernel.start()
    host = None
    try:
        host = WindowApi(kernel, catalog_path=project_catalog)
        window = webview.create_window(
            "Doppel Agent",
            kernel.url,
            width=1280,
            height=850,
            min_size=(900, 620),
            frameless=True,
            easy_drag=True,
            transparent=False,
            background_color="#1b1921",
            js_api=host,
        )
        host._bind_native_window(window)
        window.events.shown += _apply_windows_rounding
        window.events.closing += host._native_closing
        window.events.closed += host._native_closed
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))
        icon = bundle_root / "assets" / "doppel-agent.ico"
        webview.start(gui="edgechromium", icon=str(icon) if icon.is_file() else None)
    finally:
        if host is None:
            kernel.close()  # Host constructor failure still owns the SAME started kernel.
        else:
            host._close_projects()


def main() -> None:
    parser = argparse.ArgumentParser(prog="doppel-desktop")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--project-catalog", type=Path, default=None,
                        help="Override the app-owned recent-project catalog (isolated acceptance)")
    args = parser.parse_args()
    catalog = args.project_catalog.expanduser().resolve() if args.project_catalog else None
    launch_desktop(args.workspace, project_catalog=catalog)
