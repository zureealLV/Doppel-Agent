"""Native window for the same loopback console used by the browser UI."""

from __future__ import annotations

import argparse
import ctypes
import socket
import sys
import threading
import time
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
            handle,
            33,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
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
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Desktop GUI requires: python -m pip install -e '.[agent,desktop]'") from exc

    from .api import create_app

    workspace = workspace.expanduser().resolve(strict=True)
    legacy = ConsoleServer(("127.0.0.1", 0), workspace)
    legacy_worker = threading.Thread(target=legacy.serve_forever, name="doppel-legacy-ui", daemon=True)
    legacy_worker.start()
    app = create_app(
        workspace,
        legacy_base_url=f"http://127.0.0.1:{legacy.server_port}",
    )
    api_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    api_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    api_socket.bind(("127.0.0.1", 0))
    api_socket.listen(128)
    api_port = api_socket.getsockname()[1]
    api = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on"))
    api_worker = threading.Thread(
        target=api.run,
        kwargs={"sockets": [api_socket]},
        name="doppel-api",
        daemon=True,
    )
    api_worker.start()
    for _ in range(100):
        if api.started or not api_worker.is_alive():
            break
        time.sleep(0.02)
    if not api.started:
        legacy.shutdown()
        legacy.server_close()
        raise RuntimeError("Doppel runtime API failed to start")
    try:
        window = webview.create_window(
            "Doppel Agent",
            f"http://127.0.0.1:{api_port}/",
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
        api.should_exit = True
        api_worker.join(timeout=10)
        api_socket.close()
        legacy.shutdown()
        legacy_worker.join(timeout=5)
        legacy.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="doppel-desktop")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    launch_desktop(args.workspace)
