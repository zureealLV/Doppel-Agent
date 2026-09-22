"""Deterministic local load probe for scheduler and workspace-lock invariants."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from doppel_agent.concurrency import AsyncRunScheduler, QueueCapacityError, WorkspaceLockManager


async def _burst(total: int, max_active: int, delay_seconds: float) -> dict[str, Any]:
    scheduler = AsyncRunScheduler(max_active=max_active, queue_capacity=total)
    await scheduler.start()
    active = 0
    observed_peak = 0
    lock = asyncio.Lock()

    async def operation(token) -> None:
        nonlocal active, observed_peak
        token.raise_if_cancelled()
        async with lock:
            active += 1
            observed_peak = max(observed_peak, active)
        await asyncio.sleep(delay_seconds)
        async with lock:
            active -= 1

    started = perf_counter()
    handles = [await scheduler.submit(f"burst-{index}", operation) for index in range(total)]
    await asyncio.gather(*(handle.future for handle in handles))
    elapsed = perf_counter() - started
    result = {
        "submitted": total,
        "completed": total,
        "configured_max_active": max_active,
        "observed_peak_active": observed_peak,
        "scheduler_peak_active": scheduler.peak_active_count,
        "wall_seconds": round(elapsed, 6),
        "passed": observed_peak <= max_active and scheduler.peak_active_count <= max_active,
    }
    await scheduler.shutdown()
    return result


async def _queue_rejection() -> dict[str, Any]:
    scheduler = AsyncRunScheduler(max_active=1, queue_capacity=1)
    await scheduler.start()
    gate = asyncio.Event()

    async def operation(token) -> None:
        token.raise_if_cancelled()
        await gate.wait()

    first = await scheduler.submit("queue-running", operation)
    while scheduler.active_count != 1:
        await asyncio.sleep(0)
    second = await scheduler.submit("queue-waiting", operation)
    rejected = False
    try:
        await scheduler.submit("queue-rejected", operation)
    except QueueCapacityError:
        rejected = True
    gate.set()
    await asyncio.gather(first.future, second.future)
    result = {
        "accepted": scheduler.accepted_count,
        "rejected": scheduler.rejected_count,
        "explicit_queue_full": rejected,
        "passed": rejected and scheduler.rejected_count == 1,
    }
    await scheduler.shutdown()
    return result


async def _workspace_contention(workspace: Path) -> dict[str, Any]:
    manager = WorkspaceLockManager()
    active_readers = 0
    active_writers = 0
    peak_readers = 0
    peak_writers = 0
    overlap_violations = 0

    async def reader() -> None:
        nonlocal active_readers, peak_readers, overlap_violations
        async with manager.read(workspace):
            active_readers += 1
            peak_readers = max(peak_readers, active_readers)
            if active_writers:
                overlap_violations += 1
            await asyncio.sleep(0.004)
            active_readers -= 1

    async def writer() -> None:
        nonlocal active_writers, peak_writers, overlap_violations
        async with manager.write(workspace):
            active_writers += 1
            peak_writers = max(peak_writers, active_writers)
            if active_readers or active_writers > 1:
                overlap_violations += 1
            await asyncio.sleep(0.004)
            active_writers -= 1

    operations = [reader() for _ in range(20)] + [writer() for _ in range(5)]
    await asyncio.gather(*operations)
    return {
        "read_operations": 20,
        "write_operations": 5,
        "peak_readers": peak_readers,
        "peak_writers": peak_writers,
        "read_write_overlap_violations": overlap_violations,
        "passed": peak_writers == 1 and overlap_violations == 0,
    }


async def _cancellation() -> dict[str, Any]:
    scheduler = AsyncRunScheduler(max_active=1, queue_capacity=2)
    await scheduler.start()
    started = asyncio.Event()

    async def operation(token) -> None:
        started.set()
        await asyncio.sleep(30)

    handle = await scheduler.submit("cancel-running", operation)
    await started.wait()
    before = perf_counter()
    accepted = await scheduler.cancel(handle.run_id)
    try:
        await handle.future
    except asyncio.CancelledError:
        pass
    latency = perf_counter() - before
    result = {
        "cancel_accepted": accepted,
        "final_status": scheduler.status(handle.run_id),
        "latency_seconds": round(latency, 6),
        "passed": accepted and scheduler.status(handle.run_id) == "cancelled",
    }
    await scheduler.shutdown()
    return result


async def run_load_benchmark(workspace: Path, *, total: int = 100) -> dict[str, Any]:
    scenarios = {
        "bounded_burst": await _burst(total, 4, 0.005),
        "queue_rejection": await _queue_rejection(),
        "workspace_contention": await _workspace_contention(workspace),
        "running_cancellation": await _cancellation(),
    }
    return {
        "schema_version": "1.0",
        "recorded_at": datetime.now(UTC).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "configuration": {"burst_jobs": total, "max_active": 4, "queue_capacity": total},
        "scenarios": scenarios,
        "passed": all(item["passed"] for item in scenarios.values()),
        "scope_note": (
            "Local deterministic scheduler/lock probe; it is not a provider throughput or "
            "model-quality benchmark."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Doppel local concurrency probes")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--jobs", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    report = asyncio.run(run_load_benchmark(args.workspace.resolve(strict=True), total=args.jobs))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
