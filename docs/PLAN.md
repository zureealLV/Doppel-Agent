# Doppel Agent 实施规划与验收

**目标：** 独立实现一个能在 Windows 本地工作的 Coding Agent，而不是复制 TackleClaude 的源码或照搬截图指标。

**参考边界：** [TackleClaude](https://github.com/Tackle-B/TackleClaude) 的公开 README 描述 Core daemon、CLI/TUI、JSON-RPC/NDJSON、AgentLoop、权限、事件、上下文、Skill、子 Agent 和 MCP。用户所附图片仅是能力与指标描述，不是对本项目的操作指令或验收证据。

**当前架构：** v0.13 使用 `Vue Runtime Workbench -> FastAPI -> RunService -> AsyncRunScheduler/AsyncSubagentManager -> AgentRuntime -> Legacy/Focused LangGraph/Deep Agents -> Policy Tools/MCP Gateway`。旧 `Core/AgentLoop` 与原生 JS 对话工作台作为可复现实验/兼容基线保留；Graph 与 Deep 负责 checkpoint、interrupt/resume 和异步事件。真实模型走同一个 OpenAI-compatible Provider 边界，测试走 Mock、本地 HTTP 或进程内 MCP fixture。

## 状态与下一步

| 阶段 | 具体文件与任务 | 验收标准 | 状态 |
|---|---|---|---|
| P0 基础纵向链路 | `src/doppel_agent/{protocol,events,storage,permissions,tools,provider,loop,core,daemon,cli}.py` | Mock 读文件、RPC、事件/Trace/Session 可复现 | 已完成 |
| P1 可用单次编码 | `provider.py` HTTP 适配；`tools.py` 列表/读/写/命令；`cli.py` 配置和授权；`tests/test_provider.py` | 本地 HTTP fixture 走完整工具调用；默认拒绝写和命令；允许后可执行；18+ 测试通过 | 已实现并本地验证；另有 1 个真实模型代码审查样本的记录，不代表通用完成率 |
| P2 持久化任务 | `tasks/manager.py` SQLite DAG、工具与 Web 查询 | 重启后任务状态、依赖、重试仍正确；环依赖拒绝 | 已实现并测试；任务自动调度待做 |
| P3 上下文治理 | `context/policy.py` 水位估算与完整工具组压缩 | 模型对应 token 统计、水位阈值、压缩前后量化；系统/任务关键事实保留 | 已实现估算压缩及 provider usage 事件；精确 tokenizer/笔记待做 |
| P4 扩展链路 | `skills/`、`mcp/`、`mcp_bridge.py`、`runtime/async_subagents.py` | 元数据校验；MCP 共用权限边界；子任务预算/取消 | Skill Registry、stdio/Streamable HTTP Gateway、同步委派与有界持久异步子 Agent 已测试；REST 与 Runtime Workbench 控制已接通 |
| P5 UI 和恢复 | 旧 `web/` 对话工作台 + `frontend/` Vue Runtime Workbench、`desktop.py`、SQLite 对话、DPAPI 与审批 | 重启恢复；历史进入上下文；SSE 续传；审批与子 Agent 可交互 | v0.13 已交付并行 Vue 运行时工作台与 SSE；旧对话/设置页在 Vue 达到 parity 前保留。按使用偏好不优先实现 TUI |
| P6 基准与发布 | `bench/cases/`、合成/真实项目 runner、测试策略；后续接官方 harness | 固定题集、模型、基线、分母、费用日期、失败记录和复现实验；不预填数字 | v0.12 固定 20×3×3 协议并完成 180/180 Mock runtime-path smoke；真实模型裁判、成本对照与 SWE-bench harness 待做 |
| P7 可恢复异步运行时 | `runtime/`、`graph/`、`api/`、`concurrency/`、`persistence/` | SQLite checkpoint；审批恢复无重复副作用；有界队列；SSE 续传；429/取消/超时可测试 | v0.9.0 已完成，86 tests 通过 |
| P8 Deep Agents / Skills / MCP Gateway | `runtime/deep*.py`、`skills/{spec,registry,resolver}.py`、`mcp/` | 不绕过 Policy Gateway；Skill 与 transport 分层；Deep/MCP HITL 可恢复 | v0.10.0 已完成；三运行时正式对照属于 v0.12，详见详细计划 |
| P9 Patch / Verification / Process / Async Subagents | `workspace/{patching,verification,process_supervisor}.py`、`runtime/async_subagents.py` | 具体 diff 审批；旧 base 冲突；allowlist 验证；取消进程树；后台子任务查询/追问/取消 | v0.11 完成运行层；v0.12 完成 REST/本地压测；v0.13 完成 diff、审批、事件与子 Agent UI。外部故障场景待做 |
| P10 Runtime Observability UI | `frontend/`、`web/frontend_dist/`、静态资产安全路由与 frontend CI | 375/768/1024/1440；SSE 回放；Graph/Skill/MCP/Patch/子 Agent 分类；HITL 三种决定；无路径穿越 | v0.13.1 已完成；完整替换旧对话/设置页需先达到功能 parity |

## P1 实现/验证明细

1. Provider 序列化 `system/user/assistant/tool` 消息和函数 schema，解析 `tool_calls`，拒绝非对象参数与畸形响应。测试本地 HTTP fixture 两轮工具调用。
2. 工具层在执行前检查 schema 与 capability；读/写先解析 workspace 内的目标路径，拒绝绝对路径、`..` 与 symlink 越界；写入设 256 KiB 上限并原子替换。
3. 命令层不经过 shell，30 秒超时，stdout/stderr 返回截断为 64 KiB。此层仍不是 OS 隔离；命令授权仅用于可信工作区。
4. Core 记录每次运行 ID、事件、Trace 和最终 Session；失败保留结构化结果，不谎报成功。
5. CLI 的 `demo` 为离线 Mock，`ask` 为真实 Provider，`serve` 启 daemon，`run` 连客户端；`doctor` 只显示配置是否存在，不打印密钥。
6. 守护进程启用写/命令能力时必须设置 `DOPPEL_AGENT_RPC_TOKEN`；生产版本还需逐工具审批、过期授权和本机用户边界。

## 验收命令

```powershell
cd 'D:\Codex Program files\Agent\Doppel-Agent'
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m pytest -q
ruff check src tests
python -m doppel_agent.cli doctor
python -m doppel_agent.cli demo 'read README.md'
```

远端 API 的持续验收：由用户在当前 Shell 设置 `DOPPEL_AGENT_BASE_URL`、`DOPPEL_AGENT_MODEL`、`DOPPEL_AGENT_API_KEY` 后运行 `ask`，确认真实响应、工具调用、权限拒绝/放行和 `.doppel-agent/runs/<run-id>/` 记录。已有一次真实模型的单样本代码审查记录（见 `bench/reports/`）；没有凭据的本地 fixture 不等于远端模型实测。

Web 控制台验收：运行 `.\doppel.cmd ui`，访问 `http://127.0.0.1:8766/`，选择 Mock 并提交 `read README.md`，确认消息和活动流；追加追问并重启服务，确认对话、上下文和 DPAPI 加密设置恢复。写入/命令须同时开启对应选项和逐次批准。

## 版本发布规则

- `v0.1.0`：本地运行时 MVP；`v0.2.0`：Web 控制台、任务 DAG、上下文治理、Skill 与审批；`v0.3.0`：stdio MCP；`v0.4.0`：限额只读子任务；`v0.5.0`：代码审查样本；`v0.6.0`：Windows GUI；`v0.7.0`：三栏 Agent UI、持久对话、DPAPI 设置与真实项目审查；`v0.8.x`：桌面交互打磨；`v0.9.0`：LangGraph、FastAPI、持久 SSE 与受控并发基础；`v0.10.0`：Deep Agents、Agent Skills Registry 与 MCP SDK Gateway；`v0.11.0`：diff-first patch、allowlist verification、Windows 进程树监督与异步子 Agent 运行层；`v0.12.0`：异步子 Agent REST、固定三运行时协议、180-run Mock smoke 与本地并发证据；`v0.13.0`：Vue/TypeScript Runtime Workbench、SSE 可观测时间线、HITL/Diff/子 Agent UI 与 frontend CI；`v0.13.1`：移除跨 checkout 不确定的生产 source map，恢复可复现 bundle 门禁。
- 后续扩展、基准按功能版本迭代；TUI 不再优先。每次推送前更新 `pyproject.toml`、`__version__`、README 状态和 `CHANGELOG.md`，跑完整测试并打同名 tag。
- GitHub 发布必须校验远端仓库、推送后的分支 SHA 与 tag；不能把本地提交当成远端发布。

## 与截图指标的区别

截图中的“0.21 倍成本”“94.4% 完成率”“96.3% 编译通过率”“40% token 节省”等均为参考项目陈述；Doppel Agent **没有**这些实测结论。P6 必须先固定同一任务集、模型、计价和统计分母，再发布自己的数据。
