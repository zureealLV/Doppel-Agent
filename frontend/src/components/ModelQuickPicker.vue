<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { PublicSettings, ModelProfile } from "../workspaceTypes";
const props = defineProps<{ settings: PublicSettings | null; selected: string; disabled: boolean }>();
const emit = defineEmits<{ select: [id: string]; configure: []; add: [] }>();
const open = ref(false), query = ref("");
const trigger = ref<HTMLButtonElement | null>(null), search = ref<HTMLInputElement | null>(null);
const current = computed(() => props.settings?.profiles.find(p => p.id === props.selected));
const labels: Record<string, string> = { deepseek: "DeepSeek", openai: "OpenAI / 兼容服务", local: "本地服务", mock: "离线 Mock", custom: "自定义提供商" };
const groups = computed(() => {
  const result = new Map<string, ModelProfile[]>();
  const term = query.value.trim().toLocaleLowerCase();
  for (const profile of props.settings?.profiles || []) {
    const label = labels[profile.preset] || profile.preset || "自定义提供商";
    if (term && !`${profile.name} ${profile.model} ${label}`.toLocaleLowerCase().includes(term)) continue;
    if (!result.has(label)) result.set(label, []);
    result.get(label)!.push(profile);
  }
  return [...result].map(([label, profiles]) => ({ label, profiles }));
});
async function toggle(): Promise<void> {
  if (!props.settings) return;
  open.value = !open.value; query.value = "";
  if (open.value) { await nextTick(); search.value?.focus?.(); }
}
function close(): void { open.value = false; trigger.value?.focus?.(); }
function choose(id: string): void { if (props.disabled) return; emit('select', id); close(); }
function configure(add: boolean): void { close(); if (add) emit('add'); else emit('configure'); }
watch(() => props.disabled, () => { open.value = false; });
</script>
<template>
  <div class="model-quick-picker" @keydown.esc.stop.prevent="close">
    <button ref="trigger" type="button" class="model-quick-trigger" aria-label="快速切换模型" aria-haspopup="dialog" :aria-expanded="open" :disabled="!settings" :title="current?.model" @click="toggle">{{ current?.name || '选择模型' }}<span>⌄</span></button>
    <template v-if="open">
      <button type="button" class="model-picker-backdrop" aria-label="关闭模型菜单" tabindex="-1" @click="close" />
      <section class="model-picker-panel" role="dialog" aria-label="快速切换模型">
        <input ref="search" v-model="query" type="search" placeholder="搜索模型或提供商" aria-label="搜索模型或提供商" autocomplete="off" />
        <div class="model-picker-list">
          <section v-for="group in groups" :key="group.label"><h3>{{ group.label }}</h3><button v-for="profile in group.profiles" :key="profile.id" type="button" :aria-pressed="selected === profile.id" :disabled="disabled" @click="choose(profile.id)"><span><strong>{{ profile.name }}</strong><small>{{ profile.model }}</small></span><span aria-hidden="true">{{ selected === profile.id ? '✓' : '' }}</span></button></section>
          <p v-if="!groups.length" class="helper">没有匹配的已保存模型，可在下方添加。</p>
        </div>
        <p v-if="disabled" class="helper">当前任务使用已冻结配置；完成后可切换下次任务模型。</p>
        <footer><button type="button" @click="configure(false)">⚙ 模型与提供商设置</button><button type="button" @click="configure(true)">＋ 添加提供商 / 自定义模型</button></footer>
        <p class="model-picker-hint">仅搜索已保存档案，不自动联网或测试模型。</p>
      </section>
    </template>
  </div>
</template>
