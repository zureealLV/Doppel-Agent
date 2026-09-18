# stdio MCP 接入

Doppel Agent 使用[官方 MCP Python SDK 的 Client/stdio transport](https://py.sdk.modelcontextprotocol.io/client/transports/)连接本机服务。当前只支持 stdio；不会自动扫描、下载或安装服务。

## 配置

安装可选依赖：

```powershell
python -m pip install -e ".[mcp]"
```

在**实际要运行任务的工作区**创建 `.doppel/mcp.json`（该文件已被 Git 忽略）：

```json
{
  "servers": {
    "example": {
      "command": "C:/absolute/path/to/python.exe",
      "args": ["C:/absolute/path/to/trusted_server.py"],
      "env_names": []
    }
  }
}
```

`command` 必须是已存在的绝对路径。`args` 原样传给该程序；`env_names` 只写环境变量**名称**，例如 `MY_SERVICE_TOKEN`，在启动 Doppel Agent 的 PowerShell 中设置值，不要把密钥写入 JSON。服务在工作区目录启动，但这**不是文件系统沙箱**：它仍拥有当前 Windows 用户的权限。只配置自己信任的服务与脚本。

## 使用与权限

- Web：勾选“允许 MCP 服务”后提交任务。`mcp_list` 和 `mcp_call` 每次都会出现人工审批卡片，展示实际配置的可执行路径、参数与转发的变量名称；120 秒未处理即拒绝。
- CLI：使用 `--allow-mcp` 明确启用。CLI 不提供交互式二次审批；启动前自行检查配置。
- daemon：`--allow-mcp` 还要求设置 `DOPPEL_AGENT_RPC_TOKEN`。该令牌只保护本机 RPC 调用，不限制 MCP 子进程的权限。

模型先用 `mcp_list` 查询服务器的工具名/输入 schema，再通过 `mcp_call` 传入 JSON 对象。每次调用会重新连接并关闭 stdio 服务器；当前没有长连接池、HTTP MCP、OAuth 或服务器侧资源/提示词集成。

测试：`python -m unittest discover -s tests -p test_mcp.py -v`。端到端测试会启动仓库内的可信 `tests/fixtures/mcp_echo.py` 服务，不连接外部网络。
