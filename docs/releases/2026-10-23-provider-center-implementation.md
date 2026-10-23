# 提供商中心 P0/P1 实现、离线验证与隔离候选交接

日期：2026-10-23。仓库：`D:/Codex Program files/Agent/Doppel-Agent`。

## 结论与边界

本轮完成 P0-A → P0-B → P1-A → P1-B → P1-C 的实现和分阶段提交，交付原会话独立验收；**不是原会话已验收、不是发布或生产认证**。未 push、未创建 PR/Release、未替换日常 `.dist/DoppelAgent`。用户原始方案 `D:/Codex Program files/Agent/Doppel-Agent/docs/plans/2026-10-23-provider-center-next.md` 保持未跟踪、未修改，其“提案未实施”文字没有被悄悄改成验收结论。

- 起点：`cf4031de8da5a185cf2e51cc7f470f146e44b7e1`。
- 最终实现及候选源码提交：`473949298f1fbc9e021c9a1ee4af9ed4730ed647`。后续交接文档提交不改变打包源码。
- 只用临时配置、合成凭据、本机 HTTP fixture、MockTransport 与 Mock 运行时；没有读取/解密用户真实 Key，没有查询余额、复用浏览器 Token/Cookie 或外部付费生成。
- P2 连接/模型迁移、Responses/Anthropic adapter、OAuth、下载器均未实施。
- 审查并非空白：阶段独立 reviewer 的发现已逐条修复并补红→绿测试。但末轮根 Agent 契约/集成修复后，原 reviewer 因账号用量限制无法完成最终重新审查；**不声称 final independent all-clear**，请原会话重点复核下述缺口。

## 1. 已实现契约

### P0-A 凭据归属

`D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/settings.py` 统一 scheme/host/有效 port/base path + provider/preset/region/plan 身份；只消除明确等价格式，不折叠有语义路径。原身份变更且 Key 留空、不移除时，原子返回 `400 credential_reentry_required`，不半写、不解密、不请求新地址。同身份仅改名称/模型可保留 Key；显式新 Key 或移除才允许改变归属。新 revision 为不含 Key 哈希的 opaque 随机值。

`D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/runtime/service.py` 在解密、取缓存 client 前比较冻结归属/revision；历史运行不能拿新目标或替换后的 Key。`D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/provider.py` 禁止同步 urllib 和原生/注入 HTTPX 请求跟随重定向。preset/region/plan 变化也使旧计费声明失效，明确重新提交的声明除外；不等于官方价格认证。

历史无 binding 的密文只能按历史文件当时记录的归属处理，不能证明更早原始归属；无 revision 的旧快照不能获得后续新 revision 的凭据。

### P0-B 保存 / 当前应用 / 新对话默认

后端显式 `make_default: bool`；旧调用者省略保持原 True 兼容，当前前端始终明确发送。仅保存不改默认；保存并使用在保存后通过当前对话/空草稿选择链路确认；设默认不改已有对话。`saved_profile_id` 关联真实保存项，不能猜 active ID 即本次保存对象。

保存成功、应用未确认分步展示；迟到回复不自动采用。档案缺失、运行中、归档、切项目、关闭等待与 unknown sticky 保持受保护。仅对 save/discover 的受控、确定未写入 `400 credential_reentry_required` 允许纠正；不把任意失败洗成“未开始”。空草稿跟随默认与用户主动选择分别处理。

### P1-A 编辑器与草稿

抽取 `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ProviderConnectionEditor.vue`。预设地址为服务摘要 + 编辑入口，自定义端点直接编辑；高级计费/管理可达。常用主按钮与管理删除分开，OAuth 仅能力说明，本地图形不请求第三方 favicon。

切换有修改的条目需内联保留/放弃；Key 草稿仅组件内存和原请求闭包短暂持有，关闭/卸载清理，不做撤销密钥缓存。安全请求继续由 App lifetime 持有，隐藏/重开不能取消底层或消除未知结果。

### P1-B 显式模型发现

`D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/model_discovery.py` 与后端 `POST /api/models/discover`：明确 recipe 策略 `openai-list` / `ollama-tags` / `manual`；不盲拼全厂商 `/models`。自定义需显式策略；manual 不读凭据、不联网。远端 HTTPS / 本机 loopback 范围受控，不扫描局域网。

- 用户点“获取模型”才请求；打开弹窗、选提供商、填 Key 不触发元数据或生成调用。
- 父进程 10 秒工作 deadline 覆盖 spawn、DNS、headers/body、所有页；到期 terminate + join 所拥有网络子进程，收割有少量 OS 开销，不承诺严格 10 秒函数返回。不是前端 Promise.race。
- 跨页合计 1 MiB、1000 条、5 页；安全 ASCII 模型 ID 最长 256，拒绝 URL、控制符、路径遍历等。特殊目录 ID 被拒时仍可手动填写。
- OpenAI/Ollama 官方页面未要求这里的 cursor 扩展；兼容服务仅允许固定同源路径 + 编码 cursor，拒绝 next_url 和所有重定向。
- 前端目录最多 32 组内存缓存，按 canonical endpoint/provider/preset/region/plan/opaque credential revision/strategy 隔离并标时间，不存 Key 或可比对 Key hash。失败保留旧目录并标旧缓存，含 HTTP 503/连接拒绝路径。
- 发现响应 revision 与同一草稿代数/保存 revision 对应；过期保存 revision 在解密前拒绝，迟到不套到新 Key。
- `D:/Codex Program files/Agent/Doppel-Agent/scripts/desktop_entry.py` 的 main guard 先 `freeze_support()` 再导入/启动桌面；AST 回归和最终冻结 EXE 的 `/api/tags` 实际请求均已覆盖。

### P1-C 历史回执与安全诊断

后端公共字段为 `validation_receipt` / `validation_invalidated`，前端已按真实字段消费（初审发现的 receipt 字段错配有回归）。回执绑定 protocol + canonical identity + model + credential revision，记录 UTC 成功时间与 text/mock 种类；不保存响应正文，tools 恒 unknown，名称修改可保留，其余相关配置漂移立即失效。保存项成功 probe 才在写入时再次检查归属，未保存/并发已变不能污染历史。

`D:/Codex Program files/Agent/Doppel-Agent/frontend/src/providerConnectionState.ts` 表达未保存、保存未验证、验证中、上次成功、失败、未知、已变配置；只显示“上次验证”，不显示永久在线。401/403、限流、模型/端点、网络、超时、协议等返回固定安全建议，不回显 SDK 原始正文，不把 429 一律当余额不足。真实 HttpError 安全代码追加诊断但不清空请求未知状态；超时文案不谎称探测物理进程已退出。列模型 / 文本成功 / 工具验证三者明确分开。

### 全量回归追加的冻结工单修复

`D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/tasks/work_orders.py` 原字段白名单遗漏 P0 新冻结身份字段，导致 7 个真实工单集成失败；现在只允许 preset/region/plan/credential_revision 非秘密字符串精确保留，不额外接收任意秘密字段、不 trim 身份。另修复冻结 Mock 配置错误依赖当前设置而卡住恢复运行。新增 `D:/Codex Program files/Agent/Doppel-Agent/tests/test_work_order_credential_scope.py` 先红后绿；没有放宽已有安全断言凑通过。

## 2. 实际测试、失败与红绿证据

证据绝对目录：`D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023/evidence`。该目录和候选目录是被忽略的本地产物，**没有随 Git 推送**；需要远端交接时应单独带走，不能只给文档链接假装文件可下载。

| 切片 | RED → GREEN / 日志 |
|---|---|
| P0-A | 首轮 19 failed；原生/缓存/HTTPX 11 failed；最终 240 passed。`p0a-red.txt`、`p0a-native-red.txt`、`p0a-final-regression-rerun.txt` |
| P0-B 后端 | 15 failed → 117 passed。`p0b-backend-red.txt` / `p0b-backend-green.txt` |
| P0-B 前端 | 保存/应用、missing profile、空草稿默认、受控 400 分轮红绿，`p0b-*-red.log` / `p0b-final-green.log` / `p0b-controllers-green.log` |
| 计费归属 | 3 failed → 62 passed，`p0a-tariff-review-red.txt` / `p0a-tariff-review-green.txt` |
| P1-A | 隐藏 URL 行为先 1 failed / 17 passed；草稿审查再红绿，最终 mounted 20 passed。`p1a-red.log`、`p1a-review-red.log` / `p1a-review-green.log` |
| P1-B 服务 | 11 组行为红→绿；最终 114 passed（服务本身 44）。`discovery-01-red.log` 至 `discovery-11-green.log`、`discovery-final-tests.log` |
| P1-B 集成 | 后端 29 passed，独立前端切片 39 passed；陈旧缓存/受控错误审查 2 failed → 41 passed。`p1b-server-green.txt`、`discovery-review-stale-revision.log`、`p1b-slice-green.log`、`p1b-review-contract-red.log` / `p1b-review-contract-green.log` |
| P1-C | 后端 receipt/invalidated 先红→绿、累计定向 247 passed；前端字段错配 1 failed → 25 passed；真实错误代码链路 7 failed → 47 passed；IPv6/port 边界红→4 passed。`p1c-backend-red.txt`、`p1c-backend-final-green.txt`、`p1c-contract-*`、`p1c-diagnostics-*`、`p1c-endpoint-*` |
| 冻结工单 | 新用例 1 failed → 快速 7 passed；较大回归 83 passed / 1 审计失败，后者源码 HEAD 漂移另行重跑。`work-order-scope-red.log`、`work-order-scope-fast-green.log`、`work-order-scope-green.log` |

最终门禁：

- 前端 `npm test`：**62 files / 596 passed**，`frontend-full-final.log`。
- `npm run build`：vue-tsc + Vite 成功，`frontend-build-final.log`；548.82 kB JS chunk 超过 500 kB 建议阈值为非阻断警告，未冒充零警告。
- 方案指定五组 Python + 新增阶段组：**244 passed / 41.30s**，`plan-python-gate-final.log`。
- Python 最终全量：**2988 passed / 4 skipped / 2 warnings，769.66s（12:49），exit 0**，`python-full-final.log`。
- 全量 warnings 为 Starlette/anyio 弃用别名与既有错误输入 fixture 的 Pydantic bool 序列化警告；4 个 skip 仍按测试配置跳过，不声称覆盖了被跳过场景。
- 所有本轮改动 Python 文件 Ruff：**All checks passed**，`python-lint-final.log`；`git diff --check` 通过。
- 独立 scripted 任务审计：`task-fixture-audit-final.json` 的 `passed:true`、`source_unchanged_during_audit:true`，21 scripted runs；`full_matrix_ready:false`、`task_quality_scored:false`；只是 fixture 控制证据，不是模型质量/付费效果评分，也不是完整产品性能宣称。

保留的真实失败：首次 Python 全量 `python-full.log` 为 9 failed / 2973 passed / 4 skipped；其中 7 项工单字段遗漏与 1 项冻结 Mock 依赖已修复，另 1 项 audit 在运行中 HEAD 被提交改变导致 source provenance 不通过。定向重跑 audit 也曾与提交交叉，不能把这个失败删掉；已在固定 HEAD 下重跑独立 audit，并在全量最终重跑期间不改源码/HEAD。一次 P0 fixture Windows 10053 因 handler 未读 POST body 已修 fixture，未弱化异常断言。前端初次构建遇并行切片未落地的 union/type 错配，保留 `p1a-build.log`，后续成功日志另存。

## 3. 隔离浏览器与真实 Windows/WebView2

### 浏览器（不是原生 DPI 证明）

自有后台 headless Chromium，未操作 Lv 当前 Edge。1264×811、900×620、900×620 的有效 125%/150% CSS viewport + DPR 四组；Key focus、手动模型、预设 URL 折叠、Esc、主按钮在视口内通过。`final-browser.json` 记录几何，`final-1264x811-1.png`、`final-900x620-1.png`、`final-900x620-1.25.png`、`final-900x620-1.5.png` 为真实页面截图。900×620 小尺寸是浏览器证据，不冒充同尺寸原生测试。

### 真实原生交互（@oai/sky）

Windows 候选窗口截图 1264×811。候选 01（后被 02 替代）完成原生常规比例的本机发现、无 Key fixture 文本 probe、历史回执、模型漂移使验证失效、保存应用、Esc 和正常关闭；证据 `native-*-1.*`。fixture POST `/v1/chat/completions` 是本机合成回复，不是外部模型生成/工具调用证明。

**最终候选 02**完成：

- 125%：真实 Key 点击焦点、Tab 模型焦点、保存并应用 Mock 至当前空草稿；全局默认仍为本机 fixture，分别标识且有应用确认。`native-key-focus-1.25.jpg`、`native-model-focus-1.25.jpg`、`native-current-v-default-1.25.jpg` / `.txt`。
- 150%：Key focus、手动输入 `fixture/local-model`、键盘到“获取模型”并显式执行；最终冻结 EXE 子进程向 `127.0.0.1:11434/api/tags` 发无认证 GET，返回 2 条目录，UI 明确文本/工具未知。`native-key-focus-1.5.jpg`、`native-model-focus-1.5.jpg`、`native-discovery-1.5.jpg` / `.txt`、`native-fixture-requests.jsonl`。
- 两轮主按钮固定可见；内容使用内部滚动。150% 底部不可见元素不靠猜坐标点击，Tab/Shift+Tab 自动滚动后可操作，模型与高级/管理均可达。可访问树焦点常报告 RootWebArea，实际输入焦点以截图 focus ring/可见文字为准。
- 两轮 Esc 关闭弹窗，点应用关闭窗口后正常退出，未强杀；`native-exit-1.25-final.json`、`native-exit-1.5-final.json` 均为 process_absent/api_listener_absent true，重启同隔离工作区成功。即时 exit 日志可能仍处关闭中，最终日志才是退出判据。
- `owned-resources-final.json`：本轮候选进程、API 8661/3730/4636、本机 fixture 11434、浏览器测试服务 10571 均已消失，fixture/browser 服务正常退出。

**DPI 边界不能省略**：125%/150% 使用真实候选 WebView2 进程的 `--force-device-scale-factor=1.25/1.5`，不是纯浏览器截图，但也**没有修改 Windows 系统显示缩放，没有多显示器 per-monitor DPI 切换测试**。`native-webview-scale-1.5.json` 记录真实 WebView2 进程参数及 system_display_settings_changed:false。若最终验收要求物理系统 125%/150% 或跨屏迁移，原会话仍需单独验证。

当前原生保存/切换为无现有消息的空草稿；已有/归档/运行中对话与切项目链路由 controller/mounted/后端测试覆盖，未在本机原生逐一人工重演。一个已保存未验证状态可能重复显示“当前配置未测试”，属于文案小瑕疵，未为了隐藏它临末改包。

## 4. 唯一推荐候选：02

- 目录：`D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023-candidate-02`。
- EXE：`D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023-candidate-02/desktop/DoppelAgent/DoppelAgent.exe`，21,863,136 bytes。
- EXE SHA256：`76cc73cfafd137586d8015001e9de774eadf02d20e8c09d5511202122c4250cc`。
- wheel：`D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023-candidate-02/packages/doppel_agent-0.15.0rc1-py3-none-any.whl`，SHA256 `b678fbe31912857a1d358e2a7bcaf1da1bca3340ea21309416feb0545fbb46d4`。
- sdist：`D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023-candidate-02/packages/doppel_agent-0.15.0rc1.tar.gz`，SHA256 `0c20665c91ed72bd3511cc9ca7b423932eeabccc50bbce02deef9fa86e1a2ccb`。
- source manifest SHA256：`c674ed03adf15481359d6509dc1aa2fbe06cc793d0b732083a27d947c1271142`。
- `D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023/evidence/candidate-02-reconciliation.json` 已逐一核验当前 source_inputs 与 manifest 完全相同、全部交付物 hash 相同；不是只相信旧 manifest。
- `D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023-candidate-02/build-manifest.json` 保留 build-only 的 native_acceptance:false/app_launched:false/release_accepted:false：它只描述构建器阶段，没有为了原生截图改写构建证据。后续原生证据为上一节，仍不等于发布验收。
- 版本保留 **0.15.0rc1**；不是升版正式包。Python 3.11.15 / PyInstaller 6.22.3 / pywebview 6.2.1；Node 24.15.0 / Vue 3.5.43 / TypeScript 5.9.3 / Vite 7.3.6 / Vitest 3.2.7。
- 两轮构建 manifest 均 `build_success:true`、`daily_app_unchanged:true`、`dependencies_downloaded:false`。日常文件按内容/尺寸/mtime 在每次构建前后逐文件比较；最终 inventory 保存 `daily-app-final-inventory.json`，未操作日常路径。没有声称跨全程额外保存了初始完整 inventory。
- 候选 01 是工单修复前产物，保留其日志但**不要用它验最终源码**；01 EXE SHA256 `b18a90bcd04ed017c2dd1b81abce7dcce3abbfbb56373cbdc33296b1e1617d0a`。

## 5. 复现命令

以下是已执行的主要命令形态；原始 red/green 日志与构建 manifest 保留各阶段真实输出。离线执行前移除进程环境中的真实凭据变量，不读取其值，不使用用户日常工作区。

```powershell
Set-Location 'D:/Codex Program files/Agent/Doppel-Agent'
$env:PYTHONPATH='src'
Get-ChildItem Env: | Where-Object { $_.Name -match 'API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD' } | ForEach-Object { Remove-Item -LiteralPath ('Env:'+$_.Name) }
./.venv/Scripts/python.exe -m pytest -q tests/test_settings_price_boundaries.py tests/test_billing_tariff_settings.py tests/test_settings_persistence_lifetime.py tests/test_settings_file_source_retention.py tests/test_provider_probe_cleanup.py tests/test_settings_credential_binding.py tests/test_settings_default_intent.py tests/test_model_discovery.py tests/test_model_discovery_server.py tests/test_provider_validation_receipts.py tests/test_discovery_frozen_entry.py
./.venv/Scripts/python.exe -u -m pytest -q
Set-Location 'D:/Codex Program files/Agent/Doppel-Agent/frontend'
npm test
npm run build
Set-Location 'D:/Codex Program files/Agent/Doppel-Agent'
./.venv/Scripts/python.exe scripts/build_candidate.py --output .artifacts/provider-center-20261023-candidate-02
```

构建输出必须为**新的** `.artifacts` 子目录；02 已存在，复现时请另命名，不能删除它来伪装同一次构建。构建命令仅构建，不运行测试/启动应用。

隔离原生启动由 `D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023/start_native.ps1 -Scale 1.25` / `-Scale 1.5` 指向候选 02，并使用 `D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023/native-workspace` 与独立 native-catalog.json；该脚本不是发布入口。fixture 已停止；辅助脚本内仍有隔离配置初始化及 stop marker，复跑应复制到新隔离目录、检查初始化目标并移除该副本的停止标志后再启动，不能当无副作用的正式启动器。不要误把真实本地服务当合成 fixture。

## 6. 原会话独立验收建议

1. 先复核 source_inputs/hash 与本轮 38 文件改动，再看下一节完整提交 SHA/文件，不把用户未跟踪方案加入提交。
2. 对末轮根 Agent 修复重点重审：真实 `validation_receipt` 字段、HttpError 受控诊断与 unknown sticky、旧目录失败保留、精确 400、discovery revision、工单冻结白名单与 Mock 路径。
3. 用合成 Key + 两个本机 origin 再测旧凭据不流向新目标/redirect；原本无真实凭据授权不因此解除。
4. 候选 02 用隔离工作区重复保存/当前/默认、慢请求关闭与退出；如验收要求，再补系统 DPI/per-monitor 与已有原生对话交互。勿用浏览器/Mock 当替代证据。
5. 只有原会话确认后另行决定 push/替换/发布；本次不执行这些动作。

## 7. 核验过的官方元数据接口来源（2026-10-23）

- [OpenAI list models](https://developers.openai.com/api/reference/resources/models/methods/list)
- [Ollama list local models](https://docs.ollama.com/api/tags)
- [DeepSeek list models](https://api-docs.deepseek.com/api/list-models/)

这些是协议依据，不是用户账号已实测的证明。

证据文件的 SHA256/尺寸清单：`D:/Codex Program files/Agent/Doppel-Agent/.artifacts/provider-center-20261023/evidence/evidence-index.json`，用于本地交接时校验文件，而非远端已上传的声明。

## 8. 完整分阶段提交与逐文件范围

下列清单由 Git 从指定基线至最终实现提交直接生成；文档交接提交另列于交接回复，不让文档自引用一个尚未存在的 SHA。

### `edd84292dad06b3692242efae40117b23030f542`

fix(provider): bind saved credentials to destination and contract

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/provider.py`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/runtime/service.py`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/settings.py`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/server.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_settings_credential_binding.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_web.py`

### `6e797566ae220f6197d207f6b3985b972390dd2b`

feat(provider): separate saving profiles from setting defaults

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/settings.py`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/server.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_settings_default_intent.py`

### `348ca17082aa1fecaab163d4689f0db94f280da4`

feat(provider): separate save, conversation application and new-chat default

- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ModelSettings.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettings.mounted.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettingsLifetime.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettingsLifetime.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/providerApplication.mounted.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspace.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspace.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspaceApi.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspaceTypes.ts`

### `d974ef29308141d7e148626a4ae70f5029a86e78`

fix(provider): invalidate tariffs when plan or region changes

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/settings.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_settings_credential_binding.py`

### `c7d139f2f926690d7dfe6bc8d10e164320543532`

feat(provider): simplify connection editor and protect unsaved drafts

- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ModelSettings.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ProviderConnectionEditor.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettings.mounted.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/styles.css`

### `1b7cb051b7547abf298309518f5e519806352ba7`

feat(provider): add bounded explicit model metadata discovery

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/model_discovery.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_model_discovery.py`

### `66f7027793175685920537d50dc0077852a01c2b`

feat(provider): expose explicit owned model metadata discovery

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/server.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_model_discovery_server.py`

### `855587ac35deb4f5e8e0f477695f8791d63d0160`

fix(provider): correlate discovery results with credential revisions

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/server.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_model_discovery_server.py`

### `35081e89c8c31f8bc821a57c6a009aba4af63f8a`

feat(provider): persist exact probe receipts and invalidate changed configs

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/settings.py`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/server.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_provider_validation_receipts.py`

### `5b0b714fff11485e13022c941c63617fcc23d6c5`

feat(provider): add explicit cached model picker with owned discovery

- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ModelSettings.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ProviderConnectionEditor.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelDiscovery.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelDiscovery.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettings.mounted.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettingsLifetime.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/providerCatalog.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspaceApi.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/scripts/desktop_entry.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_discovery_frozen_entry.py`

### `f255536a184451294b1a0af3d601d2f7be3a2e1d`

feat(provider): explain historical verification and safe probe failures

- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ConversationWorkspace.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/LegacyConversationWorkspace.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ModelSettings.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/components/ProviderConnectionEditor.vue`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettings.mounted.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/modelSettingsLifetime.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/providerConnectionState.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/providerConnectionState.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/providerDiagnosticsLifetime.test.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspaceApi.ts`
- `D:/Codex Program files/Agent/Doppel-Agent/frontend/src/workspaceTypes.ts`

### `f349897519beee009e1e05b421f44082622c537e`

build(web): refresh provider center bundled frontend

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/frontend_dist/assets/index-Bw6pVJBa.css`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/frontend_dist/assets/index-D3fKcutw.css`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/frontend_dist/assets/index-DoqTK2sH.js`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/frontend_dist/assets/index-R48vNbt-.js`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/web/frontend_dist/index.html`

### `473949298f1fbc9e021c9a1ee4af9ed4730ed647`

fix(runtime): preserve credential scope through work-order replay

- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/runtime/service.py`
- `D:/Codex Program files/Agent/Doppel-Agent/src/doppel_agent/tasks/work_orders.py`
- `D:/Codex Program files/Agent/Doppel-Agent/tests/test_work_order_credential_scope.py`
