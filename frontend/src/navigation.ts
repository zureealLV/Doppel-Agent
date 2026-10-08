export type WorkspacePage = "conversations" | "orders" | "changes" | "extensions" | "runtime" | "legacy";
export function pageFromHash(hash: string): WorkspacePage {
  if (hash === "#changes") return "changes";
  if (hash === "#extensions") return "extensions";
  return hash === "#orders" ? "orders" : hash === "#runtime" ? "runtime" : hash === "#legacy" ? "legacy" : "conversations";
}
export function pageHash(page: WorkspacePage): string { return `#${page}`; }
export function pageAfterHashChange(hash: string, current: WorkspacePage): WorkspacePage {
  return ["#conversations", "#orders", "#changes", "#extensions", "#runtime", "#legacy"].includes(hash) ? pageFromHash(hash) : current;
}
