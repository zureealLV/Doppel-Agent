# 代码审查测试策略

Doppel Agent 不把“模型返回了一段看起来合理的文字”当作任务完成，也不借用其他项目的成功率。当前验收分三层：

1. **确定性单元/集成测试**：离线覆盖 Provider 工具回合、权限拒绝、路径边界、对话上下文、SQLite 重启恢复、Windows DPAPI 密钥保护和 Web API。
2. **合成已知答案审查**：`bench/cases/review_001/service.py` 包含可执行测试证实的跨租户读取、SQL 注入和路径穿越。`bench/run_review.py` 只把被审文件交给 Agent，结果保留原文供人工判读。
3. **真实项目运行链路**：`bench/run_project_reviews.py` 在本机真实项目中执行只读审查，状态写到 Doppel Agent 自己的 `.bench-results/`，不污染目标项目。它只证明 Agent 能导航、读源码并完成回答；发现是否正确仍需人工复核。

## v0.8.2 重构基线

`bench/baselines/v0.8.2.json` 冻结了 LangGraph/Deep Agents 重构前的可复现基线：Git 版本、Python 版本、确定性测试分母、五类离线场景，以及真实模型记录的适用边界。后续 `legacy`、`graph`、`deep` 三种 runtime 必须复用同一任务、模型、权限和判定规则，不得因为实现不同而更换题目或提示词。

该基线只证明当时 checkout 的测试和已记录样本；它不自动证明新版本兼容，也不把历史单样本外推为通用成功率。

## 2026-09-19 实测快照

- 单元/集成：52 项通过，1 项因未安装可选 MCP SDK 跳过。
- 合成题：3 次 DeepSeek `deepseek-flash` 运行均读取目标文件，均覆盖 3/3 个预置缺陷；模型另外提出的发现不计入这三个预置缺陷的命中率，仍需独立人工判读。
- 真实项目：Doppel Agent、MedOps、Work Finder 三个项目均完成只读审查并读取源码，工具失败为 0。结果属于运行链路证据，不是“缺陷准确率”。

报告默认放在 `.bench-results/` 并被 Git 忽略；API Key 不写入报告。`read_file` 和 `write_file` 均阻止 `.env`、常见凭据/私钥文件、`.git`、`.doppel-agent` 等路径，避免审查时把本机秘密送给模型或篡改审计记录。

## v0.12 三运行时协议与负载证据

`bench/cases/runtime/manifest.json` 固定 20 个 case，`bench/runtime_matrix.py` 将其展开为 legacy/graph/deep 三种 runtime、每题三次，共 180 个唯一 run key。协议校验只负责冻结题目分类、权限和确定性 validator，不在执行前写入结果或成功率。

`bench/reports/2026-09-22-v0.12.0-runtime-smoke.json` 是使用离线 `MockProvider` 执行的 plumbing smoke：180/180 条 runtime path 完成，三种 runtime 各 60 次；所有 `task_score` 都是 `null`。这个分母只能证明统一接口和三条运行路径能完成调用，不能证明真实模型会完成题目，也不能用于成本或 Token 对比。

`bench/reports/2026-09-22-v0.12.0-load.json` 是本机确定性 scheduler/lock probe：100 个短任务在 `max_active=4` 下峰值为 4；queue overflow 明确拒绝；20 读 + 5 写保持 writer 单实例且无读写重叠；运行中取消进入 `cancelled`。它不覆盖 Provider 429、MCP 断连、慢 SSE、进程树或重启 lease，这些场景必须单独记录，不能从当前报告外推。

## v0.14 故障注入证据

`bench/fault_matrix.py` 固定 10 个离线场景，`bench/reports/2026-09-22-v0.14.0-fault-matrix.json` 实际记录 10/10 通过：Provider 429 + Retry-After、连接拒绝、读取超时、503 和单探针 half-open；MCP 模糊断连不重试、同版本重连后的 schema cache 失效；慢 SSE 从 `after_seq` 有序回放；服务重启把 queued/running lease 收敛为持久失败终态；命令超时后进程监督表归零。报告保留固定分母、原始观察值和逐场景失败原因字段。

这些场景使用 `httpx.MockTransport`、进程内 MCP fixture 与本地 SQLite，能够证明确定性故障策略，但不能证明真实 Provider/MCP 服务的网络可用性，更不能替代真实模型质量矩阵。进程父子树取消另由 Windows/POSIX 集成测试验证。

## 为什么当前不发布 SWE-bench 分数

[SWE-bench Verified](https://github.com/SWE-bench/SWE-bench/blob/main/docs/guides/datasets.md) 是从 SWE-bench 中经人工验证的 500 个任务。合格评测需要为每题检出指定仓库和基础提交，让 Agent 生成补丁，再在隔离环境中运行该题的 `FAIL_TO_PASS` 与 `PASS_TO_PASS` 测试。当前 Doppel Agent 还没有完整的仓库检出、补丁收集、容器隔离和官方 harness 接口，因此现在写一个“SWE-bench 成功率”会是伪数据。

接入顺序：

1. 每题使用独立 worktree/容器并固定 `base_commit`；
2. 禁止访问答案补丁与测试预期，只提供 issue 文本和仓库；
3. 收集 git patch，而不是靠模型自报完成；
4. 使用官方 harness 执行测试并记录错误、超时、成本、模型版本；
5. 先跑小规模开发集验证协议，再固定分母跑 Verified，失败样本也保留。

## 复现

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python bench/run_review.py --env-file .env
python bench/run_project_reviews.py --env-file .env
python bench/runtime_matrix.py --offline-smoke --output .bench-results/runtime-smoke.json
python bench/run_load.py --output .bench-results/load.json
python -m bench.fault_matrix --output .bench-results/fault-matrix.json
```

不要提交 `.env`、`.bench-results/` 或目标项目中的私密文件。
