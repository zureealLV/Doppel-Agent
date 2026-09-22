# 更新记录

## v0.12.0 — 2026-09-22

- 将 v0.11 的持久异步子 Agent 运行层接入 FastAPI：父 run 必须显式授予 `delegate`，随后可通过嵌套路由创建、列出、查询、追问和取消后台子任务；子任务仍固定只读、禁止 MCP/命令/写入和递归委派。
- 子 Agent 生命周期事件写入父 run 的 durable event timeline；追问复用同一 ID 和历史，服务关闭会先收敛后台子任务再关闭主 scheduler。
- 固定 `3 runtimes × 20 cases × 3 repeats = 180 runs` 协议，覆盖导航、已知答案审查、TDD 修复、多文件补丁、审批恢复、MCP 与并发取消；manifest 校验拒绝重复 ID、分类漂移、无验证器和预填成绩。
- 实际完成 180/180 次 Mock runtime-path smoke（legacy/graph/deep 各 60）；报告明确将 `task_score` 留空，不把离线 Mock 冒充模型质量、成本或 Token 基准。
- 新增确定性本地并发探针：100 任务、`max_active=4` 的峰值为 4；队列溢出明确拒绝 1 次；同工作区 20 读 + 5 写中 writer 峰值为 1 且读写重叠为 0；运行中取消进入 terminal cancelled。
- scheduler 暴露 accepted/rejected/peak-active 观测计数；新增 API、矩阵协议和负载回归测试，发布前本地门禁为 139 passed、1 skipped。

## v0.11.0 — 2026-09-21

- 将 Graph/Deep 的文件修改统一为 `propose_patch` diff-first 链路：审批前只生成含 base SHA-256 的 unified diff，批准或编辑后才原子替换；旧 base 变化会产生明确 conflict，多文件中途失败会回滚已经替换的文件。
- Deep Agents harness 移除内置 `write_file`/`edit_file`，自定义 LangChain patch adapter 将审核元数据跨 runtime 重建持久化，防止 Deep 模式或服务重启绕过具体补丁审批。
- 新增 `.doppel/verification.json` allowlist pipeline；批准的补丁在本次 run 同时授予命令能力时自动执行固定 argv，返回 exit code、stdout/stderr、耗时、监督模式和整体结果，模型不能临时拼 shell 字符串。
- 命令执行改为 `ProcessSupervisor`：Windows 优先 Job Object，并记录 `job_object` 或 `taskkill_fallback`；POSIX 使用独立 process group。取消、超时和服务关闭都会终止整个进程树。
- 新增同进程 `AsyncSubagentManager`：有界 FIFO 并发、SQLite 生命周期、查询、追问、取消和重启时未完成任务收敛；子请求固定只读且禁止递归委派。
- 新增 `patch.proposed`、`patch.applied`、`patch.conflict` 持久事件，以及补丁、Deep 恢复、验证、进程树取消和异步子 Agent 集成测试。

## v0.10.0 — 2026-09-21

- 增加 `mode=deep`：Deep Agents 0.7.15 通过自定义 `DoppelChatModel` 复用现有 Provider，使用 SQLite checkpoint、持久 interrupt/resume、事件映射与显式 focused-graph fallback。
- 增加 `DoppelBackend`：虚拟工作区路径、symlink 二次解析、秘密/`.git`/`.doppel-agent` 拒绝、只读默认值和写入审计；harness 排除 Deep Agents 内置 shell。
- Deep 子 Agent 最多两个，分别拥有只读权限、6 次模型调用上限和 8000 Token 预算，且不能递归委派。
- 将旧 Skill Loader 升级为严格 Agent Skills Registry/Resolver；支持 progressive disclosure、唯一名称、行数/大小/链接/疑似密钥检查，并新增 code-review、bugfix、test-repair、mcp-operations 四个内置 Skill。
- 新增 MCP SDK Gateway：stdio 与 Streamable HTTP、session 生命周期、无副作用健康检查/重连、分页 catalog、schema hash cache、每服务器 semaphore、参数验证、幂等账本、审计及多模态/structured content/`is_error` 处理；有副作用的 `call_tool` 遇到模糊断连不会盲目重试。
- MCP 工具规范化为 `mcp__server__tool`，同时接入 focused LangGraph 和 Deep Agents；两条链路均在执行前进入可恢复 HITL。旧 `mcp_list`/`mcp_call` 保留为网关兼容 facade。
- HTTP MCP `auth_profile` 只映射环境变量 `DOPPEL_MCP_AUTH_<PROFILE>_TOKEN`，配置文件不保存令牌；旧版省略 `transport` 的 stdio 配置仍可读取。
- 修复同一微秒内创建任务时 UUID 排序破坏 DAG 创建顺序的问题；任务列表现在使用 SQLite 插入序列稳定排序。
- 增加离线 Deep Agents spike、专项研究记录和 Deep/Skill/MCP/API 集成测试；正式三运行时 benchmark、diff-first patch、verification pipeline 与异步并行子 Agent 留待后续版本。

## v0.9.0 — 2026-09-20

- 保留 v0.8.2 自研 ReAct 作为 `legacy` 基线，新增统一 `AgentRuntime` 契约与 `graph` 模式；主链使用 LangGraph 1.2.11 和 SQLite Checkpointer，支持跨 runtime 重建恢复状态。
- 写文件与命令进入 LangGraph `interrupt/resume` 审批；支持批准、拒绝、编辑参数，并用 `(run_id, tool_call_id)` 幂等账本阻止恢复时重复副作用，过期审批明确进入 `interrupted_expired`。
- 新增 FastAPI `/api/v1/runs` 生命周期接口和持久化 SSE：事件使用严格递增序号，支持 `after_seq` 断线续传；v0.8 工作台/API 通过同源兼容代理继续可用。
- 新增有界 FIFO `AsyncRunScheduler`、取消令牌、工作区读写锁、Provider/命令资源信号量；队列满明确返回 HTTP 429，桌面端不再依赖固定两线程池承载新运行时。
- 新增长生命周期 `httpx.AsyncClient` Provider：拆分连接/读取/写入/连接池超时，429/5xx 有界重试并尊重 `Retry-After`，带 retry budget、run deadline 和 profile 级 circuit breaker。
- 新增运行/事件 SQLite schema migration、WAL/foreign keys/busy timeout、幂等请求键与结构化状态；CI 增加 Ruff correctness gate，完整离线套件为 86 tests。
- 冻结 v0.8.2 基线及 LangGraph/Deep Agents/Agent Skills/MCP SDK/受控并发的后续实施计划；Deep Agents、Skill Registry 与新 MCP Gateway 仍属于 v0.10.0，不在本版冒充已完成。

## v0.8.2 — 2026-09-19

- 去除透明窗口留白造成的白色外框，改用与标题栏同色的实体 WebView 背景，并在支持的 Windows DWM 上请求原生圆角。
- 工作台默认显示左侧对话栏与中央聊天区；右上角菜单只负责显示/隐藏执行详情，不再提供重复的整页详情布局或左栏隐藏按钮。
- “新对话”和“代码审查”均原子复用对应的空草稿，防止快速连点产生多个空白对话。
- 代码审查入口改为四种审查模板选择，用户选择后才把提示词写入输入框，仍需手动发送才调用模型。
- 增加 `Ctrl+K` 全局对话搜索，可检索标题、消息正文及归档对话；移除无实际操作价值的“本地核心在线”状态。

## v0.8.1 — 2026-09-19

- 桌面窗口改为 Ghostty 风格的左上三色窗口按钮、透明圆角边框与居中工作区标题；最大化时自动取消圆角留白。
- 修复旧侧栏宽度缓存和最小窗口宽度造成的网格溢出，并固定标题栏与侧栏滚动边界，避免内容把顶栏挤出窗口。
- “代码审查”现在只打开并填充一个可复用草稿，必须由用户点击发送才会运行；服务端原子复用空草稿，连续点击不会继续创建对话。
- 增加运行提交锁与运行所属对话跟踪，防止快速连点重复提交，以及任务完成后错误跳转到其他对话。

## v0.8.0 — 2026-09-19

- 桌面端改为无原生白色标题栏的 Codex 式界面，移除品牌方块与荧光配色；左右侧栏可拖动调整宽度，并可单独收起。
- 增加对话分组、归档/恢复、管理弹窗；同一工作区可保存多个自定义 API/模型档案，并在对话输入区切换模型。
- 增加工作区文件图、递归文本搜索和按行读取工具；快速/均衡/深度运行分别限制为 6/8/12 步。
- 执行面板显示耗时、工具调用、Token 与按用户配置单价计算的费用，避免用未经对照的百分比宣称节省。
- 为 Windows EXE 加入原创 Doppel 图标，并把图标资源打包进桌面程序。

## v0.7.0 — 2026-09-19

- 将 Web/桌面端重构为 Codex 风格三栏 Agent 工作台：对话列表、聊天任务区与工具活动流分离，设置收进弹窗，改善拥挤问题。
- 新增 SQLite 持久化对话：自动保存用户/助手消息，支持搜索、重命名、删除与重启恢复；后续消息会把同一对话历史真正发送给模型。
- 模型配置持久化到工作区；API Key 使用 Windows DPAPI 当前用户加密，前端和 GET 接口均无法取回明文，可随时删除。
- 增加真实项目只读审查脚本和测试策略文档；用 DeepSeek `deepseek-flash` 完成 Doppel Agent、MedOps、Work Finder 三项目运行链路测试。
- 默认阻止读写 `.env`、常见凭据/私钥文件、`.git` 与 `.doppel-agent`，避免审查泄密或篡改自身状态；Agent 状态可写到目标项目外部。

## v0.6.0 — 2026-09-19

- 将 Web 工作台改成浅色任务布局：模型配置、任务输入、最近运行、结果和轨迹分别呈现；移除装饰性英文标签。
- 新增 Windows 桌面 GUI：pywebview 在独立窗口中加载同一套本机控制台，后台使用随机 loopback 端口，不弹出 TUI/控制台。
- 提供 PyInstaller 构建脚本；已在 Windows 10 启动 GUI EXE 并完成 Mock 工具调用验收。

## v0.5.0 — 2026-09-19

- 加入隔离的合成代码审查样本、可复现的 DeepSeek 只读验收脚本、缺陷复现测试和人工评分规则。
- 实际完成两轮模型审查（引导/盲测），记录模型报告的 token 用量与逐项人工判读；不将单一样本外推为总体成功率。
- 修复 Windows 下拒绝跨站 POST 时偶发连接重置的问题，稳定返回明确的 403/415 响应。

## v0.4.1 — 2026-09-18

- 修复“最近运行”在较小窗口中被 Flex 布局压扁的问题；增加可读的行高、状态标签和独立滚动区域。

## v0.4.0 — 2026-09-18

- 增加显式启用的只读子任务委派；每次运行最多委派两次，每个子任务最多四轮模型调用。
- 子任务只能使用工作区读文件与列目录工具，不可写入、运行命令、调用 MCP 或继续委派。
- 运行记录包含子任务开始、内部事件、结束与失败；Web 和 CLI 均可按本次任务启用委派。

## v0.3.0 — 2026-09-18

- 增加可选的 stdio MCP 桥接，使用官方 Python SDK；服务仅能从显式配置的绝对可执行路径启动。
- Web 端 MCP 能力默认关闭，启用后每次列表/调用仍需人工审批；CLI/daemon 需显式授权。
- MCP 环境变量只按名称转发，配置文件不保存密钥；增加本地 stdio 服务端到端测试。

## v0.2.0 — 2026-09-18

- 新增本机 Web 控制台：用户填写 API Base URL、模型、API Key，测试连接、提交任务、查看执行轨迹。
- Web 控制台默认只读，可按任务开启写文件或命令能力；密钥不落盘。
- 增加本地 HTTP API 与页面端到端测试。
- 加入任务依赖图持久化、估算上下文水位压缩、工作区 Skill 读取，以及逐工具人工审批。
- 控制台新增最近运行列表，可在服务重启后回放已完成的结果和事件。

## v0.1.0 — 2026-09-18

- 建立 CLI、Core daemon、JSON-RPC/NDJSON、Agent Loop、工具权限与 JSONL 记录的最小运行链路。
- 加入 OpenAI-compatible Provider 和离线 Mock，验证模型工具调用回合。
