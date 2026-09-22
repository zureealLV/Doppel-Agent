<script setup lang="ts">
import { Bot, Boxes, GitBranch, PlugZap, ShieldCheck, Sparkles, Wrench } from "lucide-vue-next";
import { computed, ref } from "vue";

import { compactPayload, eventGroup, type EventGroup } from "../runtime";
import type { RuntimeEvent } from "../types";

const props = defineProps<{ events: RuntimeEvent[]; connected: boolean }>();
const filters = ref<Set<EventGroup>>(new Set());
const groups: Array<{ id: EventGroup; label: string }> = [
  { id: "lifecycle", label: "生命周期" }, { id: "graph", label: "Graph" },
  { id: "skill", label: "Skill" }, { id: "mcp", label: "MCP" },
  { id: "subagent", label: "子代理" }, { id: "patch", label: "补丁" },
];
const visible = computed(() => filters.value.size === 0 ? props.events : props.events.filter((item) => filters.value.has(eventGroup(item.type))));

function toggle(group: EventGroup): void {
  const next = new Set(filters.value);
  next.has(group) ? next.delete(group) : next.add(group);
  filters.value = next;
}

function icon(group: EventGroup) {
  return { lifecycle: ShieldCheck, graph: GitBranch, skill: Sparkles, mcp: PlugZap, subagent: Bot, patch: Wrench, other: Boxes }[group];
}
</script>

<template>
  <section class="timeline-panel" aria-labelledby="timeline-title">
    <div class="section-heading">
      <div><p class="eyebrow">DURABLE EVENT STREAM</p><h2 id="timeline-title">实时事件</h2></div>
      <span class="connection" :class="{ online: connected }"><span aria-hidden="true" />{{ connected ? "SSE LIVE" : "POLL" }}</span>
    </div>
    <div class="filter-row" aria-label="事件筛选">
      <button v-for="group in groups" :key="group.id" type="button" :aria-pressed="filters.has(group.id)" @click="toggle(group.id)">{{ group.label }}</button>
    </div>
    <ol v-if="visible.length" class="timeline-list">
      <li v-for="item in [...visible].reverse()" :key="item.seq" :data-group="eventGroup(item.type)">
        <div class="event-icon"><component :is="icon(eventGroup(item.type))" :size="16" aria-hidden="true" /></div>
        <details>
          <summary><span class="event-type">{{ item.type }}</span><time :datetime="item.timestamp">{{ new Date(item.timestamp).toLocaleTimeString() }}</time></summary>
          <pre>{{ compactPayload(item.payload) }}</pre>
        </details>
        <span class="event-seq">#{{ item.seq }}</span>
      </li>
    </ol>
    <div v-else class="empty-state"><Boxes :size="24" aria-hidden="true" /><p>事件会在运行开始后出现在这里。</p></div>
  </section>
</template>
