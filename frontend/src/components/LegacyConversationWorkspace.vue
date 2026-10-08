<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { FolderPlus, MessageSquarePlus, Search, Settings2, ScanLine, MoreHorizontal, PanelRight, Plus, ArrowUp, Info, Archive } from "lucide-vue-next";

import ModelSettings from "./ModelSettings.vue";
import CodingTemplates from "./CodingTemplates.vue";
import { workspaceApi } from "../workspaceApi";
import { browserStorage, safeMarkdown, searchIndex, WorkspaceController } from "../workspace";
import type { Effort, Permissions } from "../types";
import type { ConversationSummary, PublicSettings } from "../workspaceTypes";

const props = defineProps<{ active: boolean }>();
const emit = defineEmits<{ settings: [settings: PublicSettings] }>();
const controller = new WorkspaceController(browserStorage());
const state = controller.state;
const prompt = ref("");
const effort = ref<Effort>("balanced");
const grants = reactive<Permissions>({ workspace_write: false, command_execute: false, mcp_execute: false, delegate: false });
const inspector = ref(false);
const settingsOpen = ref(false);
const searchDialog = ref<HTMLDialogElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const query = ref("");
const selectedResult = ref(0);
const manageDialog = ref<HTMLDialogElement | null>(null);
const manage = reactive({ id: "", title: "", groupId: "" });
const groupName = ref("");
let poll: number | undefined;
let searchTimer: number | undefined;
let mounted = true;
const decisionBusy = ref(false);

const rows = computed(() => state.conversations.filter((item) => !state.groupId || item.group_id === state.groupId));
const reviewDraft = computed(() => !state.current?.messages.length && state.current?.title === "代码审查");
const permissionLabels: Record<keyof Permissions, string> = { workspace_write: "写入", command_execute: "命令", mcp_execute: "MCP", delegate: "委派" };
const tools = computed(() => state.events.filter(item => item.kind === "tool_requested").length);
const usage = computed(() => state.events.filter(item => item.kind === "model_usage").reduce((sum, item) => {
  const payload = item.payload;
  return { input: sum.input + Number(payload.prompt_tokens ?? payload.input_tokens ?? 0),
    output: sum.output + Number(payload.completion_tokens ?? payload.output_tokens ?? 0) };
}, { input: 0, output: 0 }));

async function attempt(operation: () => Promise<unknown>): Promise<void> {
  state.error = "";
  try { await operation(); } catch (error) { controller.report(error); }
}

async function draft(mode: "agent" | "review"): Promise<void> {
  if (controller.busy) return;
  await attempt(async () => { await controller.prepareDraft(mode); prompt.value = ""; if (mode === "review") effort.value = "quick"; });
}

async function submit(): Promise<void> {
  if (controller.busy || !prompt.value.trim()) return;
  await attempt(async () => {
    await controller.submit({ prompt: prompt.value, effort: effort.value, permissions: { ...grants } });
    if (state.activeRun) { prompt.value = ""; await controller.refreshRun(); }
  });
}

function keySend(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); }
}

function changedSettings(settings: PublicSettings): void { controller.updateSettings(settings); emit("settings", settings); }

async function filterArchive(archived: boolean): Promise<void> {
  state.archived = archived;
  state.groupId = null;
  await attempt(() => controller.refreshList());
}

function openManage(): void {
  if (!state.current || controller.busy) return;
  Object.assign(manage, { id: state.current.id, title: state.current.title, groupId: state.current.group_id || "" });
  manageDialog.value?.showModal();
}

async function saveManage(): Promise<void> {
  if (controller.busy || !manage.id) return;
  await attempt(async () => {
    await workspaceApi.rename(manage.id, manage.title);
    await workspaceApi.setGroup(manage.id, manage.groupId || null);
    await controller.open(manage.id);
    await controller.refreshList();
    manageDialog.value?.close();
  });
}

async function archiveCurrent(): Promise<void> {
  const current = state.current;
  if (!current || current.id !== manage.id || controller.busy) return;
  await attempt(async () => {
    await workspaceApi.archive(current.id, !Boolean(current.archived));
    await controller.open(current.id);
    await controller.refreshList();
    manageDialog.value?.close();
  });
}

async function deleteCurrent(): Promise<void> {
  if (controller.busy || !manage.id || !window.confirm("永久删除这个对话和它的消息？此操作无法撤销。")) return;
  await attempt(async () => {
    await workspaceApi.delete(manage.id);
    if (state.current?.id === manage.id) controller.clearSelection();
    await controller.refreshList();
    manageDialog.value?.close();
  });
}

async function addGroup(): Promise<void> {
  if (!groupName.value.trim()) return;
  await attempt(async () => { await workspaceApi.createGroup(groupName.value.trim()); groupName.value = ""; await controller.refreshList(); });
}

async function editGroup(remove = false): Promise<void> {
  const group = state.groups.find(item => item.id === state.groupId);
  if (!group) return;
  if (remove) {
    if (!window.confirm(`删除分组“${group.name}”？其中对话会保留。`)) return;
    await attempt(async () => { await workspaceApi.deleteGroup(group.id); state.groupId = null; await controller.refreshList(); });
  } else {
    const name = window.prompt("新的分组名称", group.name);
    if (name?.trim()) await attempt(async () => { await workspaceApi.renameGroup(group.id, name.trim()); await controller.refreshList(); });
  }
}

async function openSearch(): Promise<void> {
  if (settingsOpen.value || manageDialog.value?.open || searchDialog.value?.open) return;
  query.value = "";
  selectedResult.value = 0;
  await controller.search("");
  searchDialog.value?.showModal();
  await nextTick();
  searchInput.value?.focus();
}

async function chooseResult(row?: ConversationSummary): Promise<void> {
  if (!row) return;
  await attempt(async () => {
    state.archived = !!row.archived;
    state.groupId = null;
    await controller.open(row.id);
    await controller.refreshList();
    searchDialog.value?.close();
  });
}

function searchKey(event: KeyboardEvent): void {
  if (event.isComposing) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    selectedResult.value = searchIndex(selectedResult.value, event.key === "ArrowDown" ? 1 : -1, state.searchResults.length);
  } else if (event.key === "Enter") { event.preventDefault(); void chooseResult(state.searchResults[selectedResult.value]); }
}

async function decide(id: string, allow: boolean): Promise<void> {
  if (decisionBusy.value) return;
  decisionBusy.value = true;
  try { await attempt(() => controller.decide(id, allow)); } finally { decisionBusy.value = false; }
}

watch(query, () => {
  window.clearTimeout(searchTimer);
  // Invalidate older responses immediately, including clearing a query.
  void controller.search("");
  selectedResult.value = 0;
  searchTimer = window.setTimeout(() => { void attempt(() => controller.search(query.value)); }, 140);
});
watch(() => props.active, (active) => { if (!active) { searchDialog.value?.close(); manageDialog.value?.close(); settingsOpen.value = false; } });

onMounted(async () => {
  await controller.initialize();
  if (!mounted) return;
  if (state.settings) emit("settings", state.settings);
  poll = window.setInterval(() => { void attempt(() => controller.refreshRun()); }, 700);
});
onBeforeUnmount(() => { mounted = false; controller.dispose(); window.clearInterval(poll); window.clearTimeout(searchTimer); });
async function prepareProjectSwitch(): Promise<void> {
  if (state.loading || controller.busy || decisionBusy.value) throw new Error("Legacy operation is still active");
  await controller.prepareClose();
}
defineExpose({ openSearch, prepareProjectSwitch, prepareClose: () => controller.prepareClose() });
</script>

<template>
  <section class="conversation-workspace legacy-workspace" aria-label="持久对话工作区">
    <aside class="conversation-sidebar">
      <p class="sr-only">PERSISTENT CONVERSATIONS</p><div class="codex-sidebar-heading"><h1 class="classic-product-name">Legacy 历史</h1><button type="button" class="icon-button codex-search" title="搜索对话" aria-label="搜索对话" @click="openSearch"><Search :size="16" aria-hidden="true" /></button></div>
      <div class="button-row workspace-actions"><button type="button" :disabled="controller.busy || state.loading" @click="draft('agent')"><MessageSquarePlus :size="16" aria-hidden="true" />新对话</button><button type="button" class="icon-button" title="代码审查" aria-label="代码审查" :disabled="controller.busy || state.loading" @click="draft('review')"><ScanLine :size="17" aria-hidden="true" /><span class="sr-only">代码审查</span></button></div>

      <div class="filter-row"><button type="button" :aria-pressed="!state.archived" @click="filterArchive(false)">最近</button><button type="button" class="icon-button" title="已归档" aria-label="已归档" :aria-pressed="state.archived" @click="filterArchive(true)"><Archive :size="16" aria-hidden="true" /><span class="sr-only">已归档</span></button></div>
      <label class="field-label" for="conversation-group-filter">分组</label><select id="conversation-group-filter" v-model="state.groupId"><option :value="null">全部对话</option><option v-for="group in state.groups" :key="group.id" :value="group.id">{{ group.name }} · {{ group.conversation_count }}</option></select>
      <form class="inline-form group-create" @submit.prevent="addGroup"><label class="sr-only" for="new-group-name">新分组名称</label><input id="new-group-name" v-model="groupName" maxlength="60" placeholder="新分组" /><button type="submit" class="icon-button" aria-label="创建分组"><FolderPlus :size="17" aria-hidden="true" /></button></form>
      <div v-if="state.groupId" class="button-row"><button type="button" @click="editGroup()">重命名分组</button><button type="button" @click="editGroup(true)">删除分组</button></div>
      <nav class="conversation-list" aria-label="对话列表"><button v-for="item in rows" :key="item.id" type="button" :aria-current="state.current?.id === item.id ? 'page' : undefined" @click="attempt(() => controller.open(item.id))"><strong>{{ item.title }}</strong><small>{{ item.group_name || item.updated_at }} · {{ item.message_count }} 条消息</small></button><p v-if="!rows.length" class="helper">{{ state.loading ? '加载中…' : '这里还没有对话。' }}</p></nav>
      <div class="classic-sidebar-bottom"><button type="button" class="classic-model-card" title="模型设置" aria-label="模型设置" :disabled="!state.settings" @click="settingsOpen = true"><Settings2 :size="18" aria-hidden="true" /><small>{{ state.settings?.profiles.find(p => p.id === state.profileId)?.name || '模型设置' }}</small></button><a class="legacy-ui-link" href="/legacy/" :title="state.workspace">旧版界面（验收前保留）</a></div>
    </aside>

    <main :id="props.active ? 'main-content' : undefined" class="conversation-main" tabindex="-1">
      <div v-if="state.error" class="global-alert" role="alert"><span>{{ state.error }}</span><button type="button" aria-label="关闭对话错误提示" @click="state.error = ''">×</button></div>
      <header class="section-heading classic-chat-header"><div :title="state.workspace"><h2>{{ state.current?.title || '新对话' }}</h2></div><div class="button-row"><details class="legacy-runtime-note"><summary title="Legacy 历史说明" aria-label="Legacy 历史说明"><Info :size="17" aria-hidden="true" /></summary><div class="legacy-runtime-popover"><p class="eyebrow">LEGACY CHAT / EXISTING HISTORY</p><p class="helper">此页沿用持久对话服务与已有历史；Graph / Deep 的原生运行、取消和审批在 Runtime 面板中，不会偷偷切换运行边界。此旧版 console 不绑定上方项目上下文；需要固定上下文请在原生会话 / 独立运行页显式勾选（包括其 Legacy 模式）。</p></div></details><button type="button" class="icon-button" title="管理对话" aria-label="管理对话" :disabled="!state.current || controller.busy" @click="openManage"><MoreHorizontal :size="18" aria-hidden="true" /><span class="sr-only">管理对话</span></button><button type="button" class="icon-button" title="执行详情" aria-label="执行详情" :aria-pressed="inspector" aria-controls="legacy-inspector" @click="inspector = !inspector"><PanelRight :size="18" aria-hidden="true" /><span class="sr-only">执行详情</span></button></div></header>
      <section class="conversation-messages" aria-live="polite" aria-label="对话消息">
        <template v-if="state.current?.messages.length"><article v-for="(item, index) in state.current.messages" :key="`${state.current.id}-${index}`" class="chat-message" :data-role="item.role"><header>{{ item.role === 'user' ? '你' : `Doppel${item.model ? ' · ' + item.model : ''}` }}</header><div v-if="item.role === 'assistant'" class="chat-markdown" v-html="safeMarkdown(item.content)" /><pre v-else>{{ item.content }}</pre></article></template>
        <div v-else class="empty-inline classic-welcome"><h2>{{ reviewDraft ? '选择一种审查方式。' : '浏览历史，继续工作。' }}</h2><p class="sr-only">模板只填入提示词，不会自动启动模型。</p><div class="classic-suggestions"><button type="button" @click="prompt = '先绘制项目结构，再定向搜索高风险代码。只读审查可复现问题，按严重度给出文件、行号、证据和最小修复建议。'; state.nextMode = 'review'">全面审查</button><button type="button" @click="prompt = '只读审查安全与权限边界，优先检查凭据、路径越界、注入、鉴权与危险命令。'; state.nextMode = 'review'">安全审查</button><button type="button" @click="prompt = '检查测试策略和失败风险，列出最值得补充的三个测试。'; state.nextMode = 'agent'">检查测试</button><button type="button" @click="prompt = '阅读项目结构和入口文件，说明模块与数据流。'; state.nextMode = 'agent'">理解代码库</button></div></div>
      <p v-if="state.activeRun" role="status">运行 {{ state.activeRun.run_id }} · {{ state.activeRun.status }} · 所属对话 {{ state.activeRun.conversation_id }}</p>
      <section v-if="state.approvals.length" class="approval-card" aria-label="持久对话工具审批"><h3>待审批工具</h3><div v-for="item in state.approvals" :key="item.id" class="button-row"><code>{{ item.tool }}</code><button type="button" :disabled="decisionBusy" @click="decide(item.id, true)">允许</button><button type="button" :disabled="decisionBusy" @click="decide(item.id, false)">拒绝</button></div></section>
      <section v-if="inspector" id="legacy-inspector" class="output-card legacy-inspector" aria-label="对话执行详情"><h3>真实运行事件</h3><p>工具 {{ tools }} · 输入 {{ usage.input }} / 输出 {{ usage.output }} Token · 任务 {{ state.tasks.length }}</p><p class="helper">这里只展示该持久对话运行的实测事件，不把工具次数或 Token 当作质量分。</p><pre>{{ JSON.stringify({ tasks: state.tasks, events: state.events.slice(-80) }, null, 2) }}</pre></section>
      </section>
      <form class="chat-composer" @submit.prevent="submit">
        <label class="sr-only" for="chat-prompt">任务 · Enter 发送，Shift Enter 换行</label><textarea id="chat-prompt" v-model="prompt" maxlength="100000" rows="2" placeholder="给 Doppel 一个任务" :disabled="!!state.current?.archived" required @keydown="keySend" />
        <div class="classic-composer-toolbar">
          <details class="classic-composer-options"><summary title="任务模板与显式权限" aria-label="任务模板与显式权限"><Plus :size="19" aria-hidden="true" /></summary><div class="classic-permission-popover">
            <CodingTemplates v-if="!reviewDraft" :blocked="controller.busy || !!state.current?.archived" :has-draft="!!prompt" @choose="prompt = $event; state.nextMode = 'agent'" />
            <fieldset class="permissions"><legend>显式权限</legend><label v-for="(name, key) in permissionLabels" :key="key"><input v-model="grants[key]" type="checkbox" /><span>{{ name }}</span></label></fieldset>
          </div></details>
          <div class="field-grid"><label><span class="field-label">模型档案</span><select title="模型档案" :value="state.profileId" :disabled="controller.busy || !state.settings" @change="attempt(() => controller.chooseProfile(($event.target as HTMLSelectElement).value))"><option v-for="profile in state.settings?.profiles" :key="profile.id" :value="profile.id">{{ profile.name }} · {{ profile.model }}</option></select></label><label><span class="field-label">推理力度</span><select title="推理力度" v-model="effort"><option value="quick">quick</option><option value="balanced">balanced</option><option value="deep">deep</option></select></label></div>
          <button class="primary-button classic-send" type="submit" title="发送任务" :disabled="controller.busy || !prompt.trim() || !state.profileId || !!state.current?.archived"><ArrowUp :size="19" aria-hidden="true" /><span class="sr-only">{{ controller.busy ? '运行 / 提交中…' : '发送任务' }}</span></button>
        </div>
        <p v-if="state.current?.archived" class="helper">归档对话只读；在管理对话中取消归档后继续。</p>
      </form>

    </main>

    <dialog ref="searchDialog" class="workspace-dialog" aria-labelledby="workspace-search-title"><section class="workspace-dialog-content"><header class="section-heading"><h2 id="workspace-search-title">搜索标题与消息（包括归档）</h2><button type="button" class="icon-button" aria-label="关闭搜索" @click="searchDialog?.close()">×</button></header><label class="sr-only" for="workspace-search">关键词</label><input id="workspace-search" ref="searchInput" v-model="query" type="search" autocomplete="off" @keydown="searchKey" /><p role="status">{{ state.searching ? '搜索中…' : state.searchResults.length + ' 个结果 · ↑↓ 选择，Enter 打开' }}</p><nav class="conversation-list" aria-label="搜索结果"><button v-for="(row, index) in state.searchResults" :key="row.id" type="button" :class="{ selected: index === selectedResult }" @click="chooseResult(row)"><strong>{{ row.title }} {{ row.archived ? '· 已归档' : '' }}</strong><small>{{ row.preview }}</small></button></nav></section></dialog>
    <dialog ref="manageDialog" class="workspace-dialog" aria-labelledby="manage-conversation-title"><form class="workspace-dialog-content" @submit.prevent="saveManage"><header class="section-heading"><h2 id="manage-conversation-title">管理对话</h2><button type="button" class="icon-button" aria-label="关闭对话管理" @click="manageDialog?.close()">×</button></header><label><span class="field-label">标题</span><input v-model="manage.title" maxlength="80" required /></label><label><span class="field-label">分组</span><select v-model="manage.groupId"><option value="">未分组</option><option v-for="group in state.groups" :key="group.id" :value="group.id">{{ group.name }}</option></select></label><footer class="button-row"><button type="button" class="danger-button" @click="deleteCurrent">永久删除</button><button type="button" @click="archiveCurrent">{{ state.current?.archived ? '取消归档' : '归档' }}</button><button type="submit" class="primary-button">保存</button></footer></form></dialog>
    <ModelSettings v-if="settingsOpen && state.settings" :settings="state.settings" @close="settingsOpen = false" @changed="changedSettings" />
  </section>
</template>
