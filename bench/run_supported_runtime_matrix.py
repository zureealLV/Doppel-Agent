"""Run every eligible native scripted key, separately for each frozen boundary.

This command never calls an external model and never unlocks live full mode.
Each repeat gets a fresh workspace/runtime/server/process tree. Saved attempts,
including infrastructure failures, are immutable and are not retried on resume.
"""

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from bench.live_runtime_matrix import ResultStore, _write_json_atomic
from bench.runtime_audit_inputs import AuditInputs, adjudication_forms, normalize_observation, source_context
from bench.runtime_approval_harness import probe_approval
from bench.runtime_cancel_harness import probe_cancel
from bench.runtime_contract import NativeTaskContract
from bench.runtime_factory_patch_harness import probe_factory_patch
from bench.runtime_freeze import canonical_json
from bench.runtime_mcp_harness import probe_mcp
from bench.runtime_patch_harness import probe_patch
from bench.runtime_readonly_harness import load_matrix_fixtures, probe_readonly
from bench.runtime_tdd_harness import probe_tdd


ROOT = Path(__file__).resolve().parents[1]


def supported_selection(contract, boundary):
    if boundary not in {"direct_factory", "run_service"}:
        raise ValueError("a single explicit production boundary is required")
    keys = contract.capabilities[boundary]["supported_run_keys"]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("eligible native keys must be nonempty and unique")
    return tuple((boundary, key) for key in keys)


async def native_probe(fixture, run, workspace, contract, boundary):
    if fixture.category in {"navigation", "known_answer_review"}:
        return await probe_readonly(fixture, run.runtime, workspace, boundary=boundary, _contract=contract)
    if boundary == "direct_factory":
        if fixture.category != "multi_file_patch":
            raise ValueError("eligible factory key has no native fixture dispatcher")
        return await probe_factory_patch(fixture, run.runtime, workspace, _contract=contract)
    if fixture.category == "multi_file_patch":
        return await probe_patch(fixture, run.runtime, workspace, _contract=contract)
    if fixture.category == "approval_resume":
        return await probe_approval(fixture, run.runtime, workspace, _contract=contract)
    if fixture.category == "mcp_workflow":
        return await probe_mcp(fixture, run.runtime, workspace, _contract=contract)
    if fixture.category == "concurrent_cancel":
        return await probe_cancel(fixture, run.runtime, workspace, _contract=contract)
    if fixture.category == "tdd_fix" and run.runtime == "graph":
        return await probe_tdd(fixture, workspace, _contract=contract)
    raise ValueError("eligible service key has no native fixture dispatcher")


def infrastructure_row(fixture, run, boundary, failure_class):
    return {"run_key": run.run_key, "case_id": fixture.case_id, "runtime": run.runtime, "actual_runtime": None,
            "boundary": boundary, "fixture_version": fixture.version, "fixture_sha256": fixture.sha256,
            "permissions": dict(run.permissions), "status": "infrastructure_failed", "failure_class": failure_class,
            "infrastructure_failure": True, "checks": {"native_probe_returned_valid_evidence": False},
            "deterministic_pass": False, "human_review": "pending", "task_quality_scored": False}


def _stored_records(inputs, store, selection):
    records = []
    for _, key in selection:
        record = store.load(key)
        if record is None:
            continue
        if record.get("inputs_sha256") != inputs.sha256 or record.get("observation", {}).get("run_key") != key:
            raise ValueError("saved scripted attempt belongs to a different sealed context/key")
        normalized = normalize_observation(inputs, record["observation"])
        if normalized != record.get("normalized"):
            raise ValueError("saved scripted evidence changed; choose a new output directory")
        records.append(record)
    return records


def summarize_supported(inputs, records, boundary, *, source_unchanged, native_dispatcher_used):
    context = inputs.document()
    selected = [row["run_key"] for row in context["selection"]]
    keys = [row["run_key"] for row in records]
    if len(keys) != len(set(keys)) or not set(keys) <= set(selected):
        raise ValueError("duplicate or out-of-denominator scripted result")
    complete = set(keys) == set(selected)
    normalized = [row["normalized"] for row in records]
    capability = context["capabilities"][boundary]
    passed = complete and source_unchanged and native_dispatcher_used and all(row["deterministic_pass"] for row in normalized)
    return {"schema_version": "1.0", "recorded_at": datetime.now(UTC).isoformat(), "boundary": boundary,
            "provider": "local-scripted-controls-no-external-model", "native_dispatcher_used": native_dispatcher_used,
            "source_commit": context["source"]["commit"], "evaluation_source_dirty": context["source"]["evaluation_source_dirty"],
            "inputs_sha256": inputs.sha256, "source_unchanged_during_audit": source_unchanged,
            "original_run_count": capability["original_run_count"], "supported_run_count": capability["supported_run_count"],
            "excluded_run_count": capability["excluded_run_count"], "excluded_runs": capability["excluded_runs"],
            "selected_run_count": len(selected), "recorded_run_count": len(records), "complete_denominator": complete,
            "missing_run_keys": [key for key in selected if key not in set(keys)],
            "infrastructure_failure_count": sum(row["infrastructure_failure"] for row in normalized),
            "deterministic_pass_count": sum(row["deterministic_pass"] for row in normalized),
            "statuses": dict(Counter(row["status"] for row in normalized)),
            "failure_classes": dict(Counter(row["failure_class"] for row in normalized if row["failure_class"])),
            "normalized_evidence": normalized, "runs": [row["observation"] for row in records],
            "adjudication_forms": adjudication_forms(inputs, normalized), "passed": passed,
            "scripted_supported_set_ready": passed, "full_matrix_ready": False, "task_quality_scored": False,
            "scope_note": "One frozen supported boundary with three independent repeats; scripted/native correctness evidence only. Original 180 keys, capability exclusions, paid evidence and human adjudication remain distinct."}


def _publish_review_packets(inputs, fixtures, root):
    # An external human-only copy. Never copied into any Agent workspace or
    # passed to its provider/tool surface, including the scripted controls.
    for fixture in fixtures:
        if fixture.category != "known_answer_review":
            continue
        key = dict(fixture.hidden_files)["answer_key.json"]
        packet = {"inputs_sha256": inputs.sha256, "fixture_sha256": fixture.sha256,
                  "public_source_sha256": sha256(dict(fixture.public_files)["service.py"]).hexdigest(),
                  "answer_key_sha256": sha256(key).hexdigest(), "answer_key": json.loads(key)}
        path = root / "reviewer_keys" / f"{fixture.case_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if canonical_json(json.loads(path.read_bytes())) != canonical_json(packet):
                raise ValueError("external reviewer packet changed from the frozen key")
        else:
            from bench.live_runtime_matrix import _write_json_exclusive
            _write_json_exclusive(path, packet)


async def run_supported_matrix(root, boundary, output, *, _probe=None):
    root = Path(root).resolve(strict=True)
    contract = NativeTaskContract.load(root=root)
    selection = supported_selection(contract, boundary)
    fixtures = load_matrix_fixtures(root, contract)
    inputs = AuditInputs.capture(contract, fixtures, selection, source_context(root), scope="supported_matrix")
    native = _probe is None
    store = ResultStore(output, {"frozen_inputs": inputs.document(), "execution_driver": "native_production" if native else "test_injected_orchestration"})
    _publish_review_packets(inputs, fixtures, store.root)
    runs = {run.run_key: run for run in contract.matrix.expand()}
    indexed = {fixture.case_id: fixture for fixture in fixtures}
    probe = native_probe if native else _probe
    report = None
    try:
        # Validate all saved attempts before performing any new native work.
        _stored_records(inputs, store, selection)
        with tempfile.TemporaryDirectory(prefix="doppel-supported-native-matrix-") as temp:
            for _, key in selection:
                if store.load(key) is not None:
                    continue
                run, fixture = runs[key], indexed[runs[key].case_id]
                workspace = Path(temp) / run.case_id / run.runtime / str(run.repeat)
                try:
                    observation = {**await probe(fixture, run, workspace, contract, boundary), "run_key": key}
                    normalized = normalize_observation(inputs, observation)
                except Exception as exc:  # noqa: BLE001 - preserve an attempt without raw exception/transport text
                    failure = "runtime_timeout" if isinstance(exc, TimeoutError) else "evidence_contract_error" if isinstance(exc, ValueError) else "runtime_error"
                    observation = infrastructure_row(fixture, run, boundary, failure)
                    normalized = normalize_observation(inputs, observation)
                store.save(key, {"run_key": key, "inputs_sha256": inputs.sha256, "observation": observation, "normalized": normalized})
    finally:
        records = _stored_records(inputs, store, selection)
        unchanged = True
        try:
            inputs.assert_source_unchanged(source_context(root))
        except ValueError:
            unchanged = False
        report = summarize_supported(inputs, records, boundary, source_unchanged=unchanged, native_dispatcher_used=native)
        _write_json_atomic(store.root / "summary.json", report)
        _write_json_atomic(store.root / "adjudication_forms.json", report["adjudication_forms"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary", choices=("direct_factory", "run_service"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run_supported_matrix(ROOT, args.boundary, args.output_dir))
    print(json.dumps({key: report[key] for key in ("boundary", "original_run_count", "supported_run_count", "recorded_run_count",
                                                  "complete_denominator", "passed", "full_matrix_ready", "task_quality_scored")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
