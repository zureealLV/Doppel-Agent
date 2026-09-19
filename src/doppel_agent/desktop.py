"""Native window for the same loopback console used by the browser UI."""

from __future__ import annotations

import argparse
import ctypes
import sys
import threading
from pathlib import Path

from .web.server import ConsoleServer


def _apply_windows_rounding(window) -> None:
    """Ask modern DWM for native rounded corners without transparent padding."""
    if sys.platform != "win32":
        return
    try:
        handle = int(window.native.Handle.ToInt64())
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            handle, 33, ctypes.byref(preference), ctypes.sizeof(preference),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        # Older Windows versions keep a square, solid-color frameless window.
        pass


class WindowApi:
    def __init__(self) -> None:
        self.maximized = False

    def window_action(self, action: str) -> bool:
        import webview
        if not webview.windows:
            return False
        window = webview.windows[0]
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
        elif action == "close":
            window.destroy()
        else:
            raise ValueError("unknown window action")
        return True


def launch_desktop(workspace: Path) -> None:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("Desktop GUI requires: python -m pip install -e '.[desktop]'") from exc

    workspace = workspace.expanduser().resolve(strict=True)
    server = ConsoleServer(("127.0.0.1", 0), workspace)
    worker = threading.Thread(target=server.serve_forever, name="doppel-ui", daemon=True)
    worker.start()
    try:
        window = webview.create_window(
            "Doppel Agent",
            f"http://127.0.0.1:{server.server_port}/",
            width=1280,
            height=850,
            min_size=(900, 620),
            frameless=True,
            easy_drag=True,
            transparent=False,
            background_color="#1b1921",
            js_api=WindowApi(),
        )
        window.events.shown += _apply_windows_rounding
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))
        icon = bundle_root / "assets" / "doppel-agent.ico"
        webview.start(gui="edgechromium", icon=str(icon) if icon.is_file() else None)
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="doppel-desktop")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    launch_desktop(args.workspace)
