# Doppel Agent

A local coding agent and Windows coding workbench for understanding repositories, reviewing code, planning tasks, and inspecting model/tool execution. Bring an OpenAI-compatible Chat Completions provider; keep conversations, explicit context, approvals, and run evidence in your selected local project.

**English · [简体中文](README_CN.md)**

**Latest prerelease: [v0.15.0-rc.1](https://github.com/zureealLV/Doppel-Agent/releases/tag/v0.15.0-rc.1)** · Windows x64 · Python 3.11+ · MIT

[Download Windows desktop](https://github.com/zureealLV/Doppel-Agent/releases/download/v0.15.0-rc.1/DoppelAgent-v0.15.0-rc.1-windows-x64.zip) · [User guide](docs/USER_GUIDE.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Release scope](docs/releases/2026-10-08-help-prerelease.md)

![Doppel Agent v0.15.0-rc.1 native Windows coding workbench](docs/images/doppel-agent-v015-rc1-workbench.jpg)

*Actual prerelease EXE in an isolated sample project, captured on 2026-10-08. This is the new Vue workbench, not the historical v0.8.2 console. No task or model request was submitted for these screenshots.*

## What you can do

| Workspace | Capabilities |
|---|---|
| **Conversations** | Persistent native Graph, Deep and reviewed Legacy chats; `Ctrl+K` title/message search, groups, archive, review templates, profile/effort selection and on-demand execution details. The engine is fixed when a chat is created. |
| **Plans & tasks** | Revisioned work orders with dependencies, engine/profile selection, explicit dispatch approval and a persistent task/run queue. Saving a plan does not execute it. |
| **Context & notes** | File snippets, accepted snapshots and sourced facts/constraints/decisions. Context binding is explicit and frozen at admission, not silently inherited from a panel. |
| **Changes & verification** | Current Git snapshots separate from historical patches; base-hash-checked multi-file diffs, approvals and conflicts. Reviewed inverse operations require retained preimages/fresh checks; verification uses project-configured argv, a command grant and separate approval. |
| **Extensions** | MCP configuration/cached catalogs and Agent Skills metadata, with four built-in engineering Skills. Probes/refresh require confirmation; opening the page is not tool permission. |
| **Execution & reports** | Durable events, tools, approvals, bounded read-only subagents, usage and declared-price estimates. Unknown usage/cost is not zero or an invoice. |
| **Projects & help** | Native folder/recent-project selection with guarded switching; compact sidebar/composer, optional inspector, and titlebar documentation, shortcuts, troubleshooting and build version. |

## New interface

**Plans & tasks — a read-only example draft, not an executed result**

![Doppel Agent v0.15.0-rc.1 plan editor with an unexecuted example](docs/images/doppel-agent-v015-rc1-plans.jpg)

**Help / About — the version comes from the actual build**

![Doppel Agent v0.15.0-rc.1 Help About dialog](docs/images/doppel-agent-v015-rc1-help.jpg)

[Screenshot provenance](docs/images/README.md). Unedited native-window captures are presentation evidence, **not full native acceptance or model-quality results**.

## Runtime and architecture

- **Three engines:** Legacy baseline, durable LangGraph `graph`, optional Deep Agents `deep`, behind one async runtime contract. Deep uses a project-owned backend and bounded read-only specialists; fallback is recorded.
- **Durable execution:** SQLite conversations/checkpoints, approve/reject/edit interrupts, tool-call idempotency, resumable SSE, bounded FIFO scheduling, workspace locks and cancellation/restart reconciliation.
- **Review before effects:** diff-first multi-file changes, stale-base rejection, rollback and project verification allowlists. Per-run write/command/MCP/delegation grants do not replace individual approvals.
- **Providers & extensions:** multiple OpenAI-compatible profiles, bounded retries/deadlines/circuit breaking, policy-aware stdio/Streamable HTTP MCP lifecycle management and progressive-disclosure Skills.
- **Local stack:** pywebview/WebView2 + PyInstaller desktop; Vue 3 + TypeScript + Vite on the hybrid desktop server; FastAPI `/api/v1`; CLI and localhost JSON-RPC/NDJSON daemon.

| Entry point | Meaning |
|---|---|
| Desktop `/` → `/runtime/` | Current Vue shell: native chats, plans, context, changes, extensions and reports. |
| **Standalone Runtime** in the shell | Separate native runs, not continuations of existing chats. |
| **Legacy history** / `/legacy/` | Older chain/data; no automatic conversion into native history or native context binding. |
| `doppel.cmd ui` | Lightweight older browser console, not the new default desktop shell. |
| `doppel.cmd api` | Standalone API; this command does not supply the desktop asset/Legacy bridge. |

## Start on Windows

### Desktop download

1. Download the [Windows x64 archive](https://github.com/zureealLV/Doppel-Agent/releases/download/v0.15.0-rc.1/DoppelAgent-v0.15.0-rc.1-windows-x64.zip) and extract the **whole folder**. Keep `DoppelAgent.exe` beside `_internal`; the EXE alone is not portable.
2. Windows 10+ needs **Microsoft Edge WebView2 Runtime**. The packaged desktop does not require Python.
3. Start in a disposable project, preferably with offline Mock. Open **Model / API settings**, choose a profile, then create a conversation and explicitly send a task. Templates only fill the composer; **Test connection may make a billable call**.

```powershell
# From the extracted release directory; use your own project path.
.\DoppelAgent.exe --workspace 'D:\your-project'
```

One window has one active project. Switching/closing waits for owned requests and IO cleanup; unresolved operations or unsaved drafts may block the transition. Hiding a panel is not cancellation. See the [user guide](docs/USER_GUIDE.md).

### Run from source

Requires Python **3.11+**. From this repository:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[agent,desktop]"
.\.venv\Scripts\python.exe -m doppel_agent desktop --workspace 'D:\your-project'
```

For the standalone API, use the same environment with `-m doppel_agent api --workspace 'D:\your-project' --port 8765`; OpenAPI is at `http://127.0.0.1:8765/api/docs`. For the older browser console, use `-m doppel_agent ui` (default `http://127.0.0.1:8766/`).

Offline CLI smoke (Mock is deterministic, not a general coding model):

```powershell
.\.venv\Scripts\python.exe -m doppel_agent demo "read README.md"
```

### Isolated candidate build

```powershell
./scripts/build-candidate.ps1 -OutputDirectory '.artifacts/my-new-candidate'
```

Uses existing dependencies and a **new** output directory. Produces wheel, sdist, onedir EXE, logs and source/artifact manifest; does not install dependencies, launch the app, run acceptance suites or overwrite the daily `.dist/DoppelAgent`. A build is not acceptance.

## Release status and evidence

**v0.15.0-rc.1 is a prerelease, not stable v1.0 or completed S0–S10/native N1–N6 acceptance.** Python package/import: **v0.15.0rc1** (PEP 440); frontend/tag: **v0.15.0-rc.1** (SemVer). Historical stable source: [v0.14.4](docs/releases/v0.14.4.md).

Release source: `c94b95c`. Its [main-branch CI](https://github.com/zureealLV/Doppel-Agent/actions/runs/37758999503) passed; the later [tag-triggered CI](https://github.com/zureealLV/Doppel-Agent/actions/runs/37763221532) failed a Python 3.12 integration timeout (Python 3.11 cancelled, frontend passed). These are distinct observations, not an all-green release claim. See [dated scope / retained failed attempts](docs/releases/2026-10-08-help-prerelease.md) and [prerelease notes / asset hashes](https://github.com/zureealLV/Doppel-Agent/releases/tag/v0.15.0-rc.1).

Engineering tests, scripted fixtures, offline Mock matrices and screenshots do **not** establish model quality, review success rate, Token savings or production reliability. Paid evaluation and full runtime/native acceptance remain pending. There is no strong OS sandbox, precise provider tokenizer or CLI/daemon interactive approval flow. The guarded evaluation runner's budget ledger is not a cap on ordinary desktop chats or your provider account.

## Security

Services bind to loopback and reject cross-origin requests. File tools enforce workspace/policy boundaries. Write, command, MCP and delegation default off; command/MCP programs run as the current user and are **not an OS sandbox**. Models can receive selected/tool-returned file content: use a trusted workspace/provider.

Project state lives under the selected workspace's `.doppel-agent/`. Windows settings protect saved API Keys with current-user DPAPI rather than returning plaintext to the browser; follow the protection status actually shown by settings. Never put credentials in reports, screenshots or Issues.

## Documentation

- [User guide](docs/USER_GUIDE.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [MCP gateway](docs/MCP.md)
- [Changelog](CHANGELOG.md) · [Technical report (Chinese)](docs/TECHNICAL_REPORT_ZH.md)
- [Benchmark strategy](docs/BENCHMARK_STRATEGY.md) · [Original runtime plan](docs/plans/2026-09-20-langgraph-deepagents-mcp-concurrency.md)
- [Whole-goal handoff / remaining acceptance](docs/plans/2026-10-06-new-session-whole-goal-handoff.md)

Independent implementation informed by the public architecture description of [TackleClaude](https://github.com/Tackle-B/TackleClaude); this project does not claim its source or published benchmarks. [MIT License](LICENSE).
