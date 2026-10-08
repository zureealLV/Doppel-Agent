"""Pure cache projection definitions; no live transport or filesystem."""

from types import SimpleNamespace

from doppel_agent.mcp.catalog import MCPToolCatalog
from doppel_agent.mcp.types import MCPToolDescriptor


def test_cached_server_is_synchronous_generation_only_and_never_calls_get():
    calls = []
    manager = SimpleNamespace(generation=lambda name: calls.append(name) or 7)
    catalog = MCPToolCatalog(manager)
    assert catalog.cached_server('demo') == ('missing', None)
    tool = MCPToolDescriptor('mcp__demo__read', 'demo', 'read', 'read', 'text', {})
    catalog._cache['demo'] = ('historical_metadata_not_live_checked', 7, (tool,))
    assert catalog.cached_server('demo') == ('cached', (tool,))
    assert calls == ['demo']
    manager.generation = lambda _name: 8
    assert catalog.cached_server('demo') == ('stale_generation', None)
    assert catalog._cache['demo'][2] == (tool,)  # read never invalidates/discovers
