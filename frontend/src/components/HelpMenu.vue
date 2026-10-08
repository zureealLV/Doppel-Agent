<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { version } from '../../package.json';

const props = defineProps<{ blocked: boolean }>();
const root = ref<HTMLElement | null>(null), trigger = ref<HTMLButtonElement | null>(null);
const menu = ref<HTMLElement | null>(null), dialog = ref<HTMLDialogElement | null>(null);
const open = ref(false), sheet = ref<'shortcuts' | 'about'>('shortcuts');
const github = 'https://github.com/zureealLV/Doppel-Agent';
const entries = [
  { label: '使用文档', url: `${github}/blob/main/docs/USER_GUIDE_CN.md` },
  { label: '快捷键', sheet: 'shortcuts' as const },
  { label: '故障排除', url: `${github}/blob/main/docs/TROUBLESHOOTING_CN.md` },
  { label: '问题反馈', url: `${github}/issues` },
  { label: '关于 Doppel-Agent', sheet: 'about' as const },
];
function items() { return Array.from(menu.value?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []); }
function close(restore = false) { open.value = false; if (restore && !props.blocked) trigger.value?.focus(); }
async function show(last = false) {
  if (props.blocked) return;
  open.value = true; await nextTick();
  if (open.value && !props.blocked) (last ? items().at(-1) : items()[0])?.focus();
}
function toggle() { if (open.value) close(true); else void show(); }
function triggerKey(event: KeyboardEvent) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); void show(event.key === 'ArrowUp'); }
}
function menuKey(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
  if (event.key === 'Tab') { close(); return; }
  const list = items(), index = list.indexOf(event.target as HTMLElement);
  let next: number;
  if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = list.length - 1;
  else if (event.key === 'ArrowDown') next = (index + 1) % list.length;
  else if (event.key === 'ArrowUp') next = (index - 1 + list.length) % list.length;
  else return; // Enter/Space retain native button/link activation.
  event.preventDefault(); list[next]?.focus();
}
function outside(event: Event) { if (open.value && !root.value?.contains(event.target as Node)) close(); }
function showSheet(value: 'shortcuts' | 'about') {
  close(); if (props.blocked) return;
  sheet.value = value; dialog.value?.showModal();
}
function closeSheet() { if (dialog.value?.open) dialog.value.close(); }
watch(() => props.blocked, blocked => { if (blocked) { close(); closeSheet(); } });
onMounted(() => { document.addEventListener('pointerdown', outside); document.addEventListener('focusin', outside); });
onBeforeUnmount(() => { document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside); });
</script>
<template>
  <div ref="root" class="help-menu no-drag">
    <button ref="trigger" type="button" class="help-trigger" title="帮助" aria-label="帮助" aria-haspopup="menu" aria-controls="workbench-help-menu" :aria-expanded="open" :disabled="blocked" @click="toggle" @keydown="triggerKey">帮助</button>
    <div v-if="open" id="workbench-help-menu" ref="menu" class="help-dropdown" role="menu" aria-label="帮助菜单" @keydown="menuKey">
      <template v-for="entry in entries" :key="entry.label">
        <a v-if="entry.url" role="menuitem" tabindex="-1" :href="entry.url" target="_blank" rel="noopener noreferrer" @click="close(true)">{{ entry.label }}</a>
        <button v-else type="button" role="menuitem" tabindex="-1" @click="showSheet(entry.sheet!)">{{ entry.label }}</button>
      </template>
    </div>
    <dialog ref="dialog" class="workspace-dialog help-dialog" aria-labelledby="help-sheet-title" @close="close(true)">
      <section class="workspace-dialog-content">
        <header class="section-heading"><h2 id="help-sheet-title">{{ sheet === 'shortcuts' ? '快捷键' : '关于 Doppel-Agent' }}</h2><button type="button" aria-label="关闭帮助说明" @click="closeSheet">关闭</button></header>
        <dl v-if="sheet === 'shortcuts'" class="help-shortcuts">
          <dt><kbd>Ctrl</kbd> / <kbd>⌘</kbd> + <kbd>K</kbd></dt><dd>搜索对话（含归档）；Legacy 页面搜索旧版历史，其余页面打开原生搜索。</dd>
          <dt><kbd>Enter</kbd> / <kbd>Shift</kbd> + <kbd>Enter</kbd></dt><dd>对话输入框发送 / 换行；输入法组字时不发送。</dd>
          <dt><kbd>↑</kbd> <kbd>↓</kbd> / <kbd>Enter</kbd></dt><dd>搜索结果选择 / 打开。帮助菜单也可用 ↑↓、Home、End。</dd>
          <dt><kbd>Esc</kbd></dt><dd>关闭帮助菜单或可关闭的弹窗；不能代替运行取消、审批或清理。</dd>
        </dl>
        <template v-else>
          <p><strong>Doppel-Agent {{ version }}</strong></p>
          <p>本地编码工作台 · 预发布版。界面验收不代表完整原生矩阵或模型质量评测通过。</p>
          <a :href="github" target="_blank" rel="noopener noreferrer">GitHub 项目与发布记录</a>
        </template>
      </section>
    </dialog>
  </div>
</template>
