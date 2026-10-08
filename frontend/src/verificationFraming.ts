import { inspectionFingerprint, relativeChangesPath, runIdentifier, toolCallIdentifier } from "./changesApi";
import { validVerificationNames, VERIFICATION_PAGE_SIZE } from "./verificationApi";
import { frozenCopy } from "./inverseFraming";
import type { VerificationLifecycle, VerificationPage, VerificationPlan, VerificationReview, VerificationSource, VerificationSummary } from "./verificationTypes";
const keys = (value: object, expected: string[]) => Object.keys(value).sort().join("|") === expected.sort().join("|");
const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;
const integer = (value: unknown, min: number, max: number) => typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
const states = ["pending", "running", "completed", "failed", "cancelled", "rejected", "expired", "indeterminate"];
const errors = [null, "verification_review_stale", "verification_cancelled", "verification_deadline_exceeded", "verification_outcome_indeterminate", "verification_evidence_budget"];
export function validVerificationSource(value: VerificationSource | null): value is VerificationSource {
  return !!value && typeof value.run_id === "string" && runIdentifier(value.run_id) && typeof value.patch_id === "string"
    && runIdentifier(value.patch_id) && toolCallIdentifier(value.tool_call_id);
}
export function sameVerificationSource(left: VerificationSource, right: VerificationSource): boolean {
  return left.run_id === right.run_id && left.tool_call_id === right.tool_call_id && left.patch_id === right.patch_id;
}
export function verificationUnknown(value: VerificationSummary | null): boolean {
  return !!value && (["running", "indeterminate"].includes(value.status) || value.has_unknown_command);
}
function lifecycleValid(value: VerificationLifecycle, summary: VerificationSummary): boolean {
  return !!value && value.stored_status === summary.status && typeof value.pending_expired === "boolean"
    && (summary.status === "pending" || value.pending_expired === false)
    && value.effective_status === (summary.status === "pending" && value.pending_expired ? "expired" : summary.status)
    && value.pending_unexpired === (summary.status === "pending" && !value.pending_expired)
    && value.approval_available === false && value.retry_available === false && integer(value.planned_commands, 1, 16)
    && integer(value.sealed_steps, 0, value.planned_commands) && value.remaining_commands === value.planned_commands - value.sealed_steps
    && value.outcome_unknown === verificationUnknown(summary)
    && value.all_planned_attempts_sealed === (value.sealed_steps === value.planned_commands && !summary.has_unknown_command)
    && typeof value.commands_completed === "boolean" && (!value.commands_completed || value.sealed_steps === value.planned_commands)
    && (!summary.has_unknown_command || value.sealed_steps < value.planned_commands)
    && (!["pending", "rejected", "expired"].includes(summary.status) || value.sealed_steps === 0)
    && (summary.status !== "completed" || value.sealed_steps > 0 && (summary.success !== true || value.all_planned_attempts_sealed));
}
function validSummary(value: VerificationSummary, run: string): boolean {
  if (!value || !validVerificationSource(value.source) || value.source.run_id !== run || typeof value.review_id !== "string"
    || !runIdentifier(value.review_id) || value.operation_id !== "manual-verification:" + value.review_id
    || value.operation_kind !== "manual_verification" || value.target !== "current_workspace_not_original_patch_snapshot"
    || !states.includes(value.status) || !errors.includes(value.error_code) || typeof value.has_unknown_command !== "boolean"
    || (value.status === "completed" ? typeof value.success !== "boolean" || value.has_unknown_command : value.success !== null)
    || ["pending", "rejected", "expired"].includes(value.status) && value.has_unknown_command
    || value.evidence !== (value.status === "completed" ? "sealed_verification_steps" : "not_completion_proof")) return false;
  const created = typeof value.created_at === "string" && value.created_at.length <= 64 ? Date.parse(value.created_at) : NaN;
  const expires = typeof value.expires_at === "string" && value.expires_at.length <= 64 ? Date.parse(value.expires_at) : NaN;
  if (!Number.isFinite(created) || !Number.isFinite(expires) || expires <= created || expires - created > 86400000) return false;
  if (value.lifecycle && !lifecycleValid(value.lifecycle, value)) return false;
  const provenance = value.provenance;
  if (provenance && (provenance.source_registration !== "registered_run" || provenance.patch_success !== "not_inferred_from_verification"
    || provenance.plan !== "stored_exact_review_not_current_config_validation" || provenance.output !== "stored_sealed_attempts_not_whole_project_acceptance"
    || provenance.cross_database_snapshot !== "not_atomic" || !provenance.source_run || typeof provenance.source_run.lease_active !== "boolean"
    || !["queued", "running", "interrupted", "completed", "failed", "cancelled", "interrupted_expired"].includes(provenance.source_run.status))) return false;
  return true;
}
export function validVerificationPlan(plan: VerificationPlan): boolean {
  if (!plan || !keys(plan, ["schema", "plan_id", "source", "commands", "max_output_bytes", "stop_on_failure", "operation_timeout_seconds"])
    || plan.schema !== 1 || typeof plan.plan_id !== "string" || !inspectionFingerprint(plan.plan_id) || !plan.source
    || !keys(plan.source, ["path", "config_hash", "snapshot_hash", "workspace_id"]) || !relativeChangesPath(plan.source.path)
    || ![plan.source.config_hash, plan.source.snapshot_hash, plan.source.workspace_id].every(hash => typeof hash === "string" && inspectionFingerprint(hash))
    || !Array.isArray(plan.commands) || plan.commands.length < 1 || plan.commands.length > 16
    || !integer(plan.max_output_bytes, 1, 1048576) || typeof plan.stop_on_failure !== "boolean" || plan.operation_timeout_seconds !== 600
    || bytes(plan) > 128 * 1024) return false;
  return validVerificationNames(plan.commands.map(command => command?.name)) && plan.commands.every(command => !!command
    && keys(command, ["name", "argv", "timeout_seconds"]) && Array.isArray(command.argv) && command.argv.length > 0 && command.argv.length <= 64
    && command.argv.every(arg => typeof arg === "string" && arg.length > 0 && !arg.includes("\0") && new TextEncoder().encode(arg).length <= 4096)
    && typeof command.timeout_seconds === "number" && Number.isFinite(command.timeout_seconds) && command.timeout_seconds > 0 && command.timeout_seconds <= 600);
  // Python hashes canonical JSON including 600.0; JS parsed numbers cannot reproduce that lexical value.
  // Frontend pins exact opaque plan_id/whole immutable plan. Backend checks its actual canonical hash.
}
/** Bounded known presentation framing only, not cryptographic validation or a streaming/network/OS cap. */
export function validVerificationReview(value: VerificationReview, run: string, id: string, source: VerificationSource | null = null, planId: string | null = null): boolean {
  if (!validSummary(value, run) || value.review_id !== id || source && !sameVerificationSource(value.source, source)
    || !validVerificationPlan(value.plan) || planId !== null && value.plan.plan_id !== planId || typeof value.decision_replayed !== "boolean"
    || !Array.isArray(value.steps) || value.steps.length > value.plan.commands.length) return false;
  let consumed = 0;
  for (const [index, step] of value.steps.entries()) {
    if (!step || !keys(step, ["index", "status", "result"]) || step.index !== index || !["running", "finished"].includes(step.status)
      || (step.status === "finished") !== (step.result !== null) || step.result !== null && (!step.result || typeof step.result !== "object" || Array.isArray(step.result))
      || index < value.steps.length - 1 && step.status !== "finished"
      || index > 0 && value.plan.stop_on_failure && value.steps[index - 1]!.result?.success === false) return false;
    const result = step.result, command = value.plan.commands[index]!;
    if (result) {
      consumed += bytes(result);
      if (!keys(result, ["name", "argv", "exit_code", "success", "stdout", "stderr", "duration_ms", "supervision", "error"])
        || result.name !== command.name || JSON.stringify(result.argv) !== JSON.stringify(command.argv)
        || result.exit_code !== null && !Number.isSafeInteger(result.exit_code) || result.success !== (result.exit_code === 0)
        || typeof result.stdout !== "string" || typeof result.stderr !== "string" || !integer(result.duration_ms, 0, 86400000)
        || typeof result.supervision !== "string" || result.supervision.length < 1 || result.supervision.length > 64
        || ![null, "verification_timeout", "verification_output_limit", "verification_supervision_unavailable"].includes(result.error)
        || (result.exit_code === null) !== (result.error !== null) || consumed > 4 * 1024 * 1024
        || result.error !== null && (result.stdout !== "" || result.stderr !== "" || result.supervision !== (result.error === "verification_supervision_unavailable" ? "unavailable" : "terminated"))) return false;
    }
  }
  const unknown = value.steps.some(step => step.status === "running"), sealed = value.steps.filter(step => step.result !== null).length;
  if (unknown !== value.has_unknown_command || ["pending", "rejected", "expired"].includes(value.status) && value.steps.length > 0) return false;
  if (value.status === "completed" && (unknown || !value.steps.length || value.steps.length !== value.plan.commands.length
    && !(value.plan.stop_on_failure && value.steps.at(-1)?.result?.success === false)
    || value.success !== value.steps.every(step => step.result?.success === true))) return false;
  if (value.lifecycle && (value.lifecycle.planned_commands !== value.plan.commands.length || value.lifecycle.sealed_steps !== sealed
    || value.lifecycle.commands_completed !== (sealed === value.plan.commands.length && value.steps.every(step => step.result?.exit_code !== null)))) return false;
  return true;
}
export function validVerificationPage(value: Readonly<VerificationPage>, run: string, after: string): boolean {
  if (!value || !Array.isArray(value.items) || value.items.length > VERIFICATION_PAGE_SIZE || typeof value.has_more !== "boolean"
    || typeof value.budget_limited !== "boolean" || !integer(value.decode_bytes, 0, 8 * 1024 * 1024) || value.sql_read_only !== true
    || value.filesystem_zero_write_guarantee !== false || value.order !== "review_id_keyset_not_chronological"
    || value.budget_limited && !value.has_more || value.items.length > 0 && value.decode_bytes === 0) return false;
  let previous = after;
  for (const item of value.items) {
    if (!validSummary(item, run) || !item.lifecycle || !item.provenance || item.review_id <= previous
      || !keys(item, ["review_id", "operation_id", "operation_kind", "source", "target", "created_at", "expires_at", "status", "error_code",
        "success", "has_unknown_command", "evidence", "lifecycle", "provenance"])) return false;
    previous = item.review_id;
  }
  return value.next_after_id === (value.has_more && value.items.length ? value.items.at(-1)!.review_id : null);
}
export function verificationPresentation(value: VerificationReview): VerificationReview {
  const { review_id, operation_id, operation_kind, source, target, created_at, expires_at, status, error_code,
    success, has_unknown_command, evidence, plan, steps, decision_replayed, lifecycle, provenance } = value;
  return frozenCopy({ review_id, operation_id, operation_kind, source: { run_id: source.run_id, tool_call_id: source.tool_call_id, patch_id: source.patch_id },
    target, created_at, expires_at, status, error_code, success, has_unknown_command, evidence, plan, steps, decision_replayed,
    ...(lifecycle ? { lifecycle: { stored_status: lifecycle.stored_status, effective_status: lifecycle.effective_status,
      pending_expired: lifecycle.pending_expired, pending_unexpired: lifecycle.pending_unexpired, approval_available: false as const, retry_available: false as const,
      planned_commands: lifecycle.planned_commands, sealed_steps: lifecycle.sealed_steps, remaining_commands: lifecycle.remaining_commands,
      outcome_unknown: lifecycle.outcome_unknown, all_planned_attempts_sealed: lifecycle.all_planned_attempts_sealed, commands_completed: lifecycle.commands_completed } } : {}),
    ...(provenance ? { provenance: { source_registration: provenance.source_registration,
      source_run: { status: provenance.source_run.status, lease_active: provenance.source_run.lease_active }, patch_success: provenance.patch_success,
      plan: provenance.plan, output: provenance.output, cross_database_snapshot: provenance.cross_database_snapshot } } : {}) });
}
