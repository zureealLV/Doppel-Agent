"""C2b trusted product policy definitions; execution is deferred to S9."""

import pytest

from doppel_agent.core import Core
from doppel_agent.provider import MockProvider
from doppel_agent.runtime import factory
from doppel_agent.runtime.deep import DeepAgentRuntime
from doppel_agent.runtime.legacy import LegacyRuntime
from doppel_agent.tools import patch_tool
from doppel_agent.workspace.tool_adapter import langchain_patch_tool


@pytest.mark.parametrize("policy", [None, 0, 1, "true", [], {}])
@pytest.mark.parametrize("entry", ["factory", "core", "legacy", "deep", "patch", "adapter"])
def test_invalid_policy_is_rejected_before_workspace_or_ledger_side_effects(tmp_path, policy, entry):
    root = tmp_path / "must-not-be-created"
    with pytest.raises(ValueError, match="verification_review_policy_invalid"):
        if entry == "factory":
            factory.create_runtime("deep", root, MockProvider(), require_verification_review=policy)
        elif entry == "core":
            Core(root, MockProvider(), require_verification_review=policy)
        elif entry == "legacy":
            LegacyRuntime(root, MockProvider(), reviewed=True, require_verification_review=policy)
        elif entry == "deep":
            DeepAgentRuntime(root, MockProvider(), require_verification_review=policy)
        elif entry == "patch":
            patch_tool(root, require_verification_review=policy)
        else:
            langchain_patch_tool(root, require_verification_review=policy)
    assert not root.exists()


@pytest.mark.parametrize("mode,constructor", [("deep", "DeepAgentRuntime"), ("legacy", "LegacyRuntime")])
def test_factory_keeps_explicit_owner_policy_distinct_from_default_and_options(tmp_path, monkeypatch, mode, constructor):
    calls = []

    def capture(*args, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(factory, constructor, capture)
    factory.create_runtime(mode, tmp_path, MockProvider(), core_options={"allow_write": True, "allow_command": True})
    assert calls[-1]["require_verification_review"] is False
    factory.create_runtime(mode, tmp_path, MockProvider(), reviewed_legacy=True,
        core_options={"allow_write": True, "allow_command": True, "require_verification_review": False},
        require_verification_review=True)
    assert calls[-1]["require_verification_review"] is True
