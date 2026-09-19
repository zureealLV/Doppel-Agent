"""Run a blind, read-only code-review case; never persist API credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from doppel_agent.core import Core  # noqa: E402
from doppel_agent.provider import OpenAICompatibleProvider, Provider  # noqa: E402


CASE_ID = "review_001"
SOURCE = ROOT / "bench" / "cases" / CASE_ID / "service.py"
PROMPT = (
    "对工作区中的 service.py 进行只读代码审核。请先调用工具读取文件。"
    "报告最多 5 个有证据支持、会造成实际后果的问题；每条给出严重度、"
    "准确的 service.py:行号、触发条件、影响和最小修复建议。"
    "不要修改文件或运行命令；如果某段代码没有问题，不要为了凑数而报错。"
)


def read_key(env_file: Path | None) -> str:
    if env_file is None:
        return os.environ.get("DEEPSEEK_API_KEY", "")
    if not env_file.is_file():
        raise ValueError("env file does not exist")
    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line and line.startswith("sk-"):
            return line
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip().removeprefix("export ").strip() == "DEEPSEEK_API_KEY":
            return value.strip().strip('"\'')
    return ""


def run_case(provider: Provider, model: str, output_dir: Path, *, prompt: str = PROMPT) -> tuple[Path, dict]:
    scratch = ROOT / ".test-tmp"
    scratch.mkdir(exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = scratch / f"{CASE_ID}-{uuid4().hex}"
    workspace.mkdir()
    try:
        shutil.copy2(SOURCE, workspace / "service.py")
        result = Core(workspace, provider).run(prompt)
        event_file = workspace / ".doppel-agent" / "runs" / result["run_id"] / "events.jsonl"
        events = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]
        report = {
            "case_id": CASE_ID,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "fixture_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "prompt": prompt,
            "status": result["status"],
            "run_id": result["run_id"],
            "answer": result["answer"],
            "tool_requests": [event["payload"] for event in events if event["kind"] == "tool_requested"],
            "tool_failures": [event["payload"] for event in events if event["kind"] == "tool_failed"],
            "usage": [event["payload"] for event in events if event["kind"] == "model_usage"],
            "evaluation": "pending_human_adjudication",
        }
    finally:
        shutil.rmtree(workspace)
    path = output_dir / f"{CASE_ID}_{report['run_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Blind read-only code-review evaluation")
    parser.add_argument("--env-file", type=Path, help="optional .env file containing DEEPSEEK_API_KEY")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".bench-results")
    args = parser.parse_args()
    key = read_key(args.env_file)
    if not key:
        parser.error("DEEPSEEK_API_KEY is not configured; it will not be printed")
    provider = OpenAICompatibleProvider(args.base_url, args.model, key, timeout=120)
    path, report = run_case(provider, args.model, args.output_dir)
    print(json.dumps({
        "report": str(path), "status": report["status"],
        "inspected_target": any(
            call["name"] == "read_file" and call["arguments"].get("path") == "service.py"
            for call in report["tool_requests"]
        ),
        "total_tokens": sum(item.get("total_tokens", 0) for item in report["usage"]),
        "evaluation": report["evaluation"],
    }, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
