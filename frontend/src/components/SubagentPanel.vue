<script setup lang="ts">
import { Bot, CornerDownRight, Send, Square } from "lucide-vue-next";
import { ref } from "vue";

import type { SubagentRecord } from "../types";
import StatusBadge from "./StatusBadge.vue";

defineProps<{ items: SubagentRecord[]; enabled: boolean; busy: boolean }>();
const emit = defineEmits<{ spawn: [prompt: string]; followup: [id: string, prompt: string]; cancel: [id: string] }>();
const prompt = ref("");
const followups = ref<Record<string, string>>({});

function spawn(): void {
  if (!prompt.value.trim()) return;
  emit("spawn", prompt.value.trim());
  prompt.value = "";
}
</script>

<template>
  <section class="subagent-panel" aria-labelledby="subagent-title">
    <div class="section-heading"><div><p class="eyebrow">BOUNDED DELEGATION</p><h2 id="subagent-title">异步子代理</h2></div><span class="count">{{ items.length }}/4</span></div>
    <form class="inline-form" @submit.prevent="spawn">
      <label class="sr-only" for="subagent-prompt">子代理任务</label>
      <input id="subagent-prompt" v-model="prompt" :disabled="!enabled || busy" placeholder="为子代理分配一个只读任务" />
      <button class="icon-button" type="submit" :disabled="!enabled || busy || !prompt.trim()" aria-label="创建子代理"><Send :size="17" aria-hidden="true" /></button>
    </form>
    <p v-if="!enabled" class="helper">创建父运行时启用「子代理」权限后可用。</p>
    <div v-if="items.length" class="subagent-list">
      <article v-for="item in items" :key="item.subagent_id">
        <header><Bot :size="17" aria-hidden="true" /><code>{{ item.subagent_id.slice(0, 8) }}</code><StatusBadge :status="item.status" /></header>
        <p>{{ item.prompt }}</p>
        <pre v-if="item.answer">{{ item.answer }}</pre><p v-if="item.error" class="field-error">{{ item.error }}</p>
        <form class="inline-form" @submit.prevent="emit('followup', item.subagent_id, followups[item.subagent_id] || '')">
          <label class="sr-only" :for="`followup-${item.subagent_id}`">追问子代理</label>
          <input :id="`followup-${item.subagent_id}`" v-model="followups[item.subagent_id]" placeholder="继续追问" />
          <button type="submit" class="icon-button" :disabled="busy || !followups[item.subagent_id]?.trim()" aria-label="发送追问"><CornerDownRight :size="16" aria-hidden="true" /></button>
          <button v-if="['queued', 'running'].includes(item.status)" type="button" class="icon-button danger" :disabled="busy" aria-label="取消子代理" @click="emit('cancel', item.subagent_id)"><Square :size="15" aria-hidden="true" /></button>
        </form>
      </article>
    </div>
    <div v-else class="empty-inline">尚未委派子任务</div>
  </section>
</template>
