// Test-only reviewed wire fixtures. Not engine/backend/native evidence.
import type { ChildAdmission, ChildRecord, ChildSnapshot } from "../subagentReviewApi";
export const childParent = "a".repeat(32), childId = "b".repeat(32), otherParent = "c".repeat(32);
export function childRecord(generation = 1): ChildRecord {
  const total = generation - 1, offset = Math.max(0, total - 16);
  return { parent_run_id: childParent, subagent_id: childId, generation, status: "completed", prompt: "inspect", answer: "answer",
    created_at: "2026-10-05T00:00:00+00:00", updated_at: "2026-10-05T00:00:00+00:00", error_present: false,
    history_total: total, history_offset: offset, history_limit: 16, history_truncated: offset > 0,
    history: Array.from({ length: Math.min(16, total) }, (_, i) => ({ prompt: `p${offset + i}`, answer: `a${offset + i}` })) };
}
export function childSnapshot(generation = 1): ChildSnapshot {
  return { parent_run_id: childParent, items: [childRecord(generation)], total: 1, limit: 100, truncated: false,
    counts: { lifetime_for_parent: 1, durable_active_for_parent: 0 },
    capabilities: { workspace_read: true, workspace_write: false, command_execute: false, mcp_execute: false, delegate: false },
    child_mode: "graph", profile_inheritance: "parent_snapshot",
    service: { read_only: true, owner_held: true, failed_close_diagnostic: false, execution_admission: "not_checked_by_read" },
    limits: { lifetime_per_parent: 4, global_active: 2, global_queue: 16 },
    scheduler_observation: { scope: "original_scheduler_in_memory", active: 0, queued: 0 }, physical_drain_verified: false,
    output: { sensitive: true, globally_redacted: false, text_trusted: false } };
}
export function childAdmission(generation = 2, prompt = "next"): ChildAdmission {
  return { parent_run_id: childParent, reviewed_generation: generation === 1 ? null : generation - 1,
    record: { ...childRecord(generation), prompt, status: "queued", answer: "" }, admission_acknowledged: true,
    completion_verified: false, physical_drain_verified: false, output: childSnapshot().output };
}
