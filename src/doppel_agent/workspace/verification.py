"""Project-configured, shell-free verification commands."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Sequence

from .process_supervisor import ProcessSupervisor


@dataclass(frozen=True)
class VerificationCommand:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float


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

    def as_dict(self) -> dict[str, Any]:
        return {"success": self.success, "results": [item.as_dict() for item in self.results]}


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
        self.supervisor = supervisor or ProcessSupervisor()
        self.commands, self.max_output_bytes, self.stop_on_failure = self._load()

    def _load(self) -> tuple[dict[str, VerificationCommand], int, bool]:
        if not self.config_path.is_file():
            return {}, 64 * 1024, True
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {
            "commands",
            "max_output_bytes",
            "stop_on_failure",
        }:
            raise ValueError("verification config contains unsupported fields")
        raw_commands = data.get("commands")
        if not isinstance(raw_commands, list):
            raise ValueError("verification commands must be a list")
        commands: dict[str, VerificationCommand] = {}
        for raw in raw_commands:
            if not isinstance(raw, dict) or set(raw) != {"name", "argv", "timeout_seconds"}:
                raise ValueError("each verification command requires name, argv and timeout_seconds")
            name, argv, timeout = raw["name"], raw["argv"], raw["timeout_seconds"]
            if (
                not isinstance(name, str)
                or not name
                or name in commands
                or not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item and "\x00" not in item for item in argv)
                or not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or not 0 < timeout <= 600
            ):
                raise ValueError("invalid verification command")
            commands[name] = VerificationCommand(name, tuple(argv), float(timeout))
        output_limit = data.get("max_output_bytes", 64 * 1024)
        if (
            not isinstance(output_limit, int)
            or isinstance(output_limit, bool)
            or not 1 <= output_limit <= 1024 * 1024
        ):
            raise ValueError("max_output_bytes must be between 1 and 1048576")
        stop = data.get("stop_on_failure", True)
        if not isinstance(stop, bool):
            raise ValueError("stop_on_failure must be boolean")
        return commands, output_limit, stop

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(self.commands)

    @classmethod
    def _safe_env(cls) -> dict[str, str]:
        env = {
            name: value for name, value in os.environ.items() if name.upper() in cls.SAFE_ENV_NAMES
        }
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
        results: list[VerificationResult] = []
        for name in selected:
            command = self.commands[name]
            started = monotonic()
            try:
                process = await self.supervisor.run(
                    command.argv,
                    cwd=self.workspace,
                    run_id=run_id,
                    timeout_seconds=command.timeout_seconds,
                    env=self._safe_env(),
                    output_limit=max(1, self.max_output_bytes // 2),
                )
            except TimeoutError as exc:
                result = VerificationResult(
                    name,
                    command.argv,
                    None,
                    False,
                    "",
                    "",
                    round((monotonic() - started) * 1000),
                    "terminated",
                    str(exc),
                )
            else:
                result = VerificationResult(
                    name,
                    command.argv,
                    process.exit_code,
                    process.exit_code == 0,
                    process.stdout,
                    process.stderr,
                    round((monotonic() - started) * 1000),
                    process.supervision,
                )
            results.append(result)
            if not result.success and self.stop_on_failure:
                break
        return VerificationReport(all(item.success for item in results), tuple(results))
