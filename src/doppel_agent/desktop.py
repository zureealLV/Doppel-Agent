"""Native window for the same loopback console used by the browser UI."""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from .web.server import ConsoleServer


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
        webview.create_window(
            "Doppel Agent",
            f"http://127.0.0.1:{server.server_port}/",
            width=1280,
            height=850,
            min_size=(900, 620),
        )
        webview.start(gui="edgechromium")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="doppel-desktop")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    launch_desktop(args.workspace)
