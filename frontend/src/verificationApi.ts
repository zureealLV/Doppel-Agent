import { request } from "./api";
import { inspectionFingerprint, runIdentifier, toolCallIdentifier } from "./changesApi";
import type { VerificationApi, VerificationPage, VerificationReview } from "./verificationTypes";
export const VERIFICATION_PAGE_SIZE = 16;
export function validVerificationNames(value: string[] | null): boolean {
  return value === null || Array.isArray(value) && value.length > 0 && value.length <= 16 && new Set(value).size === value.length
    && value.every(name => typeof name === "string" && name.length > 0 && !name.includes("\0") && new TextEncoder().encode(name).length <= 128);
}
function route(run: string, review?: string): string {
  if (!runIdentifier(run) || review !== undefined && !runIdentifier(review)) throw new Error("invalid_verification_scope");
  return `/api/v1/changes/runs/${run}/verification-reviews${review === undefined ? "" : "/" + review}`;
}
function confirmed(value: boolean): void { if (value !== true) throw new Error("verification_confirmation_required"); }
export const verificationApi: VerificationApi = {
  prepare: async (run, body) => {
    const path = route(run); confirmed(body.confirmed);
    if (!toolCallIdentifier(body.source_tool_call_id) || !runIdentifier(body.source_patch_id) || !runIdentifier(body.operation_id)
      || !validVerificationNames(body.names) || typeof body.command_execute !== "boolean" || typeof body.workspace_write !== "boolean") throw new Error("invalid_verification_prepare");
    const { source_tool_call_id, source_patch_id, operation_id, names, command_execute, workspace_write } = body;
    return request<VerificationReview>(path, { method: "POST", cache: "no-store", body: JSON.stringify({ confirmed: true,
      source_tool_call_id, source_patch_id, operation_id, names, command_execute, workspace_write }) });
  },
  list: async (run, after, signal) => {
    const path = route(run);
    if (after !== "" && !runIdentifier(after)) throw new Error("invalid_verification_cursor");
    return request<VerificationPage>(`${path}?limit=${VERIFICATION_PAGE_SIZE}&after_id=${after}`, { cache: "no-store", signal });
  },
  read: async (run, review, signal) => request<VerificationReview>(route(run, review), { cache: "no-store", signal }),
  decide: async (run, review, body) => {
    const path = route(run, review); confirmed(body.confirmed);
    if (!inspectionFingerprint(body.plan_id) || !["approve", "reject"].includes(body.action)
      || typeof body.command_execute !== "boolean" || typeof body.workspace_write !== "boolean") throw new Error("invalid_verification_decision");
    const { plan_id, action, command_execute, workspace_write } = body;
    return request<VerificationReview>(`${path}/decision`, { method: "POST", cache: "no-store",
      body: JSON.stringify({ confirmed: true, plan_id, action, command_execute, workspace_write }) });
  },
  cancel: async (run, review, body) => {
    const path = route(run, review); confirmed(body.confirmed);
    if (!inspectionFingerprint(body.plan_id)) throw new Error("invalid_verification_cancel");
    return request<VerificationReview>(`${path}/cancel`, { method: "POST", cache: "no-store", body: JSON.stringify({ confirmed: true, plan_id: body.plan_id }) });
  },
  reconcile: async (run, review, body) => {
    const path = route(run, review); confirmed(body.confirmed);
    return request<VerificationReview>(`${path}/reconcile`, { method: "POST", cache: "no-store", body: JSON.stringify({ confirmed: true }) });
  },
};
