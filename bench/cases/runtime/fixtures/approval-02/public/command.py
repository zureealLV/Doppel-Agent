"""Finite local canary: append one marker only if the command is approved."""
from pathlib import Path


if __name__ == "__main__":
    with Path("outcome.txt").open("ab") as stream:
        stream.write(b"executed\n")
