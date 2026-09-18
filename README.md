# Doppel Agent

一个在本机运行的编程 Agent。用户可以通过命令行或 Web 控制台连接模型、执行任务，并查看模型回合、工具调用与最终结果。

**语言：简体中文 · [English introduction](README_EN.md)**

> 当前版本：`v0.2.0`。项目仍在开发中；已实现的功能与后续计划分开列出，不以参考项目的指标作为本项目成绩。

## 功能

- **Agent Loop**：模型发起工具调用，Core 校验并执行，再把结果返回模型；单次运行有最大步数限制。
- **工具**：列目录、读取 UTF-8 文件、写入 UTF-8 文件、运行不经过 Shell 的 argv 命令。
- **权限**：默认只读；写文件和运行命令必须在本次任务中明确启用。命令工具不是操作系统沙箱。
- **模型接入**：支持 OpenAI-compatible Chat Completions；可填写 API Base URL、模型名和 API Key，也提供离线 Mock 用于功能测试。
- **本地控制台**：配置、连接测试、任务提交、执行轨迹与结果集中在一个页面。API Key 不写入项目文件或浏览器存储。
- **运行记录**：每次任务生成独立 ID，并写入 `events.jsonl`、`trace.jsonl`、`session.json`。
- **任务与上下文**：SQLite 持久化任务依赖图；上下文到达估算水位时压缩旧工具回合并记录事件。
- **扩展**：工作区 `.doppel/skills/` 中的 Skill 可经校验后读取；Web 中的写入和命令操作需要逐次人工批准。
- **常驻 Core**：CLI 与 daemon 使用 localhost JSON-RPC/NDJSON 通信。

## 快速开始

要求：Windows 10、Python 3.11 或更新版本。运行时不依赖第三方 Python 包。

```powershell
cd '<你的 Doppel-Agent 仓库目录>'
.\doppel.cmd doctor
.\doppel.cmd ui
```

打开 [http://127.0.0.1:8766/](http://127.0.0.1:8766/)；左侧填写 API Base URL、模型名称和 API Key，点“测试 API 连接”，再提交任务。`ui` 仅监听 `127.0.0.1`。页面中的密钥只保留在当前页面内存，并随请求发送给本机 Core；Core 请求远端模型时会将其作为认证信息发送给你填写的服务。

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

### 测试

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
```

测试包含本地 HTTP 模型替身的完整工具回合、Web 控制台接口、daemon/client 通信、路径越界与权限拒绝。没有配置真实 API Key 时，这些测试**不能**证明某个付费提供商的在线可用性。

## 安全边界

Web 控制台只向本机开放，拒绝跨站来源请求；API Key 不持久化。模型可能收到工具读取的文件内容，因此应只对可信工作区和可信模型服务启用读取。`--allow-command` 允许以当前用户身份运行程序，不应在不可信代码目录使用。

Web 已提供写入/命令的逐工具审批（120 秒超时自动拒绝），但 CLI/daemon 不提供交互审批；其显式授权会直接生效。上下文水位使用启发式 token 估算，不等同于模型精确 tokenizer。当前尚无强隔离、MCP、子 Agent、TUI 或独立基准成绩。完整阶段与验收标准见 [实施规划](docs/PLAN.md)。

## 版本与来源

版本按功能迭代发布；每次推送应更新版本号、[更新记录](CHANGELOG.md)并通过测试。项目独立开发，设计上参考了 [TackleClaude](https://github.com/Tackle-B/TackleClaude) 对本地 Agent 运行时的公开介绍；没有复制其源码，也不沿用其成本、成功率等数据。
