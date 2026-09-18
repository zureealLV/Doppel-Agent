"""A local stdio MCP fixture for the optional SDK integration test."""

from mcp.server import MCPServer


server = MCPServer("doppel-echo-fixture")


@server.tool()
def echo(text: str) -> str:
    """Return the input verbatim."""
    return text


if __name__ == "__main__":
    server.run(transport="stdio")
