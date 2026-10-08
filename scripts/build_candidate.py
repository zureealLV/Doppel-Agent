"""Build an isolated, explicitly untested Local Mode candidate. No app launch."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def inventory(directory: Path) -> dict[str, dict[str, object]]:
    if not directory.exists():
        return {}
    return {
        p.relative_to(directory).as_posix(): {
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "mtime_ns": p.stat().st_mtime_ns,
        }
        for p in sorted(directory.rglob("*")) if p.is_file()
    }


def source_inputs() -> dict[str, str]:
    files = []
    for name in ("src", "frontend/src", "scripts", "assets"):
        files.extend(p for p in (ROOT / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts
                     and not any(part.endswith(".egg-info") for part in p.parts)
                     and p.suffix not in {".pyc", ".pyo"})
    files.extend(ROOT / name for name in (
        "pyproject.toml", "uv.lock", "README.md", "README_CN.md",
        "frontend/package.json", "frontend/package-lock.json",
        "frontend/vite.config.ts", "frontend/index.html",
        "frontend/tsconfig.json", "frontend/tsconfig.app.json",
        "frontend/tsconfig.node.json",
    ) if (ROOT / name).is_file())
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(files))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True,
                        help="New directory beneath this checkout's .artifacts")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output = output.resolve()
    artifact_root = (ROOT / ".artifacts").resolve()
    if output == artifact_root or not output.is_relative_to(artifact_root):
        parser.error("output must be a new child of .artifacts; daily builds are forbidden")
    if output.exists():
        parser.error("output already exists; preserve previous build attempts")
    python = ROOT / ".venv/Scripts/python.exe"
    npm = shutil.which("npm.cmd")
    uv = shutil.which("uv")
    if not python.is_file() or not npm or not uv:
        parser.error("existing .venv, npm.cmd and uv are required; no automatic downloads")
    output.mkdir(parents=True)
    logs = output / "logs"
    logs.mkdir()
    env = os.environ.copy()
    for name in list(env):
        upper = name.upper()
        if any(term in upper for term in ("API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN",
                                          "SECRET", "PASSWORD")) or upper in {
            "PYTHONPATH", "PYTHONHOME", "OPENAI_BASE_URL", "OPENAI_API_BASE",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        }:
            del env[name]
    env.update(PYTHONUTF8="1", NO_PROXY="127.0.0.1,localhost,::1",
               LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    manifest: dict[str, object] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "kind": "build-only candidate",
        "version": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "tests_executed": False, "native_acceptance": False,
        "release_accepted": False, "app_launched": False,
        "route_acceptance_deferred": True, "dependencies_downloaded": False,
        "source_inputs": {}, "steps": [], "build_success": False,
    }
    daily = inventory(ROOT / ".dist/DoppelAgent")

    def save() -> None:
        (output / "build-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run(name: str, command: list[str], cwd: Path = ROOT) -> None:
        print(f"Building: {name}", flush=True)
        row = {"name": name, "command": command, "cwd": str(cwd),
               "started_at": datetime.now(timezone.utc).isoformat()}
        manifest["steps"].append(row)
        save()
        with (logs / f"{name}.log").open("wb") as log:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, check=False)
        row.update(exit_code=result.returncode,
                   finished_at=datetime.now(timezone.utc).isoformat())
        save()
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {logs / (name + '.log')}")

    try:
        manifest["toolchain"] = {
            "python": sys.version, "pyinstaller": metadata.version("pyinstaller"),
            "pyinstaller_hooks": metadata.version("pyinstaller-hooks-contrib"),
            "setuptools": metadata.version("setuptools"),
        }
        manifest["frontend_build_note"] = "vue-tsc compiler checks + Vite; no Vitest"
        run("frontend", [npm, "run", "build"], ROOT / "frontend")
        frozen = source_inputs()
        manifest["source_inputs"] = frozen
        manifest["source_sha256"] = hashlib.sha256(
            json.dumps(frozen, sort_keys=True).encode("utf-8")).hexdigest()
        run("packages", [uv, "build", "--offline", "--no-build-isolation",
                         "--out-dir", str(output / "packages")])
        icon = ROOT / "assets/doppel-agent.ico"
        run("desktop", [str(python), "-m", "PyInstaller", "--noconfirm", "--windowed",
                        "--onedir", "--name", "DoppelAgent", "--icon", str(icon),
                        "--add-data", f"{icon};assets", "--collect-data", "doppel_agent.web",
                        "--distpath", str(output / "desktop"),
                        "--workpath", str(output / "pyinstaller-work"),
                        "--specpath", str(output / "spec"),
                        str(ROOT / "scripts/desktop_entry.py")])
        if source_inputs() != frozen:
            raise RuntimeError("source inputs changed during packaging")
        packages = list((output / "packages").glob("*.whl")) + list(
            (output / "packages").glob("*.tar.gz"))
        exe = output / "desktop/DoppelAgent/DoppelAgent.exe"
        if len(packages) != 2 or not exe.is_file():
            raise RuntimeError("required wheel/sdist/EXE build output missing")
        manifest["deliverables"] = {
            str(p.relative_to(output)): {"bytes": p.stat().st_size,
                                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in [*packages, exe]
        }
        manifest["frontend_assets"] = inventory(ROOT / "src/doppel_agent/web/frontend_dist")
        manifest["build_success"] = True
    except Exception as exc:
        manifest["failure_class"] = type(exc).__name__
        print(str(exc), file=sys.stderr)
    finally:
        manifest["daily_app_unchanged"] = inventory(ROOT / ".dist/DoppelAgent") == daily
        if not manifest["daily_app_unchanged"]:
            manifest["build_success"] = False
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    print(f"Build manifest: {output / 'build-manifest.json'}", flush=True)
    return 0 if manifest["build_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
