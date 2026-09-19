"""Run read-only operational reviews against real local projects.

This is an integration smoke test, not a correctness benchmark: reports keep the
raw model answer and tool trace for later human adjudication.  Agent state is
written under ``.bench-results`` so the reviewed projects remain untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:  # Supports both ``python bench/run_project_reviews.py`` and module imports.
    from bench.run_review import read_key  # type: ignore  # noqa: E402
except ModuleNotFoundError:
    from run_review import read_key  # type: ignore  # noqa: E402
from doppel_agent.core import Core  # noqa: E402
from doppel_agent.provider import OpenAICompatibleProvider  # noqa: E402


DEFAULT_PROJECTS = (
    ROOT,
    Path(r"D:\Codex Program files\medops-rag"),
    Path(r"D:\Codex Program files\Work Finder"),
    Path(r"D:\Codex Program files\LangChainCoding"),
)

PROMPT = (
    "请对这个项目执行只读代码审核。先用 list_files 查看根目录，再读取 README、配置和源代码来理解项目；"
    "不要读取 .env、credentials、私钥、构建产物、依赖目录或 .git，也不要修改文件或运行命令。"
    "最多读取 10 个文件，并在完成检查后立即给出结论。"
    "最多报告 3 个经过源码证实、会造成实际后果的问题。每项必须给出严重度、准确文件路径和代码证据、"
    "触发条件、影响、最小修复建议；证据不足就明确说未发现，不要凑数。"
)


def _slug(path: Path) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", path.name).strip("-.").lower()
    return value or "project"


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_projects(
    projects: list[Path], provider: OpenAICompatibleProvider, model: str, output_dir: Path,
) -> tuple[Path, dict]:
    timestamp = datetime.now(timezone.utc)
    batch = timestamp.strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict] = []

    for index, raw_project in enumerate(projects, start=1):
        project = raw_project.resolve(strict=True)
        state_root = (output_dir / "state" / f"{batch}-{index:02d}-{_slug(project)}").resolve()
        started = time.perf_counter()
        result = Core(project, provider, state_root=state_root, max_steps=12).run(PROMPT)
        duration = round(time.perf_counter() - started, 3)
        events = _events(state_root / "runs" / result["run_id"] / "events.jsonl")
        requests = [event["payload"] for event in events if event["kind"] == "tool_requested"]
        failures = [event["payload"] for event in events if event["kind"] == "tool_failed"]
        usage = [event["payload"] for event in events if event["kind"] == "model_usage"]
        read_paths = [
            item["arguments"]["path"] for item in requests
            if item.get("name") == "read_file" and isinstance(item.get("arguments", {}).get("path"), str)
        ]
        reports.append({
            "project": str(project),
            "status": result["status"],
            "run_id": result["run_id"],
            "duration_seconds": duration,
            "answer": result["answer"],
            "tool_requests": requests,
            "tool_failures": failures,
            "read_paths": read_paths,
            "usage": usage,
            "evaluation": "pending_human_adjudication",
        })

    summary = {
        "kind": "read_only_real_project_operational_smoke",
        "timestamp_utc": timestamp.isoformat(),
        "model": model,
        "prompt": PROMPT,
        "projects_requested": len(projects),
        "projects_completed": sum(item["status"] == "completed" for item in reports),
        "projects_with_source_reads": sum(bool(item["read_paths"]) for item in reports),
        "tool_failures": sum(len(item["tool_failures"]) for item in reports),
        "total_tokens": sum(
            usage.get("total_tokens", 0)
            for item in reports for usage in item["usage"]
            if isinstance(usage.get("total_tokens", 0), int)
        ),
        "evaluation": "operational_only_answers_require_human_adjudication",
        "reports": reports,
    }
    path = output_dir / f"project_reviews_{batch}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only operational review of real local projects")
    parser.add_argument("projects", nargs="*", type=Path, help="project directories (defaults to four curated projects)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".bench-results")
    args = parser.parse_args()
    projects = args.projects or list(DEFAULT_PROJECTS)
    missing = [str(path) for path in projects if not path.is_dir()]
    if missing:
        parser.error("project directory not found: " + ", ".join(missing))
    key = read_key(args.env_file)
    if not key:
        parser.error("DEEPSEEK_API_KEY is not configured; it will not be printed")

    provider = OpenAICompatibleProvider(args.base_url, args.model, key, timeout=120)
    path, summary = run_projects(projects, provider, args.model, args.output_dir)
    print(json.dumps({
        "report": str(path),
        "completed": summary["projects_completed"],
        "requested": summary["projects_requested"],
        "source_reads": summary["projects_with_source_reads"],
        "tool_failures": summary["tool_failures"],
        "total_tokens": summary["total_tokens"],
        "evaluation": summary["evaluation"],
    }, ensure_ascii=False))
    return 0 if summary["projects_completed"] == summary["projects_requested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
