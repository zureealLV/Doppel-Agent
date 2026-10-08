import { request } from "./api";
import type { ChangesApi, DiffSelection } from "./changesTypes";

export const PATCH_PAGE_SIZE = 25;
export function runIdentifier(value: string): boolean { return /^[0-9a-f]{32}$/.test(value); }
export function inspectionFingerprint(value: string): boolean { return /^[0-9a-f]{64}$/.test(value); }
export function toolCallIdentifier(value: string): boolean { return typeof value === "string" && value.length > 0 && value.length <= 256 && !value.includes("\0"); }
export function relativeChangesPath(value: string): boolean {
  return typeof value === "string" && value.length > 0 && value.length <= 4096 && !/[\\:\u0000-\u001f\u007f]/.test(value)
    && !value.startsWith("/") && value.split("/").every(part => part.length > 0 && part !== "." && part !== "..");
}
function scope(run: string): string {
  if (!runIdentifier(run)) throw new Error("invalid_changes_scope");
  return `/api/v1/changes/runs/${run}`;
}
export function validDiffSelection(selection: DiffSelection): boolean {
  return relativeChangesPath(selection.path) && ["staged", "worktree", "combined"].includes(selection.plane)
    && [null, 1, 2, 3].includes(selection.conflict_stage) && inspectionFingerprint(selection.expected_fingerprint);
}
export const changesApi: ChangesApi = {
  status: signal => request("/api/v1/changes/status", { cache: "no-store", signal }),
  diff: async (selection, signal) => {
    if (!validDiffSelection(selection)) throw new Error("invalid_changes_selection");
    // Explicit projection prevents hidden metadata authority/command fields crossing HTTP.
    const { path, plane, conflict_stage, expected_fingerprint } = selection;
    return request("/api/v1/changes/diff", { method: "POST", cache: "no-store", signal,
      body: JSON.stringify({ path, plane, conflict_stage, expected_fingerprint }) });
  },
  patches: async (run, offset = 0, signal) => {
    const base = scope(run);
    if (!Number.isInteger(offset) || offset < 0 || offset > 100000) throw new Error("invalid_changes_page");
    return request(`${base}/patches?limit=${PATCH_PAGE_SIZE}&offset=${offset}`, { cache: "no-store", signal });
  },
  evidence: async (run, call, signal) => {
    const base = scope(run);
    if (!toolCallIdentifier(call)) throw new Error("invalid_changes_tool_call");
    return request(`${base}/patch-evidence?tool_call_id=${encodeURIComponent(call)}`, { cache: "no-store", signal });
  },
};
