<script setup lang="ts">
import { computed } from "vue";
import { safeMarkdown } from "../workspace";
import type { RunActivity } from "../replyStream";
const props = defineProps<{ items: RunActivity[]; active: boolean; answer?: string }>();
const emit = defineEmits<{ inspect: [] }>();
// Render public progress before the stored answer; never duplicate final synthesis.
const groups = computed(() => {
  const result: Array<{ id: string; kind: "text" | "tools"; text?: string; running?: boolean; tools?: RunActivity[] }> = [];
  for (const item of props.items) {
    if (item.kind === "text") {
      if (!item.text.trim() || (props.answer !== undefined && (item.phase === "final" || item.text === props.answer))) continue;
      result.push({ id: item.id, kind: "text", text: item.text, running: props.active && item.status === "running" });
    } else {
      const previous = result.at(-1);
      if (previous?.kind === "tools") previous.tools!.push(item);
      else result.push({ id: item.id, kind: "tools", tools: [item] });
    }
  }
  return result;
});
</script>
<template>
  <section class="run-activity" aria-label="阶段回复与工具执行">
    <p v-if="active && !items.some(item => item.kind === 'text' && item.text.trim())" class="activity-ack" role="status">已接收任务，正在准备检查。执行进展会保留在这里…</p>
    <template v-for="group in groups" :key="group.id">
      <article v-if="group.kind === 'text'" class="chat-message activity-progress" data-role="assistant" :data-streaming="group.running || undefined"><header>Doppel · {{ group.running ? '输出中' : '阶段进展' }}</header><div class="chat-markdown" v-html="safeMarkdown(group.text || '')" /></article>
      <details v-else class="activity-tools"><summary>{{ active && group.tools!.some(tool => tool.status === 'running') ? '正在执行' : '工具记录' }} · {{ group.tools!.length }} 项 <span>{{ group.tools!.at(-1)?.text }}</span></summary>
        <ol><li v-for="tool in group.tools" :key="tool.id"><span aria-hidden="true">{{ tool.status === 'failed' ? '!' : tool.status === 'completed' ? '✓' : '·' }}</span><code>{{ tool.text }}</code><small>{{ tool.status === 'failed' ? '执行异常' : tool.status === 'completed' ? '调用已结束' : active ? '执行中 / 等待返回' : '未记录完成' }}</small></li></ol>
        <button type="button" @click="emit('inspect')">查看该轮执行详情</button>
      </details>
    </template>
  </section>
</template>
