import { request } from "./api";
import { runIdentifier, toolCallIdentifier } from "./changesApi";
import type { InverseApi, InverseReview } from "./inverseTypes";
function route(run: string, review?: string): string {
  if (!runIdentifier(run) || review !== undefined && !runIdentifier(review)) throw new Error("invalid_inverse_scope");
  return `/api/v1/changes/runs/${run}/inverse-reviews${review === undefined ? "" : "/" + review}`;
}
function confirmation(value: boolean): void { if (value !== true) throw new Error("inverse_confirmation_required"); }
export const inverseApi: InverseApi = {
  prepare: async (run, body) => {
    const path = route(run); confirmation(body.confirmed);
    if (!toolCallIdentifier(body.source_tool_call_id) || !runIdentifier(body.source_patch_id) || !runIdentifier(body.operation_id)
      || typeof body.workspace_write !== "boolean") throw new Error("invalid_inverse_request");
    const { confirmed, source_tool_call_id, source_patch_id, operation_id, workspace_write } = body;
    return request<InverseReview>(path, { method: "POST", cache: "no-store", body: JSON.stringify({ confirmed, source_tool_call_id, source_patch_id, operation_id, workspace_write }) });
  },
  read: async (run, review, signal) => request<InverseReview>(route(run, review), { cache: "no-store", signal }),
  decide: async (run, review, body) => {
    const path = route(run, review); confirmation(body.confirmed);
    if (!runIdentifier(body.patch_id) || !["approve", "reject"].includes(body.action) || typeof body.workspace_write !== "boolean") throw new Error("invalid_inverse_decision");
    const { confirmed, patch_id, action, workspace_write } = body;
    return request<InverseReview>(`${path}/decision`, { method: "POST", cache: "no-store", body: JSON.stringify({ confirmed, patch_id, action, workspace_write }) });
  },
  reconcile: async (run, review, body) => {
    const path = route(run, review); confirmation(body.confirmed);
    return request<InverseReview>(`${path}/reconcile`, { method: "POST", cache: "no-store", body: JSON.stringify({ confirmed: true }) });
  },
};
