# 代码审查测试策略

Doppel Agent 不把“模型返回了一段看起来合理的文字”当作任务完成，也不借用其他项目的成功率。当前验收分三层：

1. **确定性单元/集成测试**：离线覆盖 Provider 工具回合、权限拒绝、路径边界、对话上下文、SQLite 重启恢复、Windows DPAPI 密钥保护和 Web API。
2. **合成已知答案审查**：`bench/cases/review_001/service.py` 包含可执行测试证实的跨租户读取、SQL 注入和路径穿越。`bench/run_review.py` 只把被审文件交给 Agent，结果保留原文供人工判读。
3. **真实项目运行链路**：`bench/run_project_reviews.py` 在本机真实项目中执行只读审查，状态写到 Doppel Agent 自己的 `.bench-results/`，不污染目标项目。它只证明 Agent 能导航、读源码并完成回答；发现是否正确仍需人工复核。

## 2026-09-19 实测快照

- 单元/集成：52 项通过，1 项因未安装可选 MCP SDK 跳过。
- 合成题：3 次 DeepSeek `deepseek-flash` 运行均读取目标文件，均覆盖 3/3 个预置缺陷；模型另外提出的发现不计入这三个预置缺陷的命中率，仍需独立人工判读。
- 真实项目：Doppel Agent、MedOps、Work Finder 三个项目均完成只读审查并读取源码，工具失败为 0。结果属于运行链路证据，不是“缺陷准确率”。

报告默认放在 `.bench-results/` 并被 Git 忽略；API Key 不写入报告。`read_file` 和 `write_file` 均阻止 `.env`、常见凭据/私钥文件、`.git`、`.doppel-agent` 等路径，避免审查时把本机秘密送给模型或篡改审计记录。

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
```

不要提交 `.env`、`.bench-results/` 或目标项目中的私密文件。
