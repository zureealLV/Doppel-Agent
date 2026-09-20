# Doppel Agent v0.9.0 技术汇报

## 1. 项目定位

Doppel Agent 是一个面向本地代码审查与编程任务的 Windows Agent。目标不是复刻某个产品的外观演示，而是形成一条可运行、可追踪、可控权限、可保存对话的完整 Agent 链路：用户在桌面 GUI 中提交任务，模型按 ReAct 方式选择工具，Core 在权限边界内执行，再将结果返回模型继续推理，最终保存对话、运行事件和 Token 数据。

## 2. 技术栈与职责

| 层次 | 技术 | 当前版本/方式 | 主要职责 |
| --- | --- | --- | --- |
| 运行时 | Python | 3.11.15 | Agent Core、HTTP 服务、工具、持久化与桌面启动 |
| Agent 编排 | LangGraph + 自研 ReAct 基线 | LangGraph 1.2.11 / 有界 6/8/12 步 | checkpoint、interrupt/resume、工具回合与基线对照 |
| 模型协议 | OpenAI-compatible Chat Completions | `tools` / `tool_calls` | 接入 DeepSeek 等兼容服务，并提供离线 Mock |
| 本地服务 | FastAPI + Uvicorn；旧 `http.server` 兼容层 | FastAPI 0.141.1 | `/api/v1` 异步 API、OpenAPI、SSE 与旧 UI 同源迁移 |
| 数据持久化 | SQLite WAL + LangGraph Checkpointer | checkpoint-sqlite 3.1.1 | 对话、运行、事件、审批恢复、幂等账本与 schema migration |
| 并发治理 | asyncio bounded queue / semaphore / RW lock | 默认 4 active、100 queued | 背压、取消、工作区写互斥、Provider 与命令限流 |
| 前端 | 原生 HTML/CSS/JavaScript | 无前端框架 | 双栏默认工作台、全局搜索、审查模板、运行轨迹和对话管理 |
| 桌面容器 | pywebview | 6.2.1 | 将同一套 Web 工作台装入 Windows 原生窗口 |
| Web 内核 | Microsoft Edge WebView2 | 153.0.4234.48 | 渲染桌面端 HTML/CSS/JavaScript |
| 凭据保护 | Windows DPAPI | 当前用户作用域 | 加密保存模型 API Key，前端无法取回明文 |
| 打包 | PyInstaller | 6.22.3 onedir | 生成无控制台窗口的 Windows EXE |
| 验证 | pytest、Ruff、compileall | 86 passed | 回归 API、Graph 恢复、并发、Provider 可靠性与既有安全边界 |

## 3. 核心架构

```text
Desktop EXE / Browser
        │
        ▼
FastAPI /api/v1 ───── Durable SSE + RuntimeRunStore
        │
        ├──────────── AsyncRunScheduler + Workspace RW Lock
        │
        ├──────────── Legacy UI/API same-origin compatibility proxy
        │
        ▼
AgentRuntime
  ├── Legacy Core / bounded ReAct baseline
  └── LangGraph focused graph + SQLite checkpoint
        │
        ├── interrupt/resume + tool idempotency ledger
        ├── workspace tools + permission boundary
        └── async OpenAI-compatible Provider / Mock Provider
```

运行记录按任务 ID 写入 `.doppel-agent/runs/`，包含事件流、工具轨迹和会话结果。对话历史存入 SQLite，并会作为后续消息的真实模型上下文，而不是只在界面中展示。

## 4. v0.9.0 本轮交付

### 可恢复 LangGraph 主链

- 统一 `AgentRuntime`、`RunRequest`、`RuntimeResult` 和异步 `EventSink`，保留 legacy 作为同任务基线。
- Graph state 只保存可序列化数据；SQLite Checkpointer 开启 WAL、foreign keys、busy timeout 与 strict msgpack。
- 写文件、命令和 MCP capability 在副作用前进入 interrupt；支持 approve/reject/edit。
- `(run_id, tool_call_id)` 唯一幂等账本记录 running/completed/failed，恢复时复用成功结果并拒绝输入漂移。

### FastAPI、SSE 与并发

- `/api/v1/runs` 支持提交、查询、取消、事件回放和 interrupt resume；请求可带 idempotency key。
- Durable event envelope 包含全局递增 `seq`、run/thread、type、UTC 时间与 payload；SSE 支持 `after_seq`。
- 有界 FIFO scheduler 默认最多 4 个 active run、100 个等待任务；满载返回 429，不创建无限线程。
- 同工作区读任务可并发，写任务互斥；Provider profile 与命令各有独立 semaphore。
- 桌面入口由 FastAPI 暴露统一 loopback origin，尚未迁移的 v0.8 页面和接口通过内部兼容代理继续工作。

### Provider 可靠性

- Graph 模式使用长生命周期 `httpx.AsyncClient`，连接、读取、写入和连接池超时独立配置。
- 只重试网络错误、429 与 5xx，尊重 `Retry-After`，并受 max attempts、retry budget 与 run deadline 共同约束。
- 连续失败触发 profile 级 circuit breaker；`CancelledError` 单独传播，不被普通错误处理吞掉。

## 5. v0.8.2 历史交付

### 桌面与交互

- 使用 Ghostty 风格左上三色按钮，分别负责关闭、最小化、最大化/还原。
- 去除透明留白合成路径，WebView 和窗口背景使用同一深色；支持的 Windows DWM 直接绘制原生圆角，旧系统则安全退化为无白边窗口。
- 标题栏固定为不可滚动区域，工作区名称居中显示。
- 默认只显示对话栏和聊天区；右上角菜单负责显示/隐藏执行详情，不再出现与右栏重复的整页布局。
- 对历史保存的左右栏宽度重新执行 210–390 px、260–480 px 限幅，修复旧缓存造成的超宽溢出。
- 会话列表独立占用剩余高度并滚动，顶部导航和底部模型卡不再被挤走。
- `Ctrl+K` 打开全局搜索面板，服务端在 SQLite 中同时匹配标题和消息正文，并包含归档对话。

### 代码审查入口

- 点击“代码审查”只准备审查草稿并展示四种审查模板，不再直接向输入框写提示词，也不会自动请求模型。
- 服务端提供原子 `get_or_create_empty` 逻辑，“新对话”和“代码审查”连续点击均复用各自的空草稿。
- 前端增加提交锁，API 返回前再次发送不会产生重复运行。
- 运行记录绑定发起时的对话 ID；用户切换到其他对话后，任务完成不会把界面强制跳回错误页面。

## 6. 验收证据

- pytest：`86 passed`（Windows 沙箱外运行，包含 MCP stdio 命名管道测试）。
- Ruff correctness gate：`ruff check src tests` 通过。
- Python：`compileall` 通过。
- 新增覆盖：Graph 工具循环、checkpoint 重建、interrupt 三种决定、工具幂等、API idempotency、SSE replay、队列背压、取消、工作区锁、资源限流、Provider 429/circuit/cancel。
- JavaScript：`node --check src/doppel_agent/web/app.js` 通过。
- HTML 标签栈检查通过。
- 隔离工作区中连续点击“新对话”与“代码审查”：每类空草稿仅 1 个、消息数 0、运行数 0。
- Edge 实际渲染检查通过：默认双栏、右侧详情切换、全局搜索、审查模板、标题栏和输入区均可见，无白色外框或左上溢出。
- PyInstaller 6.22.3 已重新生成 v0.9.0 onedir EXE；隔离工作区冷启动 10 秒后进程仍存活，主窗口标题为 `Doppel Agent`，随后关闭测试进程。

## 7. 安全与成本边界

- Web 服务仅绑定 `127.0.0.1`，并校验 Host、Origin、Content-Type 和 UI 自定义请求头。
- API Key 使用 DPAPI 当前用户密文保存；临时更换 Base URL 时不会复用已保存 Key，避免将密钥发送到非档案地址。
- 文件操作限制在工作区，常见凭据、`.git` 和 Agent 状态目录默认拒绝读取。
- 写文件、命令和 MCP 在 Web 端需要运行级授权与逐工具审批。
- UI 展示真实 Token 与按用户配置单价估算的费用，但不在缺少同模型、同任务基线时宣称固定节省比例。

## 8. 当前边界与后续建议

当前版本尚不等同于强隔离执行环境：命令和外部 MCP 仍以当前 Windows 用户身份运行；同步 legacy Provider 被取消后，底层阻塞线程不能被 Python 强杀。Deep Agents、Agent Skills Registry、Streamable HTTP MCP Gateway 与并行子 Agent 属于 v0.10+，不得把依赖已安装写成能力已完成。后续按详细计划先做 Deep Agents spike，再做 Policy-controlled backend、Skill Registry 和 MCP session/catalog/executor。SWE-bench、吞吐或成功率数字只有在完成可复现实验和人工判定后才能写入项目成绩。
