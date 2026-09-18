# Doppel Agent

An independent, Windows-friendly local coding-agent implementation inspired by the architecture of [TackleClaude](https://github.com/Tackle-B/TackleClaude). This is **not** a fork or a drop-in replacement.

## What works now

- Local `ask` CLI and a persistent localhost JSON-RPC/NDJSON daemon with a separate `run` client.
- OpenAI-compatible Chat Completions provider with function/tool calls, plus a deterministic offline mock.
- Bounded agent loop with multi-tool-call turns, tool-result round trips and structured error feedback.
- Workspace-scoped `list_files`, `read_file`, `write_file`, and argv-only `run_command` tools.
- Default-deny write/command capabilities; explicit per-process grants; optional daemon RPC token.
- JSONL events/trace and a session result under `.doppel-agent/runs/<run-id>/`.

Tested on Windows 10 with Python 3.11.15. No third-party runtime dependency is required.

## Offline smoke test (PowerShell)

```powershell
cd 'D:\Codex Program files\Agent\Doppel-Agent'
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
python -m doppel_agent.cli doctor
python -m doppel_agent.cli demo 'read README.md'
```

On Windows, the `doppel.cmd` wrapper sets `PYTHONPATH` automatically, so `./doppel.cmd doctor` and `./doppel.cmd demo "read README.md"` also work from PowerShell.

`demo` uses a deterministic mock; it is **not** a real LLM. It only understands `read <relative-path>` and a generic fallback.

## Real model (direct CLI)

Set your own API credentials in the current PowerShell session. Do not put a key in the repository.

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:DOPPEL_AGENT_BASE_URL = 'https://api.deepseek.com'
$env:DOPPEL_AGENT_MODEL = 'deepseek-flash'
$env:DOPPEL_AGENT_API_KEY = '<your-api-key>'
python -m doppel_agent.cli ask 'List files and explain this repository'
```

The adapter appends `/chat/completions` to `DOPPEL_AGENT_BASE_URL`. It also accepts a localhost HTTP-compatible server without a key. DeepSeek's [current API guide](https://api-docs.deepseek.com/) describes that URL/model combination; change these variables for another compatible provider.

Editing and command execution are **off by default**. For a direct run, grant only the capability needed:

```powershell
python -m doppel_agent.cli ask 'Create a small hello.py in this workspace' --allow-write
python -m doppel_agent.cli ask 'Run Python unit tests and summarize failures' --allow-command
```

`--allow-command` permits arbitrary argv executables as the current user; use it only in a trusted workspace. The command tool does not invoke a shell and has a 30-second timeout, but it is **not** an OS sandbox. File tools reject absolute paths and traversal/symlink escapes. The provider may receive file contents returned by tools: choose a provider you trust.

## Daemon and client

In terminal A:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m doppel_agent.cli serve --port 8765
```

In terminal B:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m doppel_agent.cli run 'read README.md' --port 8765
```

The daemon uses the mock by default. Use `serve --provider openai` with the model environment variables for real inference. For a daemon with `--allow-write` or `--allow-command`, set the same nonempty `DOPPEL_AGENT_RPC_TOKEN` in both terminal environments; the daemon refuses sensitive grants without one. The token protects RPC callers, **not** against local administrators or hostile code in the same user account.

## Current boundaries

This is a usable **single-run coding-agent MVP**, not the entire original project. No TUI, streaming tokens, persistent task DAG, context compaction, MCP, skills, subagents or independently measured cost/success-rate claims yet. The remote model path is contract-tested against a local HTTP fixture; it has **not** been live-tested with a paid provider in this environment because no API key is configured. See [the implementation plan](docs/PLAN.md).
