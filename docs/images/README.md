# Screenshot provenance / 截图来源

The current README gallery was captured on **2026-10-08** from the actual Windows
`v0.15.0-rc.1` onedir EXE, not a mock HTML page, design render or edited image.
Source commit: `c94b95ce0fa2d88a8607ea13931c0ae6d5c644ed`.
EXE SHA256: `29bd8b9269abd8d45c77c435380be8f468dd139ec09ba3e8e2bff081ed4e07db`,
matching the [published prerelease](https://github.com/zureealLV/Doppel-Agent/releases/tag/v0.15.0-rc.1).

| Image | State shown |
|---|---|
| [Workbench](doppel-agent-v015-rc1-workbench.jpg) | Empty native conversation workspace; the displayed provider name does not mean a request was made. |
| [Plans](doppel-agent-v015-rc1-plans.jpg) | Manually entered, unsaved read-only example plan; no dispatch approval or execution. |
| [Help](doppel-agent-v015-rc1-help.jpg) | Actual build-version About dialog, entered through titlebar Help. |

Capture used the bundled Computer Use window API, exporting original JPEG bytes.
An isolated `sample-project` and isolated recent-project catalog were used; no
real project history, credentials or daily `.dist/DoppelAgent` files were used
for the gallery. No model connection test, task dispatch or MCP probe was made.
The plan was subsequently saved **without execution** so the application could
close normally; the screenshot records its earlier unsaved state. The window
and owned EXE process exited. See [machine-readable provenance](screenshots-v015-rc1.json)
for dimensions, image hashes and isolated run count.

These images demonstrate the visible interface only. They do **not** replace
whole native/lifecycle acceptance, establish model quality or change release
status. The historic v0.8.1/v0.8.2 images remain for old documentation but are
not the current README gallery.

中文：本组图片为已发布预览版 EXE 的原生窗口实拍，未修图、未拼接、未生成模型
成果。使用隔离示例项目与隔离最近项目目录；未提交模型/任务请求、未探测 MCP、
未读取真实项目历史或凭据，也未覆盖日常安装。计划截图记录的是手工填写、尚未
保存的草稿；之后仅保存草稿以正常退出，没有批准执行。截图只证明界面展示，
不等于完整原生矩阵或模型质量验收。
