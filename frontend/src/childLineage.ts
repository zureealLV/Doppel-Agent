import type { RuntimeEvent } from "./types";

// Display recorded source only: never assign today's selection/generation to an
// old event, derive run lifecycle from nested text or create an executable link.
export function childEventLabel(event: RuntimeEvent): string | null {
  if (!event.type.startsWith("subagent.")) return null;
  const payload = event.payload;
  if (typeof payload.parent_run_id !== "string" || !/^[0-9a-f]{32}$/.test(payload.parent_run_id)
    || payload.parent_run_id !== event.run_id || typeof payload.subagent_id !== "string" || !/^[0-9a-f]{32}$/.test(payload.subagent_id)
    || typeof payload.generation !== "number" || !Number.isSafeInteger(payload.generation) || payload.generation < 1)
    return "子任务归属未固定：历史事件缺少或不匹配 parent、child、generation；不补推当前代际。";
  const kind = event.type === "subagent.runtime" && typeof payload.runtime_kind === "string"
    && /^[a-z][a-z0-9_.-]{0,127}$/.test(payload.runtime_kind) ? ` · 嵌套运行事件 ${payload.runtime_kind}` : "";
  return `记录归属 parent ${payload.parent_run_id} · child ${payload.subagent_id} · generation ${payload.generation}${kind}`;
}
