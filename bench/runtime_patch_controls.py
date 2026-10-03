"""External fault instrumentation around the unmodified native PatchService.

Never injected as an Agent tool. Global hooks are narrowed to one owned
temporary workspace, used serially, and restored even if native resume fails.
"""

from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from bench.runtime_tdd_harness import workspace_snapshot
from doppel_agent.workspace import patching


class ScopedPatchFault:
    def __init__(self, workspace: Path, control: str):
        if control not in {"second_replace_failure", "stale_base"}:
            raise ValueError("unknown patch failure control")
        self.workspace = workspace.resolve(strict=True)
        self.control = control
        self.trace = {"apply_calls": 0, "apply_errors": [], "replace_attempts": 0,
                      "failure_injected": False, "replacements": [], "rollbacks": []}
        self.stack = ExitStack()

    def __enter__(self):
        original_replace = patching.os.replace
        original_apply = patching.PatchService.apply

        def replace(source, target):
            source, target = Path(source), Path(target)
            owned = target.parent.resolve() == self.workspace and source.parent.resolve() == self.workspace
            is_patch = owned and ".doppel-patch-" in source.name
            is_rollback = owned and ".doppel-rollback-" in source.name
            if is_patch:
                self.trace["replace_attempts"] += 1
                if self.control == "second_replace_failure" and self.trace["replace_attempts"] == 2:
                    self.trace["failure_injected"] = True
                    raise OSError("owned second replacement fault")
            result = original_replace(source, target)
            if is_patch or is_rollback:
                self.trace["replacements" if is_patch else "rollbacks"].append({
                    "path": target.name, "sha256": sha256(target.read_bytes()).hexdigest(),
                })
            return result

        def apply(service, proposal):
            if service.workspace.root != self.workspace:
                return original_apply(service, proposal)
            self.trace["apply_calls"] += 1
            try:
                return original_apply(service, proposal)
            except Exception as exc:
                self.trace["apply_errors"].append(type(exc).__name__)
                raise
            finally:
                self.trace["after_apply"] = workspace_snapshot(self.workspace)

        self.stack.enter_context(patch.object(patching.os, "replace", side_effect=replace))
        self.stack.enter_context(patch.object(patching.PatchService, "apply", new=apply))
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def validate_atomic_control(fixture, evidence, *, runtime, boundary="run_service"):
    """Recompute trusted control checks; never trust a recorded pass flag."""
    control = evidence.get("control")
    trace = evidence.get("fault", {})
    base = {p: sha256(payload).hexdigest() for p, payload in fixture.public_files}
    approval = evidence.get("approval", {})
    checks = {
        "frozen_control_identity": evidence.get("case_id") == fixture.case_id
        and evidence.get("fixture_sha256") == fixture.sha256 and evidence.get("runtime") == runtime
        and evidence.get("actual_runtime") == runtime and runtime in {"graph", "deep"}
        and evidence.get("boundary") == boundary and boundary in {"direct_factory", "run_service"}
        and (evidence.get("factory_same_instance", False) is False if boundary == "run_service" else
             evidence.get("factory_same_instance") is True and evidence.get("reconstructed_service") is False),
        "reviewed_no_early_effects": evidence.get("initial") == base
        and approval.get("integrity") is True and approval.get("visible_match") is True
        and approval.get("bounds_pass") is True and approval.get("no_unapproved_effects") is True
        and (evidence.get("reconstructed_service") is True if boundary == "run_service" else evidence.get("factory_same_instance") is True)
        and type(evidence.get("approval_count")) is int and evidence["approval_count"] == 1
        and approval.get("base_sha256") == {p: "sha256:" + base[p] for p in fixture.allowed_edits},
        "native_failure_observed": type(trace.get("apply_calls")) is int and trace["apply_calls"] == 1
        and trace.get("after_apply") == evidence.get("current")
        and not evidence.get("paused_fallback_runtime") and evidence.get("status") in {"completed", "failed"},
        "no_command_grant": evidence.get("permissions") == {"workspace_write": True},
    }
    if control == "second_replace_failure":
        writes, restores = trace.get("replacements", []), trace.get("rollbacks", [])
        checks["real_partial_write_and_restore"] = (
            trace.get("replace_attempts") == 2 and trace.get("failure_injected") is True
            and trace.get("apply_errors") == ["OSError"] and len(writes) == len(restores) == 1
            and writes[0].get("path") in fixture.allowed_edits
            and writes[0].get("sha256") == approval.get("content_sha256", {}).get(writes[0].get("path"))
            and writes[0].get("sha256") != base.get(writes[0].get("path"))
            and restores[0] == {"path": writes[0].get("path"), "sha256": base.get(writes[0].get("path"))}
        )
        checks["complete_rollback_and_temp_cleanup"] = evidence.get("pre_resume") == evidence.get("current") == base
    elif control == "stale_base":
        pre_resume = evidence.get("pre_resume", {})
        expected = {**base, "settings.json": sha256(dict(fixture.public_files)["settings.json"] + b"\n").hexdigest()}
        checks["stale_review_rejected_before_any_write"] = (
            trace.get("apply_errors") == ["PatchConflictError"] and trace.get("replace_attempts") == 0
            and trace.get("failure_injected") is False and trace.get("replacements") == trace.get("rollbacks") == []
            and pre_resume == evidence.get("current") == expected
        )
    else:
        checks["known_control"] = False
    return checks
