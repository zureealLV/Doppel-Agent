# Doppel Agent LangGraph / Deep Agents / Skills / MCP / Concurrency Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 将 Doppel Agent 从同步自研 ReAct Demo 升级为一个可恢复、可审批、可扩展、可量化的本地 Coding Agent Runtime，并形成 LangGraph、Deep Agents、Agent Skills、MCP SDK 与受控高并发的完整面试项目证据链。

**Architecture:** 保留现有 `AgentLoop` 作为可复现实验基线，在其旁边增加统一 `AgentRuntime` 接口；Focused LangGraph 与 Deep Agents 分别作为 `graph` / `deep` 实现挂在同一个 RunService 后面，共用持久化、权限、调度、事件和 Provider 边界，而不强行合并两套不兼容的 state schema。Skill 只描述工作流与领域知识，MCP SDK 层负责连接、缓存、调用、限流、重试和审计，二者不得混成一层。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic v2、LangChain 1.x、LangGraph 1.x、Deep Agents 0.x、MCP Python SDK 2.x、SQLite WAL / LangGraph SQLite Checkpointer、Vue 3 + TypeScript + Vite、pytest、Ruff、pywebview、PyInstaller；远期服务端模式可选 PostgreSQL + Redis，不作为本地 v1.0 的强依赖。

## 实施状态（更新于 2026-09-22）

- **v0.9.0 / Milestone A-B：已实现。** 完成 legacy/graph 统一 runtime、SQLite checkpoint、interrupt 三种决定、工具幂等账本、FastAPI v1、durable SSE、有界调度、取消、工作区锁、资源限流及异步 Provider 可靠性治理。
- **验证：** v0.9.0 门禁为 `86 passed`；v0.10.0 为 `114 passed, 1 skipped`；v0.11.0 为 `133 passed, 1 skipped`；v0.12.0 发布前 Windows 门禁为 `139 passed, 1 skipped`（未授予 symlink 创建权限）。v0.8.2 基线记录在 `bench/baselines/v0.8.2.json`。
- **v0.10.0 / Milestone C-D：已实现。** Task 12-21 已完成：Deep Agents 0.7.15 spike、受控 backend、`mode=deep`、Agent Skills Registry、四个工程 Skill、stdio/Streamable HTTP MCP Gateway、分页 catalog、多模态 executor、LangGraph/Deep adapter 与独立 server semaphore。
- **v0.11.0 / Milestone E：已实现。** Task 22-25 完成 diff-first patch、base hash 冲突/原子回滚、项目 argv allowlist verification、Windows Job Object 优先进程树监督，以及可查询/追问/取消的 SQLite 异步子 Agent 运行层。
- **v0.12.0 / Milestone F 基础：已实现。** 异步子 Agent 已接 parent-scoped REST；Task 26 固定 20×3×3 协议并实际跑完 180/180 Mock runtime paths；Task 27 已实测 100-task 有界调度、queue full、20 读 + 5 写互斥与运行中取消。
- **未提前宣称：** Mock smoke 不等于真实模型质量 benchmark；Provider 429、MCP 断连、慢 SSE、重启 lease，Task 28 Vue 可视化和 Task 29 最终发布门禁仍按 v0.13-v1.0 实施。

---

## 0. 决策摘要

### 0.1 不在 LangGraph 和 Deep Agents 之间二选一

采用分层组合：

```text
FastAPI / Desktop UI
        │
        ▼
RunService + AsyncRunScheduler
        │
        ▼
AgentRuntime selector
  ├── Legacy runtime               # 当前 AgentLoop，作为基线
  ├── Focused LangGraph runtime     # 自己掌控的主链
  └── Deep Agents runtime          # deep 模式：规划/子 Agent/Skill
        │
        ▼
Policy Gateway
  ├── Workspace tools
  ├── Patch / verification tools
  ├── MCP SDK Gateway
  └── Skill Registry
```

- **应用层是主骨架：** RunService、SQLite、调度、HITL 状态、事件流和错误路由由项目自己掌握；Focused 与 Deep 都不能接管这些产品边界。
- **Deep Agents 是高阶执行器：** 只在 `mode=deep` 时提供 planning、filesystem、subagent、summarization、skills 等能力。
- **现有 AgentLoop 保留：** 通过 `mode=legacy` 运行，作为同模型同任务 A/B 基线，避免“为了套框架而套框架”。
- **不直接使用 deepagents-code：** 那是现成 Coding Agent 产品，会削弱项目 ownership；只使用 `deepagents` SDK。

### 0.2 Skill、Tool、MCP 的边界

| 层 | 负责 | 不负责 |
|---|---|---|
| Skill | 何时使用能力、操作步骤、检查清单、输出格式、失败兜底 | 网络连接、密钥、重试、并发池 |
| Tool | 一个可调用动作的 schema 与实现 | 多步骤领域流程 |
| MCP SDK Gateway | transport、session、capability negotiation、分页、typed result、错误与重试 | 决定业务流程 |
| Policy Gateway | 权限、审批、审计、限流、幂等、工作区边界 | 生成自然语言答案 |

结论：**“用 Skill 包住 MCP”应实现为 Skill 引导 Agent 选择逻辑工具名，逻辑工具名再由 MCP Gateway 执行；不能在 `SKILL.md` 里直接拼 URL、令牌或原始 JSON-RPC。**

### 0.3 高并发的产品边界

Doppel Agent 是本机 Coding Agent，不追求虚假的“千 QPS”。v1.0 的并发目标是：

- 大量任务可排队；
- 少量 Agent Run 受控并发；
- Provider、MCP Server、子 Agent、命令执行分别限流；
- 同一工作区允许并发读取，但写操作串行；
- 取消、超时、背压、恢复和资源释放都可验证。

默认建议：

```text
queue_capacity             = 100
max_active_runs            = 4
max_runs_per_workspace     = 2
max_write_runs_per_workspace = 1
max_model_calls_per_profile  = 2
max_mcp_calls_per_server     = 4
max_subagents_per_run        = 2
max_command_processes        = 2
```

所有数值均为配置与验收初值，最终由压测结果调整，不写成未经测试的性能结论。

---

## 1. 目标目录结构

```text
src/doppel_agent/
├── api/
│   ├── app.py
│   ├── schemas.py
│   ├── dependencies.py
│   ├── sse.py
│   └── routes/
│       ├── runs.py
│       ├── approvals.py
│       ├── skills.py
│       └── mcp.py
├── runtime/
│   ├── base.py
│   ├── legacy.py
│   ├── graph.py
│   ├── deep.py
│   ├── service.py
│   └── factory.py
├── graph/
│   ├── state.py
│   ├── builder.py
│   ├── nodes.py
│   ├── routing.py
│   └── events.py
├── concurrency/
│   ├── scheduler.py
│   ├── limits.py
│   ├── cancellation.py
│   ├── workspace_locks.py
│   └── leases.py
├── mcp/
│   ├── config.py
│   ├── client_manager.py
│   ├── catalog.py
│   ├── executor.py
│   ├── tool_adapter.py
│   └── types.py
├── skills/
│   ├── spec.py
│   ├── registry.py
│   ├── resolver.py
│   └── middleware.py
├── workspace/
│   ├── service.py
│   ├── patching.py
│   ├── verification.py
│   └── process_supervisor.py
└── persistence/
    ├── database.py
    ├── runs.py
    ├── events.py
    ├── tool_ledger.py
    └── migrations.py

skills/
├── code-review/SKILL.md
├── bugfix/SKILL.md
├── test-repair/SKILL.md
└── mcp-operations/SKILL.md

tests/
├── runtime/
├── graph/
├── concurrency/
├── mcp/
├── skills/
├── api/
├── integration/
└── load/
```

现有文件迁移策略：

- `src/doppel_agent/loop.py` 保留，后续由 `runtime/legacy.py` 适配。
- `src/doppel_agent/core.py` 逐步缩成兼容 facade。
- `src/doppel_agent/mcp_bridge.py` 在 MCP Gateway 验收后改成兼容导出，最后删除。
- `src/doppel_agent/web/server.py` 在 FastAPI 全部路由通过后退出主链；静态资源仍可暂时复用。
- `src/doppel_agent/skills/loader.py` 先增加 Agent Skills 校验，后由 `skills/registry.py` 接管。

---

## 2. 核心状态模型

### 2.1 Graph State

新建 `src/doppel_agent/graph/state.py`：

```python
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


class DoppelState(TypedDict, total=False):
    run_id: str
    thread_id: str
    workspace_id: str
    mode: Literal["legacy", "graph", "deep", "review"]
    messages: Annotated[list, add_messages]
    plan: list[dict]
    pending_tool_calls: list[dict]
    changed_files: list[str]
    verification: dict
    approval: dict | None
    cancel_requested: bool
    step_count: int
    tool_budget_remaining: int
    final_answer: str
    error: dict | None
```

关键原则：

- `thread_id` 用于 LangGraph checkpoint；`run_id` 表示一次用户提交，二者不能混用。
- Checkpointer 保存线程内可恢复状态；跨线程的 Skill、用户偏好、MCP metadata 放 Store 或项目数据库。
- Token、日志和大量工具输出不得无限塞进 state；大对象落磁盘，只在 state 中保存引用与摘要。

### 2.2 状态机

```text
START
  → prepare
  → plan_or_react
  → route_action
      ├─ finalize → END
      ├─ request_approval → INTERRUPT
      ├─ execute_tool → observe → plan_or_react
      ├─ generate_patch → review_patch → INTERRUPT
      └─ fail → finalize_error → END
```

LangGraph interrupt 恢复时会从节点开头重新执行。因此：

1. 产生副作用的动作必须放在 interrupt 之后的独立节点；
2. `tool_call_id` 必须写入幂等账本；
3. 节点再次进入时先查询账本，已成功的调用直接复用结果；
4. 审批节点只能生成审批请求，不得先写文件再暂停。

---

## 3. API 与事件契约

### 3.1 REST API

```text
POST   /api/v1/runs
GET    /api/v1/runs/{run_id}
GET    /api/v1/runs/{run_id}/events
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/runs/{run_id}/interrupts/{interrupt_id}/resume
GET    /api/v1/skills
POST   /api/v1/skills/reload
GET    /api/v1/mcp/servers
POST   /api/v1/mcp/servers/{name}/probe
GET    /api/v1/mcp/servers/{name}/tools
```

`POST /runs` 请求核心字段：

```json
{
  "conversation_id": "...",
  "prompt": "修复测试失败",
  "mode": "deep",
  "profile_id": "...",
  "effort": "balanced",
  "permissions": {
    "workspace_write": true,
    "command_execute": true,
    "mcp_execute": false,
    "delegate": true
  },
  "idempotency_key": "client-generated-uuid"
}
```

### 3.2 SSE

```text
GET /api/v1/runs/{run_id}/events?after_seq=123
```

统一事件 envelope：

```json
{
  "seq": 124,
  "run_id": "...",
  "thread_id": "...",
  "type": "tool.completed",
  "timestamp": "2026-09-20T12:00:00Z",
  "payload": {}
}
```

必须持久化且不得丢弃：

- `run.status_changed`
- `approval.requested`
- `approval.decided`
- `tool.started/completed/failed`
- `checkpoint.saved`
- `run.cancelled/failed/completed`

允许合并或丢弃中间增量：

- 高频 token delta；
- 重复 progress；
- debug heartbeat。

---

## 4. 分阶段实施路线

## Milestone A — v0.9.0 Runtime Foundation

### Task 1: 固定当前基线

**Objective:** 在引入框架前记录可复现基线，防止重构后只剩“能启动”。

**Files:**
- Modify: `docs/BENCHMARK_STRATEGY.md`
- Create: `bench/baselines/v0.8.2.json`
- Test: existing full suite

**Steps:**

1. 记录当前 HEAD、Python、依赖、55 tests/11 subtests 的通过命令。
2. 固定 5 个离线任务：读文件、路径越界拒绝、工具失败、任务 DAG、对话恢复。
3. 固定 3 个真实模型代码审查任务，但不保存密钥。
4. 运行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m pytest -q
```

5. 预期：当前测试全通过，报告包含分母而非宣传百分比。
6. Commit：`test: freeze v0.8.2 runtime baseline`

### Task 2: 增加依赖组而不切主链

**Objective:** 安装并锁定 LangGraph、Deep Agents、FastAPI 与异步存储依赖，同时保持旧入口可用。

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_imports.py`

**Dependency shape:**

```toml
[project.optional-dependencies]
agent = [
  "fastapi>=0.141,<1.0",
  "uvicorn[standard]>=0.52,<1.0",
  "langchain>=1.0,<2.0",
  "langgraph>=1.0,<2.0",
  "deepagents>=0.7,<1.0",
  "mcp>=2.2,<3.0",
  "aiosqlite>=0.20,<1.0",
]
```

Deep Agents 仍处于 0.x，必须锁 minor 范围并以 `uv.lock` 固定实际版本；不得调用默认模型，始终显式构造项目的 OpenAI-compatible model adapter。

### Task 3: 建立统一 AgentRuntime 接口

**Objective:** 让 legacy、LangGraph、Deep Agents 使用同一调用与事件契约。

**Files:**
- Create: `src/doppel_agent/runtime/base.py`
- Create: `src/doppel_agent/runtime/legacy.py`
- Create: `src/doppel_agent/runtime/factory.py`
- Modify: `src/doppel_agent/core.py`
- Test: `tests/runtime/test_runtime_contract.py`

**Interface:**

```python
class AgentRuntime(Protocol):
    async def run(self, request: RunRequest, sink: EventSink) -> RunResult: ...
    async def resume(self, command: ResumeCommand, sink: EventSink) -> RunResult: ...
    async def cancel(self, run_id: str) -> None: ...
```

Legacy adapter 使用 `asyncio.to_thread()` 包住现有同步 `Core.run()`；不得立即重写全部旧代码。

### Task 4: 建立持久化 Graph

**Objective:** 先完成最小 LangGraph 工具回合，再接入审批和 Deep Agents。

**Files:**
- Create: `src/doppel_agent/graph/state.py`
- Create: `src/doppel_agent/graph/nodes.py`
- Create: `src/doppel_agent/graph/routing.py`
- Create: `src/doppel_agent/graph/builder.py`
- Create: `src/doppel_agent/runtime/graph.py`
- Test: `tests/graph/test_graph_tool_loop.py`

**TDD cases:**

1. 无工具调用直接完成；
2. 一次工具调用后完成；
3. 工具错误回注模型；
4. 达到预算后禁止继续调用；
5. 同一 `thread_id` 可恢复；
6. 不同 `thread_id` 状态隔离。

### Task 5: 接入 SQLite Checkpointer

**Objective:** 程序重启后仍能恢复暂停中的 Graph。

**Files:**
- Create: `src/doppel_agent/persistence/database.py`
- Create: `src/doppel_agent/persistence/migrations.py`
- Modify: `src/doppel_agent/graph/builder.py`
- Test: `tests/integration/test_checkpoint_restart.py`

**Acceptance:**

- 不使用 `MemorySaver` 作为生产配置；
- SQLite 启用 WAL、foreign keys、busy timeout；
- 使用 UUID `thread_id`；
- 强制关闭并重新创建 runtime 后，状态仍可读取；
- 增加 checkpoint retention 策略，避免无限增长。

### Task 6: 实现 interrupt/resume 与幂等账本

**Objective:** 让写入、命令和 MCP 调用在审批后执行，恢复时不重复副作用。

**Files:**
- Create: `src/doppel_agent/persistence/tool_ledger.py`
- Modify: `src/doppel_agent/graph/nodes.py`
- Modify: `src/doppel_agent/web/approvals.py`
- Test: `tests/integration/test_interrupt_resume.py`
- Test: `tests/integration/test_tool_idempotency.py`

**Tool ledger unique key:** `(run_id, tool_call_id)`。

**Acceptance:**

- interrupt 前没有副作用；
- approve/reject/edit 三种决定均可恢复；
- 同一 resume 重放两次只执行一次工具；
- 审批过期后状态为 `interrupted_expired`，不是含糊的 failed。

---

## Milestone B — v0.9.0 Async API and Controlled Concurrency

### Task 7: 建立 FastAPI app

**Objective:** 用类型化异步 API 替代新增功能继续堆进 `http.server`。

**Files:**
- Create: `src/doppel_agent/api/app.py`
- Create: `src/doppel_agent/api/schemas.py`
- Create: `src/doppel_agent/api/dependencies.py`
- Create: `src/doppel_agent/api/routes/runs.py`
- Modify: `src/doppel_agent/desktop.py`
- Test: `tests/api/test_runs_api.py`

旧 `/api/*` 暂时保留兼容；新接口全部进入 `/api/v1/*`。

### Task 8: 实现持久化 SSE

**Objective:** 取消前端高频轮询，支持断线续传。

**Files:**
- Create: `src/doppel_agent/api/sse.py`
- Create: `src/doppel_agent/persistence/events.py`
- Modify: `src/doppel_agent/events.py`
- Test: `tests/api/test_sse_resume.py`

**Acceptance:**

- 每个事件具有严格递增 `seq`；
- 客户端传 `after_seq` 后不重复或漏掉 durable events；
- 慢客户端不会阻塞 Graph；
- 高频 token 事件队列满时可合并，但审批/完成事件不能丢。

### Task 9: 实现 AsyncRunScheduler

**Objective:** 将 `ThreadPoolExecutor(max_workers=2)` 改成有容量、可取消、可观测的异步调度器。

**Files:**
- Create: `src/doppel_agent/concurrency/scheduler.py`
- Create: `src/doppel_agent/concurrency/limits.py`
- Create: `src/doppel_agent/concurrency/cancellation.py`
- Test: `tests/concurrency/test_scheduler.py`

**Required behavior:**

- bounded queue；
- FIFO 基础公平性；
- run 状态原子迁移；
- queue full 返回 429/503 和可读错误；
- shutdown 时停止接单、取消未开始任务、等待或中止运行任务；
- `asyncio.CancelledError` 不得被普通 `except Exception` 吞掉。

### Task 10: 工作区读写锁与资源限流

**Objective:** 防止并发 Agent 相互覆盖工作区或耗尽 Provider/MCP/命令资源。

**Files:**
- Create: `src/doppel_agent/concurrency/workspace_locks.py`
- Modify: `src/doppel_agent/concurrency/limits.py`
- Test: `tests/concurrency/test_workspace_locking.py`
- Test: `tests/concurrency/test_resource_limits.py`

**Rules:**

- 同工作区多个只读 run 可并发；
- 任意写 run 与其他写 run 互斥；
- patch review 等待审批时是否继续占写锁需要明确：默认释放，批准后重新获取并校验 base hash；
- 每个 provider profile 独立 semaphore；
- 每个 MCP server 独立 semaphore；
- 命令执行使用全局较小 semaphore。

### Task 11: Provider 异步化和可靠性治理

**Objective:** 避免同步 `urllib` 阻塞事件循环，并处理 429/5xx/超时。

**Files:**
- Modify: `src/doppel_agent/provider.py`
- Create: `src/doppel_agent/runtime/provider_adapter.py`
- Test: `tests/runtime/test_provider_reliability.py`

**Policy:**

- 使用长生命周期 async HTTP client；
- connect/read/write/pool timeout 分开；
- 429 尊重 `Retry-After`；
- 仅对可重试错误使用指数退避 + jitter；
- 每次 run 有总 deadline 与 retry budget；
- 连续错误触发 profile 级 circuit breaker；
- 取消 run 时立即取消等待中的模型调用。

---

## Milestone C — v0.10.0 Deep Agents and Skills

### Task 12: Deep Agents 技术 Spike ✅ v0.10.0

**Objective:** 在不污染主链的情况下确认当前 Deep Agents API、OpenAI-compatible 模型、backend 和 checkpointer 可组合。

**Files:**
- Create: `spikes/deep_agent_runtime.py`
- Create: `tests/runtime/test_deep_agent_smoke.py`
- Document: `docs/research/DEEP_AGENTS_SPIKE.md`

**Spike questions:**

1. `create_deep_agent()` 返回的 compiled graph 如何作为主 LangGraph 的 node/subgraph 使用；
2. 如何显式注入项目 Provider，而不是依赖默认 Anthropic 模型；
3. 如何禁用或接管内置 filesystem/execute 工具；
4. 如何让 Skill 路径受工作区边界约束；
5. 子 Agent 的事件、Token、取消如何映射到 Doppel 事件模型；
6. Deep Agents 0.x 升级时哪些 API 最可能漂移。

Spike 完成后才决定生产 adapter，禁止凭文档想象直接重构。

### Task 13: 实现 DoppelBackend ✅ v0.10.0

**Objective:** 让 Deep Agents 文件能力复用 Doppel 的路径、秘密文件、审计与审批策略。

**Files:**
- Create: `src/doppel_agent/runtime/deep_backend.py`
- Modify: `src/doppel_agent/workspace/service.py`
- Test: `tests/runtime/test_deep_backend.py`

禁止 Deep Agents 绕过项目 Policy Gateway 直接读 `.env`、`.git`、`.doppel-agent`。官方 filesystem permission 只覆盖内置文件工具，不能代替自定义工具与 MCP 权限。

### Task 14: 实现 DeepAgentRuntime ✅ v0.10.0

**Objective:** 增加 `mode=deep`，提供规划、上下文卸载、同步子 Agent 与 Skill。

**Files:**
- Create: `src/doppel_agent/runtime/deep.py`
- Modify: `src/doppel_agent/runtime/factory.py`
- Modify: `src/doppel_agent/graph/builder.py`
- Test: `tests/runtime/test_deep_runtime.py`

**Initial limits:**

- 每个 run 最多 2 个子 Agent；
- 每个子 Agent 独立步骤与 Token budget；
- 默认只读；
- 子 Agent 不得继续递归委派；
- 子 Agent 结果必须带 evidence paths；
- 深度模式失败可降级回 focused graph，但必须记录降级事件。

### Task 15: 将 Skill Loader 升级为 Agent Skills Registry ✅ v0.10.0

**Objective:** 兼容现有 `.doppel/skills/`，同时实现 progressive disclosure 和严格验证。

**Files:**
- Create: `src/doppel_agent/skills/spec.py`
- Create: `src/doppel_agent/skills/registry.py`
- Create: `src/doppel_agent/skills/resolver.py`
- Modify: `src/doppel_agent/skills/loader.py`
- Test: `tests/skills/test_registry.py`
- Test: `tests/skills/test_resolution.py`

**Validation:**

- 必须有 `name`、`description`；
- skill 名唯一；
- descriptions 过度重叠时报 warning；
- `SKILL.md` 建议小于 500 行；
- supporting files 只允许 skill 根下一级引用；
- 禁止 Skill 包含明文 secret；
- Skill scripts 不自动执行，仍经 command policy/审批。

### Task 16: 添加四个面试价值最高的 Skill ✅ v0.10.0

**Objective:** 展示 Skill 是可复用工作流，不是换皮 prompt。

**Files:**
- Create: `skills/code-review/SKILL.md`
- Create: `skills/bugfix/SKILL.md`
- Create: `skills/test-repair/SKILL.md`
- Create: `skills/mcp-operations/SKILL.md`
- Test: `tests/skills/test_builtin_skills.py`

每个 Skill 必须包含：触发条件、步骤、工具选择、停止条件、失败兜底、输出格式、验收方法。先只做四个，避免 Skill 数量膨胀导致错误选择。

---

## Milestone D — v0.10.0 MCP SDK Gateway

### Task 17: 定义 MCP 配置和类型 ✅ v0.10.0

**Objective:** 同时支持 stdio 与 Streamable HTTP，并保持配置中不出现密钥值。

**Files:**
- Create: `src/doppel_agent/mcp/config.py`
- Create: `src/doppel_agent/mcp/types.py`
- Migrate: `.doppel/mcp.json` schema
- Test: `tests/mcp/test_config.py`

配置示意：

```json
{
  "servers": {
    "local-tools": {
      "transport": "stdio",
      "command": "C:/absolute/python.exe",
      "args": ["C:/trusted/server.py"],
      "env_names": ["SERVICE_TOKEN"],
      "max_concurrency": 2
    },
    "remote-tools": {
      "transport": "streamable_http",
      "url": "https://example.com/mcp",
      "auth_profile": "example-oauth",
      "max_concurrency": 4
    }
  }
}
```

### Task 18: 实现 MCPClientManager ✅ v0.10.0

**Objective:** 统一 session 生命周期、健康检查、重连和关闭。

**Files:**
- Create: `src/doppel_agent/mcp/client_manager.py`
- Test: `tests/mcp/test_client_manager.py`

**Rules:**

- `async with Client(...)` 生命周期由 manager 管理；
- 一个失效 Client 不重复使用；
- HTTP session 可按 server 保持连接，stdio 进程按配置复用或按调用启动；
- shutdown 必须关闭 session/子进程；
- 健康探针不得调用有副作用工具；
- 服务器 metadata、capabilities、protocol version 写入缓存。

### Task 19: 实现 MCPToolCatalog ✅ v0.10.0

**Objective:** 分页列出工具并缓存 schema，避免每次调用前重复 `list_tools()`。

**Files:**
- Create: `src/doppel_agent/mcp/catalog.py`
- Test: `tests/mcp/test_catalog.py`

**Cache key:** server identity + protocol version + server version/config hash。

**Acceptance:**

- 遍历 `next_cursor`；
- title 缺失时回退到 name；
- schema 变化使缓存失效；
- 不可信 description 不进入系统 prompt 的高优先级区域；
- 工具名在多 server 下规范化为 `mcp__{server}__{tool}`。

### Task 20: 实现 MCPToolExecutor ✅ v0.10.0

**Objective:** 正确处理多模态 content、structured content 和 `is_error`。

**Files:**
- Create: `src/doppel_agent/mcp/executor.py`
- Create: `src/doppel_agent/mcp/tool_adapter.py`
- Test: `tests/mcp/test_executor.py`
- Test: `tests/mcp/test_tool_adapter.py`

**Execution order:**

```text
validate schema
→ policy check
→ resource semaphore
→ approval interrupt if required
→ idempotency ledger lookup
→ call_tool
→ normalize content blocks
→ persist audit/event
→ return model view + application view
```

必须先看 `is_error`，不能因为 RPC 返回成功就把工具执行当成功。图片、音频、resource link 和 embedded resource 不得强行读取 `.text`。

### Task 21: 将 MCP Tool 暴露给 LangGraph/Deep Agents ✅ v0.10.0

**Objective:** 通过一个 adapter 将 MCP 工具转换为 LangChain tools，同时保留 Doppel policy。

**Files:**
- Modify: `src/doppel_agent/mcp/tool_adapter.py`
- Modify: `src/doppel_agent/runtime/deep.py`
- Modify: `src/doppel_agent/graph/builder.py`
- Test: `tests/integration/test_mcp_graph_tool.py`

不得让 Deep Agents 直接连接 MCP server 绕过 Gateway。Skill 只引用规范化逻辑工具名和使用流程。

---

## Milestone E — v0.11.0 Patch, Verification and Async Subagents

### Task 22: Diff-first 修改链路 ✅ v0.11.0

**Objective:** 将“批准写权限”升级为“审查具体补丁”。

**Files:**
- Create: `src/doppel_agent/workspace/patching.py`
- Modify: `src/doppel_agent/tools.py`
- Modify: `src/doppel_agent/graph/nodes.py`
- Test: `tests/integration/test_patch_approval.py`

**Flow:** propose patch → validate base hash → show diff → interrupt → approve/edit/reject → apply atomically。

拒绝后工作区必须零变化；base hash 变化后禁止直接应用旧补丁。

### Task 23: Verification pipeline ✅ v0.11.0

**Objective:** 修改完成后自动执行项目允许的验证命令并输出结构化结果。

**Files:**
- Create: `src/doppel_agent/workspace/verification.py`
- Create: `.doppel/verification.json.example`
- Test: `tests/integration/test_verification_pipeline.py`

验证命令必须来自用户/项目配置 allowlist，不能让模型任意生成 shell 字符串。

### Task 24: 可取消进程树 ✅ v0.11.0

**Objective:** 任务取消或超时时终止命令及其子进程。

**Files:**
- Create: `src/doppel_agent/workspace/process_supervisor.py`
- Modify: `src/doppel_agent/tools.py`
- Test: `tests/integration/test_process_cancellation.py`

Windows 优先 Job Object；无法启用时明确降级并记录，不得只取消 Python await 而留下后台进程。

### Task 25: 异步子 Agent ✅ v0.11.0

**Objective:** 在同步子 Agent 已稳定后，引入可查询、可追问、可取消的后台子任务。

**Files:**
- Create: `src/doppel_agent/runtime/async_subagents.py`
- Modify: `src/doppel_agent/concurrency/scheduler.py`
- Test: `tests/integration/test_async_subagents.py`

先实现同进程 ASGI/内部 runtime transport，再评估远程 Agent Protocol。禁止首版直接做跨机器分布式部署。

---

## Milestone F — v1.0.0 Evaluation and Release Evidence

### Task 26: 固定三运行时对照（协议与 Mock smoke ✅ v0.12.0；真实模型裁判待做）

**Objective:** 比较 legacy、focused LangGraph、Deep Agents，而不是只展示框架名。

**Files:**
- Create: `bench/runtime_matrix.py`
- Create: `bench/cases/runtime/`
- Modify: `docs/BENCHMARK_STRATEGY.md`
- Test: `tests/test_runtime_benchmark_protocol.py`

**Matrix:**

```text
3 runtimes × 20 tasks × 3 repeats = 180 runs
```

任务建议：

- 4 个代码导航；
- 4 个已知答案审查；
- 4 个测试驱动修复；
- 3 个多文件 patch；
- 2 个审批/恢复；
- 2 个 MCP 工作流；
- 1 个并发取消。

**Metrics:**

- deterministic test pass；
- seeded defect precision/recall；
- patch apply rate；
- tool error rate；
- duplicate side-effect count；
- token input/output；
- wall time / queue time；
- model cost；
- interrupt resume success；
- cancellation latency。

### Task 27: 并发压测（本地 scheduler/lock 子集 ✅ v0.12.0；外部故障场景待做）

**Objective:** 用可复现负载证明背压和资源治理，而不是宣称模糊“高并发”。

**Files:**
- Create: `tests/load/test_scheduler_load.py`
- Create: `bench/run_load.py`
- Create: `bench/reports/load-template.md`

**Scenarios:**

1. 100 个任务提交、queue capacity=100、max active=4；
2. 同一工作区 20 个只读 + 5 个写任务；
3. Provider 返回 429；
4. MCP server 延迟和断开；
5. SSE 慢客户端；
6. 运行中取消；
7. 进程重启后 lease 回收。

**Pass gates:**

- active run 永不超过配置；
- 同工作区写并发永不超过 1；
- 无重复 side effect；
- queue full 明确拒绝；
- shutdown 后无遗留子进程；
- durable events 数量和最终状态一致。

不要预设吞吐数字；先测，再将硬件、模型、配置、分母和日期写进报告。

### Task 28: 前端升级

**Objective:** 在后端契约稳定后再迁移 Vue 3 + TypeScript，避免两边同时重写。

**Files:**
- Create: `frontend/`
- Replace after parity: `src/doppel_agent/web/index.html`
- Replace after parity: `src/doppel_agent/web/app.js`
- Replace after parity: `src/doppel_agent/web/app.css`

页面必须展示：Graph 节点、子 Agent、Skill 命中、MCP 工具来源、队列时间、运行时间、interrupt 审批、Diff 与测试结果。

### Task 29: 发布门禁

**Objective:** 确保 v1.0 的 README 只写已验证能力。

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/TECHNICAL_REPORT_ZH.md`
- Modify: `.github/workflows/tests.yml`

**Required CI:**

- Python unit/integration；
- optional MCP in-process tests；
- Ruff；
- frontend typecheck/build；
- benchmark protocol static validation；
- Windows-specific process/DPAPI tests放 Windows runner；
- 真实付费模型测试不进入普通 PR gate。

---

## 5. 数据库与一致性计划

现有 conversations/tasks 表保留并迁移；新增：

```text
runs
  id, thread_id, conversation_id, workspace_id, mode,
  status, queued_at, started_at, finished_at, deadline_at,
  cancel_requested, error_code

run_leases
  run_id, worker_id, lease_until, heartbeat_at

tool_executions
  run_id, tool_call_id, tool_name, args_hash,
  status, result_ref, started_at, finished_at
  UNIQUE(run_id, tool_call_id)

events
  run_id, seq, type, timestamp, payload_json
  UNIQUE(run_id, seq)

approvals
  id, run_id, interrupt_id, tool_call_id,
  status, request_json, decision_json, expires_at

mcp_server_cache
  server_name, identity_hash, protocol_version,
  capabilities_json, tools_json, expires_at
```

状态迁移必须通过 repository 方法执行，禁止 API、Graph node、Scheduler 各自直接拼 SQL。

---

## 6. 错误分类

```text
USER_ERROR
  invalid_request, permission_denied, approval_rejected

TRANSIENT
  provider_rate_limited, provider_unavailable,
  mcp_disconnected, database_busy

TERMINAL
  invalid_provider_response, tool_schema_mismatch,
  context_budget_exceeded, patch_conflict

CONTROL
  cancelled, interrupted, deadline_exceeded,
  queue_full, lease_lost
```

UI 和 benchmark 按错误分类统计，不能把所有失败都压成 `RuntimeError: ...`。

---

## 7. 本地模式与服务端模式

### v1.0 必做：Local Mode

- SQLite WAL；
- 本进程 async scheduler；
- 2–4 个 active runs；
- pywebview/PyInstaller；
- stdio + Streamable HTTP MCP；
- Windows process supervision。

### v1.1 可选：Server Mode

只有当 local load report 表明单机队列已成为瓶颈，再增加：

- PostgreSQL + `AsyncPostgresSaver`；
- Redis 作为分布式 queue/cancellation/pubsub；
- 多 worker lease；
- per-user / per-tenant quotas；
- OAuth MCP credentials。

Celery/Redis/Kubernetes 不进入 v1.0；否则项目会从 Agent Runtime 变成没有证据的分布式名词展览。

---

## 8. 面试讲法

### 30 秒定位

> Doppel Agent 是我独立实现的本地 Coding Agent Runtime。项目早期用纯 Python 自研了有界 ReAct Loop、工具权限和事件存储；后续我保留这套实现作为基线，用 LangGraph 增加 checkpoint、interrupt/resume 和状态路由，再将 Deep Agents 的规划、Skill 和子 Agent 能力作为可选深度执行器接入。MCP 不直接暴露给模型，而是经过我封装的 session、schema、权限、限流和幂等网关。并发方面不追求虚假的高 QPS，而是围绕模型、工作区写锁、MCP 和子进程做分层背压，并用固定任务集比较三种 runtime 的成功率、成本、延迟和恢复能力。

### 高频追问必须能回答

1. 为什么保留自研 Loop，而不是直接删掉？
2. LangGraph checkpoint 和业务数据库分别保存什么？
3. interrupt 恢复时节点重跑，如何防止工具重复执行？
4. 为什么 Deep Agents 被隔离成一个 runtime，而不是让整个产品直接套 `create_deep_agent`？
5. Skill 与 MCP Tool 有什么区别？
6. MCP 的 `content`、`structured_content`、`is_error` 如何处理？
7. 同一工作区多个 Agent 为什么不能同时写？
8. Provider 429、MCP 断连、命令超时分别如何重试或终止？
9. SQLite 如何处理多任务写入？什么时候升级 Postgres？
10. “高并发”如何量化，压测分母和验收指标是什么？

---

## 9. 官方文档依据

- Deep Agents overview: https://docs.langchain.com/oss/python/deepagents/overview
- Deep Agents skills: https://docs.langchain.com/oss/python/deepagents/skills
- Deep Agents async subagents: https://docs.langchain.com/oss/python/deepagents/async-subagents
- Deep Agents permissions: https://docs.langchain.com/oss/python/deepagents/permissions
- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph streaming: https://docs.langchain.com/oss/python/langgraph/streaming
- MCP Python SDK client: https://py.sdk.modelcontextprotocol.io/client/
- LangChain MCP adapter: https://docs.langchain.com/oss/python/langchain/mcp

---

## 10. Definition of Done

v1.0 只有同时满足以下条件才算完成：

- legacy / graph / deep 三运行时可以从同一 API 启动；
- persistent checkpointer 在进程重启后恢复 interrupt；
- 工具副作用具有幂等账本；
- 写入采用 Diff 审批并检测 patch conflict；
- Skill 使用 progressive disclosure，并通过格式/路径校验；
- MCP 同时支持 stdio 与 Streamable HTTP，正确处理分页、多模态、structured content 和 `is_error`；
- Scheduler 有队列容量、分层 semaphore、取消、deadline 和工作区写锁；
- SSE 可断线续传 durable events；
- Windows 命令取消能够终止进程树；
- 20 个固定任务 × 3 次 × 3 runtime 的实验协议可复现；
- 并发压测证明限额、背压和无重复 side effect；
- README 的所有能力与指标都能指向测试或报告，不把计划写成已实现。
