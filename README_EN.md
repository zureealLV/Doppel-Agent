# Doppel Agent

A local coding agent with a Windows desktop GUI, a browser-based console, and a CLI. Connect an OpenAI-compatible Chat Completions endpoint, run a task, and inspect every model/tool step.

**Language: [简体中文](README.md) · English**

## Current release: v0.6.0

- Bounded agent loop with validated tool calls and tool-result feedback.
- Workspace file listing, UTF-8 read/write, and argv-only command execution.
- Read-only by default; writing and command execution require explicit per-run grants.
- User-supplied API base URL, model and key in the local web console. An offline mock is available for UI tests.
- A native Windows GUI window backed by the same local console, built with pywebview and PyInstaller; no TUI is required.
- Per-run `events.jsonl`, `trace.jsonl`, and `session.json` records.
- SQLite task dependency graph, estimated context-watermark compaction, and validated workspace skills.
- Per-tool manual approval for writes/commands in the web console; saved runs can be replayed after restarting the console.
- Optional stdio MCP integration using explicitly configured local executables; web calls require per-call approval.
- Opt-in read-only subagents, capped at two delegations per run and four model turns each; they cannot write, execute commands, call MCP, or delegate again.
- CLI plus a localhost JSON-RPC/NDJSON daemon.
- An isolated synthetic code-review case with a reproducible live-provider runner and a manually adjudicated result; no general benchmark claims.

This release does **not** include a CLI/daemon interactive approval workflow, a precise model tokenizer, strong isolation, MCP over HTTP, parallel subagents, a TUI, or independently measured benchmarks. See the [implementation plan](docs/PLAN.md).

## Start on Windows

Requires Python 3.11+; runtime dependencies are limited to the standard library.

```powershell
cd '<your Doppel-Agent repository directory>'
.\doppel.cmd ui
```

Open [http://127.0.0.1:8766/](http://127.0.0.1:8766/), enter your API base URL, model and key, test the connection, then submit a task. The key is not stored in browser storage or project files. It is passed to the local Core for the request and then to the endpoint you selected.

Offline smoke test:

```powershell
.\doppel.cmd demo "read README.md"
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
```

The mock is deterministic and cannot perform general coding tasks. A separate single-case live DeepSeek code-review check is documented in [the review report](bench/reports/2026-09-19-review-001.md); it is not a general benchmark.

### Windows GUI executable

The tested onedir build is at `D:\Codex Program files\Agent\Doppel-Agent\.dist\DoppelAgent\DoppelAgent.exe`. Keep the `_internal` directory beside it. Double-click to use the current directory as workspace, or pass `--workspace 'D:\your-project'`. The GUI needs Microsoft Edge WebView2 Runtime. Rebuild with `./scripts/build-desktop.ps1`; the script installs the optional desktop dependencies into `.venv`. The embedded server binds to a random `127.0.0.1` port and stops when the window closes.

For MCP, install `python -m pip install -e ".[mcp]"` and follow the [stdio MCP setup guide](docs/MCP.md). An external MCP server runs as the current user and is not contained by the workspace path checks.

## Security

The web console binds to `127.0.0.1` and rejects cross-origin requests. Web write/command/MCP calls require both the per-run checkbox and approval of the individual call; approval expires after 120 seconds. CLI/daemon grants are not interactive. File tools enforce workspace boundaries. `--allow-command` and MCP run programs as the current user and are **not** an OS sandbox. A model can receive file content returned by tools; use a trusted workspace and provider.

## Project notes

Releases use version increments and a [changelog](CHANGELOG.md). The architecture was informed by the public description of [TackleClaude](https://github.com/Tackle-B/TackleClaude), but this repository is an independent implementation and does not claim its published benchmarks.
