"""Project-configured, shell-free verification commands."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from types import MappingProxyType
from typing import Any, Sequence

from .process_supervisor import ProcessOutputLimitError, ProcessSupervisor, ProcessSupervisionError
from .verification_config import VerificationCommand, VerificationPlan, capture
from ..owned_async import await_durable


@dataclass(frozen=True)
class VerificationResult:
    name: str
    argv: tuple[str, ...]
    exit_code: int | None
    success: bool
    stdout: str
    stderr: str
    duration_ms: int
    supervision: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationReport:
    success: bool
    results: tuple[VerificationResult, ...]
    review: VerificationPlan | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"success": self.success, "results": [item.as_dict() for item in self.results]}
        if self.review is not None:
            result["review"] = self.review.as_dict()
        return result


class VerificationPipeline:
    """Execute only immutable argv entries selected from project configuration."""

    SAFE_ENV_NAMES = frozenset(
        {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "VIRTUAL_ENV",
            "UV_CACHE_DIR",
            "PYTHONUTF8",
        }
    )

    def __init__(
        self,
        workspace: Path,
        *,
        config_path: Path | None = None,
        supervisor: ProcessSupervisor | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.config_path = config_path or self.workspace / ".doppel" / "verification.json"
        if not self.config_path.is_absolute():
            self.config_path = self.workspace / self.config_path
        self.supervisor = supervisor if supervisor is not None else ProcessSupervisor()
        self._config = capture(self.workspace, self.config_path)
        self.commands = MappingProxyType({command.name: command for command in self._config.commands})
        self.max_output_bytes, self.stop_on_failure = self._config.max_output_bytes, self._config.stop_on_failure

    def _load(self) -> tuple[dict[str, VerificationCommand], int, bool]:
        config = capture(self.workspace, self.config_path)
        return {command.name: command for command in config.commands}, config.max_output_bytes, config.stop_on_failure

    def prepare(self, names: Sequence[str] | None = None) -> VerificationPlan:
        """Preview only; neither command grant nor operator approval is inferred."""
        if capture(self.workspace, self.config_path) != self._config:
            raise ValueError("verification_review_stale")
        return self._config.select(names)

    def _assert_review(self, plan: VerificationPlan) -> None:
        if type(plan) is not VerificationPlan:
            raise ValueError("verification_review_stale")
        fresh = capture(self.workspace, self.config_path)
        if fresh != self._config or plan != fresh.select(plan.names):
            raise ValueError("verification_review_stale")

    async def run_reviewed(self, plan: VerificationPlan, *, run_id: str, command_grant: bool,
                           on_result: Callable[[VerificationResult], Awaitable[None]] | None = None,
                           on_start: Callable[[VerificationCommand], Awaitable[None]] | None = None) -> VerificationReport:
        # The owned service must separately consume exact durable approval; this
        # foundation's boolean is NOT that operator review/owner/lease barrier.
        if command_grant is not True:
            raise PermissionError("verification_command_grant_required")
        self._assert_review(plan)
        return await self._run_plan(plan, run_id=run_id, reviewed=True, on_result=on_result, on_start=on_start)

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(self.commands)

    @classmethod
    def _safe_env(cls) -> dict[str, str]:
        # Named lookups only, never enumerate unrelated secret environment values.
        env = {name: value for name in cls.SAFE_ENV_NAMES if (value := os.environ.get(name)) is not None}
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    async def run(
        self,
        names: Sequence[str] | None = None,
        *,
        run_id: str,
    ) -> VerificationReport:
        selected = list(self.commands) if names is None else list(names)
        if not selected:
            raise ValueError("no verification commands selected or configured")
        if len(selected) != len(set(selected)) or any(name not in self.commands for name in selected):
            raise PermissionError("verification command is not in the project allowlist")
        # Compatibility/direct caller path is NOT operator-reviewed evidence.
        plan = self.prepare(selected)
        return await self._run_plan(plan, run_id=run_id, reviewed=False)

    async def _run_plan(self, plan: VerificationPlan, *, run_id: str, reviewed: bool,
                        on_result: Callable[[VerificationResult], Awaitable[None]] | None = None,
                        on_start: Callable[[VerificationCommand], Awaitable[None]] | None = None) -> VerificationReport:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128 or "\x00" in run_id:
            raise ValueError("verification_run_scope_unavailable")
        results: list[VerificationResult] = []
        for command in plan.commands:
            self._assert_review(plan)
            if on_start is not None:
                await await_durable(on_start(command))
                self._assert_review(plan)  # Config may change during durable intent IO.
            name = command.name
            started = monotonic()
            try:
                options = {"cwd": self.workspace, "run_id": run_id, "timeout_seconds": command.timeout_seconds,
                           "env": self._safe_env(), "output_limit": max(1, plan.config.max_output_bytes // 2)}
                if reviewed:
                    # Original supervisor, not a parallel command executor.
                    # Binary pipe caps fail on overflow, never text-spool/truncate.
                    process = await self.supervisor.run_binary(command.argv, **options, require_tree_ownership=True)
                    if len(process.stdout) + len(process.stderr) > plan.config.max_output_bytes:
                        raise ProcessOutputLimitError("verification total output budget exceeded")
                    # Explicit presentation conversion only, not lossless protocol
                    # evidence or an assertion that invalid bytes were UTF-8.
                    stdout = process.stdout.decode("utf-8", errors="replace")
                    stderr = process.stderr.decode("utf-8", errors="replace")
                else:
                    process = await self.supervisor.run(command.argv, **options)
                    stdout, stderr = process.stdout, process.stderr
            except (TimeoutError, ProcessOutputLimitError, ProcessSupervisionError) as exc:
                # Cleanup failure is a separate exception and MUST remain unknown;
                # these are only settled command-attempt/admission outcomes.
                error = ("verification_timeout" if isinstance(exc, TimeoutError) else
                         "verification_output_limit" if isinstance(exc, ProcessOutputLimitError) else
                         "verification_supervision_unavailable")
                result = VerificationResult(
                    name,
                    command.argv,
                    None,
                    False,
                    "",
                    "",
                    round((monotonic() - started) * 1000),
                    "unavailable" if isinstance(exc, ProcessSupervisionError) else "terminated",
                    error,
                )
            else:
                result = VerificationResult(
                    name,
                    command.argv,
                    process.exit_code,
                    process.exit_code == 0,
                    stdout,
                    stderr,
                    round((monotonic() - started) * 1000),
                    process.supervision,
                )
            results.append(result)
            if on_result is not None:
                # C2 seals each actual result independently before a later
                # config conflict/cancel/failure. Never claim persistence here.
                await await_durable(on_result(result))
            if not result.success and plan.config.stop_on_failure:
                break
        return VerificationReport(all(item.success for item in results), tuple(results), plan if reviewed else None)
