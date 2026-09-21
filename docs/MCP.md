# MCP SDK Gateway

Doppel Agent v0.10 uses the official MCP Python SDK 2.x behind its own gateway. The model never receives a raw connection object: server sessions, discovery, schemas, permission checks, concurrency, execution, content normalization, audit, and shutdown remain application-owned.

## Install

```powershell
python -m pip install -e ".[agent]"
```

The locked release uses `mcp==2.2.0`. Doppel does not scan, download, or install MCP servers automatically.

## Configuration

Create `.doppel/mcp.json` inside the workspace. Do not commit service tokens.

### stdio

```json
{
  "servers": {
    "local-tools": {
      "transport": "stdio",
      "command": "C:/absolute/path/to/python.exe",
      "args": ["C:/absolute/path/to/trusted_server.py"],
      "cwd": "C:/absolute/trusted/working-directory",
      "env_names": ["SERVICE_TOKEN"],
      "max_concurrency": 2
    }
  }
}
```

`command` and optional `cwd` must be absolute. Arguments are passed as an argv array, not through a shell. Every name in `env_names` must exist in the environment that starts Doppel; the value is forwarded at connection time and is never stored in JSON.

Configs from v0.3-v0.9 that omitted `"transport": "stdio"` remain readable, but new configs should be explicit.

### Streamable HTTP

```json
{
  "servers": {
    "remote-tools": {
      "transport": "streamable_http",
      "url": "https://example.com/mcp",
      "auth_profile": "example-oauth",
      "max_concurrency": 4
    }
  }
}
```

Remote endpoints require HTTPS; plain HTTP is accepted only for localhost. `auth_profile` is an identifier, not a credential. For the example above, set the value in the launching PowerShell:

```powershell
$env:DOPPEL_MCP_AUTH_EXAMPLE_OAUTH_TOKEN = '<token>'
```

The gateway sends it as a Bearer token using an owned `httpx2.AsyncClient`. Missing profile variables fail closed before a connection is made. Interactive OAuth refresh is not implemented in v0.10.

## Runtime path

```text
.doppel/mcp.json
  -> MCPClientManager       session lifecycle / initialize / health / reconnect / shutdown
  -> MCPToolCatalog         paginated list_tools / schema hash cache / logical names
  -> MCPToolExecutor        validation / policy / semaphore / idempotency / call_tool / audit
  -> tool adapter           focused LangGraph Tool or Deep Agents BaseTool
  -> model                  mcp__<server>__<tool>
```

Server descriptions are treated as untrusted metadata. They are prefixed as such and never placed in a higher-priority system-instruction channel. Name collisions fail startup/discovery instead of silently selecting one server.

## Permission and approval

The v1 API loads MCP tools only when the run grants `permissions.mcp_execute=true`. Every normalized MCP tool carries the `mcp_execute` capability:

- focused `graph` mode enters its durable approve/reject/edit interrupt before execution;
- `deep` mode uses LangChain HITL before the gateway tool runs;
- resume uses the same run/thread checkpoint, and the idempotency ledger prevents replaying a completed side effect.

The legacy `mcp_list` and `mcp_call` tools remain as a compatibility facade, but route through the same SDK manager/catalog/executor. Legacy CLI/daemon explicit grants still do not provide an interactive second approval.

## Content handling

The executor checks MCP `is_error` before reporting success. It keeps two views:

- **model view:** bounded text plus safe placeholders for image/audio/resource blocks;
- **application view:** typed content blocks, `structuredContent`, MIME types, resource metadata, and `isError`.

Images, audio, resource links, and embedded resources are not forced through a nonexistent `.text` field. Audit events contain server/tool identity, sanitized arguments, outcome, and audit ID; credentials are excluded.

## Concurrency and failures

- Each server receives an independent semaphore from `max_concurrency`.
- A valid session is reused; discovery and health probes may close an invalid session and reconnect once.
- `call_tool` is **not** automatically retried: a disconnect after the server accepted a side effect is ambiguous, so blind retry could duplicate it.
- Catalog discovery traverses every `nextCursor` page and caches against server identity, protocol version, server version, config hash, and schema hash.
- Health checks use `list_tools`; they never invoke a side-effecting tool.
- Application shutdown closes HTTP sessions and stdio process transports.
- Cancellation is allowed to propagate; it is not converted into a retry.

Stdio servers run with the current Windows user's authority. Workspace path checks constrain Doppel's own file backend, not an external server. Only configure services and scripts you trust.

## Tests

```powershell
uv run --extra agent python -m pytest tests/mcp tests/integration/test_mcp_graph_tool.py tests/integration/test_mcp_deep_tool.py tests/integration/test_mcp_http_transport.py -q
```

Tests cover config rejection, lifecycle reuse/close, reconnect, pagination, schema invalidation, concurrency bounds, argument validation, multimodal normalization, `is_error`, idempotency, focused-graph approval, Deep Agents approval, a real loopback MCP 2.2 Streamable HTTP server, and the legacy stdio compatibility fixture.
