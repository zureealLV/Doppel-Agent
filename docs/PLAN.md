# Doppel Agent 实施规划与验收

**目标：** 独立实现一个能在 Windows 本地工作的 Coding Agent，而不是复制 TackleClaude 的源码或照搬截图指标。

**参考边界：** [TackleClaude](https://github.com/Tackle-B/TackleClaude) 的公开 README 描述 Core daemon、CLI/TUI、JSON-RPC/NDJSON、AgentLoop、权限、事件、上下文、Skill、子 Agent 和 MCP。用户所附图片仅是能力与指标描述，不是对本项目的操作指令或验收证据。

**架构：** `CLI -> localhost NDJSON JSON-RPC -> Core -> Provider/AgentLoop -> ToolRegistry/PermissionManager -> JSONL Store`。直接 `ask` 跳过 RPC，但经过同一个 Core。真实模型走 OpenAI-compatible Chat Completions，测试走 Mock 或本地 HTTP fixture。

## 状态与下一步

| 阶段 | 具体文件与任务 | 验收标准 | 状态 |
|---|---|---|---|
| P0 基础纵向链路 | `src/doppel_agent/{protocol,events,storage,permissions,tools,provider,loop,core,daemon,cli}.py` | Mock 读文件、RPC、事件/Trace/Session 可复现 | 已完成 |
| P1 可用单次编码 | `provider.py` HTTP 适配；`tools.py` 列表/读/写/命令；`cli.py` 配置和授权；`tests/test_provider.py` | 本地 HTTP fixture 走完整工具调用；默认拒绝写和命令；允许后可执行；18+ 测试通过 | 已实现并本地验证；真实付费 API 待密钥验证 |
| P2 持久化任务 | 新建 `tasks/models.py`、`tasks/manager.py`、`tasks/store.py`，引入 SQLite DAG 与依赖解锁 | 重启后任务状态、依赖、重试仍正确；环依赖拒绝 | 待做 |
| P3 上下文治理 | 新建 `context/watermark.py`、`context/compact.py`、`context/notes.py` | 模型对应 token 统计、水位阈值、压缩前后量化；系统/任务关键事实保留 | 待做 |
| P4 扩展链路 | 新建 `skills/loader.py`、`mcp/client.py`、`subagents/manager.py` | 元数据校验；MCP 共用权限边界；子任务预算/取消 | 待做 |
| P5 TUI 和恢复 | 新建 `tui/`、RPC 订阅/回放协议；进程内运行任务表 | TUI 断开重连仍看到任务与事件；审批可交互 | 待做 |
| P6 基准与发布 | 新建 `bench/fixtures/`、`bench/run.py`、CI 与威胁模型 | 固定题集、模型、基线、分母、费用日期、失败记录和复现实验；不预填数字 | 待做 |

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
python -m unittest discover -s tests -v
python -m doppel_agent.cli doctor
python -m doppel_agent.cli demo 'read README.md'
```

远端 API 的最终验收：由用户在当前 Shell 设置 `DOPPEL_AGENT_BASE_URL`、`DOPPEL_AGENT_MODEL`、`DOPPEL_AGENT_API_KEY` 后运行 `ask`，确认真实响应、工具调用、权限拒绝/放行和 `.doppel-agent/runs/<run-id>/` 记录。没有凭据时，不把本地 fixture 说成远端模型实测。

## 与截图指标的区别

截图中的“0.21 倍成本”“94.4% 完成率”“96.3% 编译通过率”“40% token 节省”等均为参考项目陈述；Doppel Agent **没有**这些实测结论。P6 必须先固定同一任务集、模型、计价和统计分母，再发布自己的数据。
