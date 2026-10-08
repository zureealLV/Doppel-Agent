import { relativeChangesPath, runIdentifier, toolCallIdentifier } from "./changesApi";
import type { InverseReview, InverseSource } from "./inverseTypes";

const keys = (value: object, expected: string[]) => Object.keys(value).sort().join("|") === expected.sort().join("|");
const hash = (value: unknown) => typeof value === "string" && /^(?:missing|sha256:[0-9a-f]{64})$/.test(value);
const fileMode = (value: unknown) => value === null || typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 0o777;
const states = ["pending", "applying", "applied", "failed", "rejected", "expired", "indeterminate"];
export function validInverseSource(value: InverseSource | null): value is InverseSource {
  return !!value && runIdentifier(value.run_id) && runIdentifier(value.patch_id) && toolCallIdentifier(value.tool_call_id);
}
export function sameInverseSource(left: InverseSource, right: InverseSource): boolean {
  return left.run_id === right.run_id && left.tool_call_id === right.tool_call_id && left.patch_id === right.patch_id;
}
export function frozenCopy<T>(value: T): T {
  const copy = JSON.parse(JSON.stringify(value)) as T;
  function freeze(item: unknown): void { if (item && typeof item === "object") { Object.values(item).forEach(freeze); Object.freeze(item); } }
  freeze(copy); return copy;
}
/** Bounded presentation framing, not a cryptographic/backend grant verification or streaming heap cap. */
export function validInverseReview(value: InverseReview, source: InverseSource, reviewId: string, patchId: string | null = null): boolean {
  if (!value || value.review_id !== reviewId || value.operation_kind !== "manual_inverse" || !validInverseSource(value.source)
    || !sameInverseSource(value.source, source) || !runIdentifier(value.patch_id) || patchId !== null && value.patch_id !== patchId
    || value.effect_tool_call_id !== "manual-inverse:" + reviewId || !states.includes(value.status)
    || ![null, "patch_operation_failed", "patch_outcome_indeterminate"].includes(value.error_code)
    || typeof value.effect_replayed !== "boolean" || typeof value.decision_replayed !== "boolean") return false;
  const created = typeof value.created_at === "string" && value.created_at.length <= 64 ? Date.parse(value.created_at) : NaN;
  const expires = typeof value.expires_at === "string" && value.expires_at.length <= 64 ? Date.parse(value.expires_at) : NaN;
  if (!Number.isFinite(created) || !Number.isFinite(expires) || expires <= created || expires - created > 86400000) return false;
  const review = value.review;
  if (!review || !keys(review, ["unified_diff", "files"]) || typeof review.unified_diff !== "string"
    || !Array.isArray(review.files) || review.files.length < 1 || review.files.length > 32
    || new TextEncoder().encode(JSON.stringify(review)).length > 4 * 1024 * 1024) return false;
  if (!review.files.every(file => !!file && keys(file, ["path", "base_hash", "base_mode", "target_hash", "target_mode", "action"])
    && relativeChangesPath(file.path) && hash(file.base_hash) && hash(file.target_hash) && fileMode(file.base_mode) && fileMode(file.target_mode)
    && (file.base_hash === "missing") === (file.base_mode === null) && (file.target_hash === "missing") === (file.target_mode === null)
    && file.action === (file.target_hash === "missing" ? "delete" : "restore"))) return false;
  if (new Set(review.files.map(file => file.path.toLowerCase())).size !== review.files.length) return false;
  const expectedEvidence = value.status === "applied" ? "sealed_patch_ledger" : ["applying", "failed", "indeterminate"].includes(value.status)
    ? "requires_patch_ledger_inspection" : "review_not_applied";
  if (value.effect_evidence !== expectedEvidence) return false;
  if (value.lifecycle && (value.lifecycle.stored_status !== value.status || !states.includes(value.lifecycle.effective_status)
    || typeof value.lifecycle.pending_expired !== "boolean" || value.lifecycle.approval_available !== false || value.lifecycle.retry_available !== false
    || value.lifecycle.effective_status !== (value.status === "pending" && value.lifecycle.pending_expired ? "expired" : value.status))) return false;
  if (value.status !== "applied") return value.result === null;
  const result = value.result;
  if (!result || !keys(result, ["patch_id", "changed_paths", "unified_diff", "patch_receipt", "receipt_source"])
    || result.patch_id !== value.patch_id || result.unified_diff !== review.unified_diff || !Array.isArray(result.changed_paths)
    || JSON.stringify(result.changed_paths) !== JSON.stringify(review.files.map(file => file.path))
    || new TextEncoder().encode(JSON.stringify(result)).length > 4 * 1024 * 1024) return false;
  const receipt = result.patch_receipt, origin = result.receipt_source;
  if (!receipt || !keys(receipt, ["schema", "patch_id", "status", "files"]) || receipt.schema !== 1 || receipt.patch_id !== value.patch_id
    || receipt.status !== "applied" || !Array.isArray(receipt.files) || receipt.files.length !== review.files.length
    || !origin || !keys(origin, ["run_id", "tool_call_id", "origin", "durability", "source_tool_call_id", "source_patch_id"])
    || origin.run_id !== source.run_id || origin.tool_call_id !== value.effect_tool_call_id || origin.origin !== "manual_inverse"
    || origin.durability !== "sealed_tool_ledger" || origin.source_tool_call_id !== source.tool_call_id || origin.source_patch_id !== source.patch_id) return false;
  return receipt.files.every((file, index) => {
    const planned = review.files[index];
    return !!file && !!planned && keys(file, ["path", "base_hash", "base_mode", "after_hash", "after_mode", "outcome"])
      && file.path === planned.path && file.base_hash === planned.base_hash && file.base_mode === planned.base_mode
      && file.after_hash === planned.target_hash && file.after_mode === planned.target_mode && file.outcome === "applied";
  });
}
