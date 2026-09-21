# Doppel Agent

A local coding agent with a Windows desktop GUI, a browser-based console, and a CLI. Connect an OpenAI-compatible Chat Completions endpoint, run a task, and inspect every model/tool step.

**Language: [简体中文](README.md) · English**

## Current release: v0.10.0

![Doppel Agent v0.8.2 desktop workspace](docs/images/doppel-agent-v082.png)

- Bounded agent loop with validated tool calls and tool-result feedback.
- A unified async runtime contract with `legacy`, durable LangGraph `graph`, and optional Deep Agents `deep` modes.
- A provider-backed `BaseChatModel`, policy-constrained `DoppelBackend`, bounded read-only subagents, and recorded fallback to the focused graph.
- SQLite checkpoints plus approve/reject/edit interrupts and a tool-call idempotency ledger.
- FastAPI `/api/v1` run lifecycle endpoints, durable resumable SSE, bounded FIFO scheduling, cancellation, workspace read/write locks, and resource limits.
- A long-lived async HTTP provider with bounded 429/5xx retry, `Retry-After`, deadlines, and a profile circuit breaker.
- Compact workspace maps, recursive text search, line-range reads, UTF-8 read/write, and argv-only command execution.
- Read-only by default; writing and command execution require explicit per-run grants.
- A frameless desktop window without transparent white padding, Ghostty-style traffic-light controls, and a Codex-style two-pane default workspace; the resizable run inspector opens from the top-right menu.
- Multiple custom provider profiles and per-conversation model switching; optional prices drive transparent per-run cost estimates.
- Persistent conversations with `Ctrl+K` search across titles, message bodies, and archived items; per-workspace provider settings use Windows DPAPI for API keys.
- A native Windows GUI window backed by the same local console, built with pywebview and PyInstaller; no TUI is required.
- Per-run `events.jsonl`, `trace.jsonl`, and `session.json` records.
- SQLite task dependency graph, estimated context-watermark compaction, and a strict progressive-disclosure Agent Skills registry with four built-in engineering workflows.
- Per-tool manual approval for writes/commands in the web console; saved runs can be replayed after restarting the console.
- A policy-aware MCP SDK gateway for stdio and Streamable HTTP with pooled lifecycle management, health/reconnect, paginated schema caching, per-server semaphores, HITL, idempotency, audit events, and typed multimodal results.
- The legacy loop retains opt-in read-only subagents capped at two delegations and four model turns per run. Deep mode instead exposes two read-only specialist subagents, each bounded to six model calls and an 8,000-token budget; neither path inherits write, command, MCP, or recursive-delegation capability.
- CLI plus a localhost JSON-RPC/NDJSON daemon.
- An isolated synthetic code-review case with a reproducible live-provider runner and a manually adjudicated result; no general benchmark claims.

This release does **not** include a CLI/daemon interactive approval workflow, a precise provider tokenizer, strong OS isolation, diff-first patch verification, asynchronous parallel subagents, a TUI, or independently measured three-runtime benchmarks. See the [implementation plan](docs/plans/2026-09-20-langgraph-deepagents-mcp-concurrency.md).

## Start on Windows

Requires Python 3.11+. The legacy CLI remains lightweight; install `.[agent]` for the LangGraph/FastAPI runtime.

```powershell
cd '<your Doppel-Agent repository directory>'
.\doppel.cmd ui
```

Start the v0.10 API with `python -m pip install -e ".[agent]"` and `.\doppel.cmd api --workspace 'D:\your-project' --port 8765`; OpenAPI is at `/api/docs`, versioned endpoints are under `/api/v1`, and run mode may be `legacy`, `graph`, or `deep`.

Open [http://127.0.0.1:8766/](http://127.0.0.1:8766/), open **Model & API** in the lower-left corner, enter the endpoint, model and key, test, save, then start a conversation. Conversations and settings live under the selected workspace's `.doppel-agent/`; the key is stored only as a current-user Windows DPAPI ciphertext and is never returned by the settings API.

Offline smoke test:

```powershell
.\doppel.cmd demo "read README.md"
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
```

The mock is deterministic and cannot perform general coding tasks. The synthetic live review plus read-only reviews of three real local projects are documented in the [benchmark strategy](docs/BENCHMARK_STRATEGY.md); they are not a general benchmark or a SWE-bench score.

### Windows GUI executable

The tested onedir build is at `D:\Codex Program files\Agent\Doppel-Agent\.dist\DoppelAgent\DoppelAgent.exe`. Keep the `_internal` directory beside it. Double-click to use the current directory as workspace, or pass `--workspace 'D:\your-project'`. The GUI needs Microsoft Edge WebView2 Runtime. Rebuild with `./scripts/build-desktop.ps1`; the script installs the optional desktop dependencies into `.venv`. The embedded server binds to a random `127.0.0.1` port and stops when the window closes.

The traffic-light buttons close, minimize, and maximize/restore the window. The top-right menu toggles the run inspector. **New Conversation** and **Code Review** each reuse an existing empty draft. Code Review first offers four review templates and only fills the composer after a choice; it never calls the model until the user explicitly sends the prompt. Press `Ctrl+K` to search all active and archived conversations.

For MCP, install `python -m pip install -e ".[agent]"` and follow the [MCP Gateway guide](docs/MCP.md). Stdio servers run as the current user and are not an OS sandbox; HTTP auth tokens are resolved from environment profiles rather than stored in JSON.

## Security

The web console binds to `127.0.0.1` and rejects cross-origin requests. Web write/command/MCP calls require both the per-run checkbox and approval of the individual call; approval expires after 120 seconds. CLI/daemon grants are not interactive. File tools enforce workspace boundaries. `--allow-command` and MCP run programs as the current user and are **not** an OS sandbox. A model can receive file content returned by tools; use a trusted workspace and provider.

## Project notes

Releases use version increments and a [changelog](CHANGELOG.md). The architecture was informed by the public description of [TackleClaude](https://github.com/Tackle-B/TackleClaude), but this repository is an independent implementation and does not claim its published benchmarks.
