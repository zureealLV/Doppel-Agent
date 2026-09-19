# Doppel Agent

一个在本机运行的编程 Agent。可通过 Windows 桌面窗口、浏览器工作台或命令行连接模型、执行任务，并查看模型回合、工具调用与最终结果。

**语言：简体中文 · [English introduction](README_EN.md)**

> 当前版本：`v0.7.0`。项目仍在开发中；已实现的功能与后续计划分开列出，不以参考项目的指标作为本项目成绩。

## 功能

- **Agent Loop**：模型发起工具调用，Core 校验并执行，再把结果返回模型；单次运行有最大步数限制。
- **工具**：列目录、读取 UTF-8 文件、写入 UTF-8 文件、运行不经过 Shell 的 argv 命令。
- **权限**：默认只读；写文件和运行命令必须在本次任务中明确启用。命令工具不是操作系统沙箱。
- **模型接入**：支持 OpenAI-compatible Chat Completions；可填写 API Base URL、模型名和 API Key，也提供离线 Mock 用于功能测试。
- **Agent 工作台**：Codex 风格三栏界面将对话、聊天任务与工具活动分开呈现，模型设置收进独立弹窗。
- **持久对话**：消息自动写入 SQLite，支持搜索、重命名、删除和重启恢复；同一对话的历史会继续参与模型推理。
- **安全保存模型配置**：Base URL 与模型名保存在当前工作区；API Key 使用 Windows DPAPI 按当前用户加密，明文不会返回浏览器。
- **Windows GUI**：独立桌面窗口复用同一套本机工作台；无需使用 TUI，关闭窗口即停止该实例的本地服务。
- **运行记录**：每次任务生成独立 ID，并写入 `events.jsonl`、`trace.jsonl`、`session.json`。
- **任务与上下文**：SQLite 持久化任务依赖图；上下文到达估算水位时压缩旧工具回合并记录事件。
- **扩展**：工作区 `.doppel/skills/` 中的 Skill 可经校验后读取；可选 stdio MCP 服务通过显式配置接入。Web 中的写入、命令和 MCP 操作需要逐次人工批准。
- **只读子任务**：按次启用委派，子任务最多两次、每次最多四轮，只能读取工作区，不能继续委派；会产生额外模型调用与费用。
- **常驻 Core**：CLI 与 daemon 使用 localhost JSON-RPC/NDJSON 通信。

## 快速开始

要求：Windows 10、Python 3.11 或更新版本。基础运行时不依赖第三方 Python 包；MCP 扩展需额外安装 `mcp` SDK。

```powershell
cd '<你的 Doppel-Agent 仓库目录>'
.\doppel.cmd doctor
.\doppel.cmd ui
```

打开 [http://127.0.0.1:8766/](http://127.0.0.1:8766/)；点击左下角“模型与密钥”，填写 API Base URL、模型名称和 API Key，测试后保存，再开始对话。`ui` 仅监听 `127.0.0.1`。配置保存在当前工作区 `.doppel-agent/`；Key 由 Windows DPAPI 加密，只能由同一台电脑上的当前 Windows 用户解密，接口不会把明文发回页面。

### Windows 桌面 GUI（无需 TUI）

已构建的程序位于 `D:\Codex Program files\Agent\Doppel-Agent\.dist\DoppelAgent\DoppelAgent.exe`。保留同目录的 `_internal` 文件夹；双击 EXE 即可打开独立窗口。默认把启动时的当前目录作为工作区；指定其他项目时：

```powershell
& 'D:\Codex Program files\Agent\Doppel-Agent\.dist\DoppelAgent\DoppelAgent.exe' --workspace 'D:\your-project'
```

桌面 GUI 需要系统安装 Microsoft Edge WebView2 Runtime；Windows 10 上如果缺少它，程序会显示启动错误。它复用 Web 控制台的功能和安全边界，但使用随机的 `127.0.0.1` 端口，关闭窗口会停止这个实例。对话和加密后的 API Key 会保存在所选工作区。

重新构建：`./scripts/build-desktop.ps1`。脚本在项目 `.venv` 安装 `pywebview` 和 `PyInstaller`，产出无控制台窗口的 onedir EXE。也可通过 `python -m pip install -e '.[desktop]'` 后执行 `doppel-agent desktop --workspace 'D:\your-project'`。

不想先配置模型，可在服务类型中选择“离线 Mock”。它用于测试 UI 和执行链路，**不具备通用编程能力**。

### 命令行

```powershell
# 离线链路测试
.\doppel.cmd demo "read README.md"

# 真实模型：在当前 PowerShell 会话设置，不要提交密钥文件
$env:DOPPEL_AGENT_BASE_URL = 'https://api.deepseek.com'
$env:DOPPEL_AGENT_MODEL = 'deepseek-flash'
$env:DOPPEL_AGENT_API_KEY = '<你的 API Key>'
.\doppel.cmd ask '列出当前目录，并解释项目结构'

# 需要修改文件时，单次显式授权
.\doppel.cmd ask '创建 hello.py' --allow-write
```

Base URL 后会自动追加 `/chat/completions`。服务需要兼容 Chat Completions 的 `tools` / `tool_calls` 格式；不同提供商的模型名请以其文档为准。

### MCP 扩展

先安装 `python -m pip install -e ".[mcp]"`，再按 [MCP 配置说明](docs/MCP.md) 创建本机配置。Web 提交任务时勾选“允许 MCP 服务”，每次启动服务/调用工具都需检查实际命令并审批；CLI 则使用 `--allow-mcp`。**外部 MCP 服务作为当前用户运行，不受工作区文件边界约束。**

### 只读子任务

Web 勾选“允许只读子任务”，或 CLI 添加 `--allow-delegate`。该能力默认关闭；子任务共享本次任务的模型连接，限制为读文件/列目录、四轮模型调用和 8000 字符的返回值。它不是并行执行器，也不会继承父任务的写入、命令或 MCP 权限。

### 测试

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
```

测试包含本地 HTTP 模型替身的完整工具回合、Web 控制台接口、daemon/client 通信、路径越界与权限拒绝。没有配置真实 API Key 时，这些测试**不能**证明某个付费提供商的在线可用性。

### 真实代码审查验收

`bench/` 提供独立的合成代码审查样本：运行时只把样本 `service.py` 放入临时工作区，答案键不会暴露给 Agent。若已拥有 DeepSeek Key，可运行：

```powershell
python bench/run_review.py --env-file '<你的本机 .env 路径>'
```

报告存入忽略提交的 `.bench-results/`；密钥不写入报告。三轮合成题与三个真实项目的分层结果、SWE-bench 接入边界见 [代码审查测试策略](docs/BENCHMARK_STRATEGY.md)，早期逐项结果见 [审查验收记录](bench/reports/2026-09-19-review-001.md)。一个样本不能证明通用成功率。

## 安全边界

Web 控制台只向本机开放，拒绝跨站来源请求；API Key 仅以 Windows DPAPI 密文持久化。模型可能收到工具读取的文件内容，因此应只对可信工作区和可信模型服务启用读取。文件工具拒绝常见密钥文件和自身状态目录，但黑名单不能替代工作区审查。`--allow-command` 允许以当前用户身份运行程序，不应在不可信代码目录使用。

Web 已提供写入/命令/MCP 的逐工具审批（120 秒超时自动拒绝），但 CLI/daemon 不提供交互审批；其显式授权会直接生效。子任务仅在显式启用后可用，但不会再次弹出审批。上下文水位使用启发式 token 估算，不等同于模型精确 tokenizer。当前尚无强隔离、MCP HTTP 传输、并行子 Agent、TUI 或独立基准成绩。完整阶段与验收标准见 [实施规划](docs/PLAN.md)。

## 版本与来源

版本按功能迭代发布；每次推送应更新版本号、[更新记录](CHANGELOG.md)并通过测试。项目独立开发，设计上参考了 [TackleClaude](https://github.com/Tackle-B/TackleClaude) 对本地 Agent 运行时的公开介绍；没有复制其源码，也不沿用其成本、成功率等数据。
