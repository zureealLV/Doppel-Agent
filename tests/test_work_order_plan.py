"""No runtime/provider execution: work-order DAG domain regression definitions."""

import pytest

from doppel_agent.tasks.work_orders import plan_from_payload, topological_order


def task(identifier, dependencies=(), **extra):
    return {"id": identifier, "title": identifier, "prompt": "Read the fixture source.",
            "dependencies": list(dependencies), **extra}


def test_dag_has_deterministic_dependency_order_and_conservative_defaults():
    plan = plan_from_payload({"title": "fixture", "tasks": [task("verify", ["change"]),
                             task("read"), task("change", ["read"], access="write")]})
    assert topological_order(plan) == ("read", "change", "verify")
    assert plan.tasks[0].mode == "graph"
    assert plan.tasks[0].access == "read"
    assert plan.tasks[0].profile_id is None


@pytest.mark.parametrize("tasks", [
    [], [task("a"), task("a")], [task("a", ["missing"])], [task("a", ["a"])],
    [task("a", ["b"]), task("b", ["a"])], [task("a", ["b", "b"]), task("b")],
    [task("../escape")], [task("a", access="admin")], [task("a", mode="unknown")],
    [task("a", api_key="NOT-A-REAL-KEY")], [{**task("a"), "dependencies": "b"}],
    [task("a", prompt=" ")], [task("a", prompt="x" * 100001)],
])
def test_invalid_or_unbounded_plan_is_rejected(tasks):
    with pytest.raises(ValueError):
        plan_from_payload({"title": "fixture", "tasks": tasks})


def test_plan_rejects_excess_tasks_total_prompt_bytes_and_unknown_fields():
    for payload in (
        {"title": "fixture", "tasks": [task(f"t{i}") for i in range(65)]},
        {"title": "fixture", "tasks": [task(f"t{i}", prompt="界" * 100000) for i in range(2)]},
        {"title": "fixture", "tasks": [task("a")], "permissions": {"workspace_write": True}},
    ):
        with pytest.raises(ValueError):
            plan_from_payload(payload)


def test_http_schemas_keep_permissions_and_keys_out_of_draft_plans():
    from pydantic import ValidationError
    from doppel_agent.api.schemas import WorkOrderCreate, WorkOrderPlanReplace

    payload = {"plan": {"title": "fixture", "tasks": [task("a")]}}
    assert WorkOrderCreate.model_validate(payload).plan.to_plan().tasks[0].access == "read"
    with pytest.raises(ValidationError):
        WorkOrderCreate.model_validate({**payload, "permissions": {"workspace_write": True}})
    with pytest.raises(ValidationError):
        WorkOrderPlanReplace.model_validate({"plan": payload["plan"], "expected_revision": True})
