"""Frozen metadata workers must not recursively launch the desktop."""
import ast
from pathlib import Path


def test_frozen_entry_dispatches_worker_before_importing_desktop():
    tree = ast.parse((Path(__file__).parents[1] / 'scripts/desktop_entry.py').read_text(encoding='utf-8'))
    block = next(n for n in tree.body if isinstance(n, ast.If))
    assert isinstance(block.body[0], ast.Expr)
    assert isinstance(block.body[0].value, ast.Call)
    assert getattr(block.body[0].value.func, 'id', '') == 'freeze_support'
    assert not any(isinstance(n, ast.ImportFrom) and n.module == 'doppel_agent.desktop' for n in tree.body)
