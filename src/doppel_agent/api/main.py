"""Command-line entrypoint for the ASGI API."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    import uvicorn

    from .app import create_app

    parser = argparse.ArgumentParser(prog="doppel-api")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(args.workspace), host=args.host, port=args.port)
