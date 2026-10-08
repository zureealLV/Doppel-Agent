"""S9 fresh-process definitions: do not rely on pytest's prior import order."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("first", ["doppel_agent.graph.nodes", "doppel_agent.workspace.tool_adapter"])
def test_graph_and_patch_adapter_cold_import_without_loading_runtime_or_creating_state(tmp_path, first):
    source = Path(__file__).resolve().parents[1] / "src"
    script = f"""
import importlib
import sys
sys.path.insert(0, {str(source)!r})
importlib.import_module({first!r})
assert 'doppel_agent.runtime' not in sys.modules
from doppel_agent.events import EventSink, NullEventSink
from doppel_agent.runtime.base import EventSink as RuntimeSink, NullEventSink as RuntimeNull
assert EventSink is RuntimeSink and NullEventSink is RuntimeNull
from doppel_agent.runtime import create_runtime
from doppel_agent.graph import build_focused_graph
assert callable(create_runtime) and callable(build_focused_graph)
"""
    # Use the S9 environment's optional agent dependencies, but no inherited
    # provider credentials/PYTHONPATH, no repository cwd and no runtime instance.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
