# Deep Agents 0.7.15 Integration Spike

Date: 2026-09-21
Validated dependency: `deepagents==0.7.15` from `uv.lock`
Reproduction: `uv run --extra agent python spikes/deep_agent_runtime.py`

## Result

The locked Deep Agents API can be composed with Doppel's provider, SQLite checkpointer, workspace policy, Skill sources, HITL, and MCP tools. The production path is implemented as the optional `DeepAgentRuntime`; the focused LangGraph remains the deterministic fallback and comparison baseline.

The spike is deliberately offline. `MockProvider` passes through the real `create_deep_agent()` graph, middleware, `DoppelBackend`, and SQLite checkpoint setup, so success does not depend on a vendor key.

## Questions answered

### 1. How is the compiled graph composed?

`create_deep_agent()` returns a `CompiledStateGraph`. It can be invoked directly, used as a runnable node, or wrapped as a subgraph when the parent and child state contracts are adapted. Doppel keeps it behind `AgentRuntime` instead of merging its `DeepAgentState` into the focused `DoppelState`; this prevents Deep Agents middleware changes from contaminating the stable runtime API.

### 2. How is the project provider injected?

`DoppelChatModel` subclasses LangChain `BaseChatModel`, translates LangChain messages/tools to Doppel's `Provider` protocol, and maps `ModelTurn` back to `AIMessage`. Both synchronous test providers and the asynchronous OpenAI-compatible provider therefore use the existing timeout, retry, semaphore, and circuit-breaker path.

### 3. How are built-in filesystem and execute capabilities controlled?

`DoppelBackend` implements only the filesystem backend protocol, not `SandboxBackendProtocol`; Deep Agents therefore cannot obtain a working shell through its built-in `execute` tool. Virtual paths are resolved again after symlinks, secrets and `.git`/`.doppel-agent` are denied, write capability is fail-closed, and write/edit tools use HITL when enabled.

Deep Agents tools are additive. Removing or rewriting built-ins depends on middleware/harness APIs, which are version-sensitive. Doppel therefore enforces security in the backend as well as the tool permissions instead of relying on prompt text.

### 4. How are Skill paths bounded?

`SkillRegistry` accepts only `workspace/skills/` and `workspace/.doppel/skills/`, validates names/frontmatter/size/links/secrets, and returns backend-virtual POSIX source paths. Detailed instructions are loaded only after selection. Skill scripts are data; they are never executed by the registry.

### 5. How are events, tokens, and cancellation mapped?

An async LangChain callback emits model/tool/subagent lifecycle events into Doppel's durable sink. Each subagent receives its own chat-model adapter with an 8,000-token budget and a six-model-call limit. The main runtime has a bounded model-call count. The scheduler's task cancellation propagates through `ainvoke`; `DeepAgentRuntime.cancel()` also cancels a directly tracked task.

The API stores the terminal runtime status, interrupt payloads, and fallback reason. MCP execution separately records normalized audit events and idempotency outcomes.

### 6. Which APIs are most likely to drift before Deep Agents 1.0?

The high-risk seams are:

- `create_deep_agent()` middleware ordering and the default general-purpose subagent;
- harness profile registration and tool exclusion;
- `BackendProtocol` result objects and permission patterns;
- HITL interrupt payload and resume decision shapes;
- subagent dictionaries and middleware inheritance;
- filesystem Skill source behavior.

All those seams are isolated in `runtime/deep.py`, `runtime/deep_backend.py`, and `runtime/deep_model.py`, with offline integration tests. Upgrading Deep Agents should change those adapters rather than the API, scheduler, persistence, or permission layers.

## Acceptance evidence

- `tests/runtime/test_deep_runtime.py`: real offline Deep Agents graph, no silent fallback.
- `tests/runtime/test_deep_backend.py`: workspace boundary, protected files, read/write grants.
- `tests/integration/test_mcp_deep_tool.py`: MCP tool appears in Deep Agents, interrupts, resumes, and executes once through the gateway.
- `tests/skills/`: strict registry, resolution, built-in workflow structure.

## Known boundary

This spike validates composition and deterministic behavior. It does not claim that every OpenAI-compatible model follows tool-calling conventions equally well; vendor/model quality belongs in the v0.12 fixed benchmark, not in the runtime acceptance gate.
