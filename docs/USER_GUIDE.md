# Doppel-Agent user guide

[简体中文](USER_GUIDE_CN.md) · [Troubleshooting](TROUBLESHOOTING.md) · [Project](../README.md)

## Scope first

This is a **prerelease Local Coding Workbench candidate**, not a stable release with the complete S0–S10/native acceptance matrix finished. User UI acceptance, Mock regressions and engineering checks do not establish model quality, Token savings or production reliability. Historical FAIL/UNKNOWN evidence remains; paid evaluation has not been performed.

New native conversations support **Graph (LangGraph), Deep (DeepAgent), and reviewed Legacy (Core with native run records and durable approvals)**. The engine cannot be changed after creation. **Legacy history** uses the older conversation/execution chain: it is not imported or converted into native history and does not bind the native context panel. **Standalone Runtime** creates separate native runs, not continuations of existing chats. These are distinct entry points.

## First five minutes

1. Download the Windows onedir prerelease archive and extract the whole folder. Keep `DoppelAgent.exe` with `_internal`. Begin with a disposable project and Mock, not write access to an important repository.
2. Use the folder icon to select a directory or recent project, acknowledge the switch, and execute it. Directory selection registers a project without starting a task. One window has one active project. Switching waits for original requests and IO cleanup; older Legacy tasks must end first. Unsent drafts are not automatically saved and grants do not carry over.
3. Open **Model / API settings** at the bottom of the chat sidebar. Choose/create a profile: Mock for offline use, or the exact compatible URL, model and Key for a real service. Saving does not probe automatically. **Test connection may make a billable call.** Do not include Keys in feedback. Saved Keys are not shown again; use the protection status actually displayed by settings.
4. Choose the engine when creating a conversation. Describe a task, select profile/effort, then send explicitly. Start with read-only repository understanding/review. Templates fill the prompt only; they do not invoke a model.
5. Open **execution details** to inspect events, state, tools and approvals. Review scope, inputs and risk before granting an approval.

## Navigation and implemented shortcuts

The rail contains the workbench, plans/tasks, changes/verification, extensions, standalone Runtime and Legacy history. Icons have accessible labels and hover titles. Titlebar **Help** opens documentation, shortcuts, troubleshooting, GitHub Issues and the actual build version. There is no built-in update check or cloud-status monitor.

| Action | Key / entry |
|---|---|
| Search titles and messages, including archived chats | `Ctrl+K` (also `⌘+K`); Legacy searches older history; other pages open native search |
| Send / newline in a chat composer | `Enter` / `Shift+Enter`; IME composition does not send |
| Select / open search results | `↑↓` / `Enter` |
| Help menu | Click/focus Help; `↑↓`, `Home`, `End` navigate; `Enter` activates; `Esc` closes; `Tab` leaves |

Recent/archive, groups and conversation management are in the sidebar/header. Deleting visible history is not erasure of all audit evidence. Other dialogs have request/confirmation-specific close barriers; `Esc` is not run cancellation, approval or cleanup.

## Explicit permissions and context

The composer **＋** expands templates, grants and context binding. Workspace write, command execution, MCP and delegation default to off. A grant is not automatic approval of every risky action and is not a strong OS sandbox. Standalone Runtime also exposes explicit permissions.

The context panel selects file snippets, creates accepted snapshots, and manages fact/constraint/decision notes with sources in project/work-order scopes. **Bind context for this submission** defaults off. Binding first waits for selection persistence; admission validates sources and freezes input. Later panel changes do not rewrite historical inputs or grant tool access. Predecessor results obey source/dependency/selection rules. Save or discard unsaved edits first.

## Plans, tasks and recovery

Plans/tasks create and edit revisioned work orders with tasks, dependencies, engines, profiles and access. Save the draft, explicitly approve dispatch, then refresh the queue. Drafts are not automatically approved; failed nodes are not silently retried; conflicts are not overwritten. Historical revisions are plan definitions, not replay of historical execution state.

Reopening a project loads persisted conversations, runs and work-order selections, not guaranteed unsent drafts. Inspect run state, pending approvals and recovery in execution details. Unknown submission locks the form: use **retry the original request (same input/key)** only where offered; do not create a duplicate or change its key. Adopt original late receipts instead of making a new call. Hiding/closing a panel is not proof of backend cancellation/drain. See [troubleshooting](TROUBLESHOOTING.md).

## Changes, approvals and verification

Changes/verification separates **current Git snapshots** from **historical run patch evidence**. Snapshots are not a complete Git porcelain clean verdict. Historical applied receipts do not prove current bytes match, or all changes were agent-generated. Bring in the explicitly selected native run from chat/work-order/standalone Runtime, or enter a registered run ID.

Exact inverse review requires retained preimages, fresh current-file checks and source quiescence checks, a preview and a new approval. It is not arbitrary Git rollback. Verification uses project-configured argv, explicit command permission and a separate approval, not arbitrary terminal input. Inverse and verification do not inherit consent or overwrite each other's results; neither replaces whole-release acceptance.

External linked-worktree metadata requires the desktop bridge: **native directory selection plus explicit confirmation of the exact workspace/metadata root**. The grant is read-only, owner-scoped and nonpersistent. Browser paths, checkboxes and gitfiles are not grants. Revocation waits for already-entered reads to drain.

## Extensions and reports

The extension center explicitly reads configured servers, cache, MCP probes/tool catalogs and Skills metadata. Entering it does not connect, install extensions, call tools or authorize a run. Probe/refresh requires confirmation. Cached/successful catalogs do not establish live health or tool execution. Remote text is untrusted and potentially private: do not publish it directly. Skill adoption is decided by runtime/Policy, not a displayed title.

Run reports show events, usage, declared-price estimates and evidence scope for a specified native run. Default price 0 is not free; unknown usage/cost is not zero, a verified invoice or an account hard cap. Collapsing reports/child review retains original requests, drafts and unknown state. Manually redact paths, code and service details before sharing.

## Source map

Entry points: `frontend/src/App.vue` and `components/WorkbenchShell.vue`; conversation, project, context, orders, changes, extensions and report components implement the corresponding panels. API routes live under `src/doppel_agent/api/routes/`. Commands/install instructions: [README](../README.md). Acceptance boundaries: [release record](releases/2026-10-08-help-prerelease.md).
