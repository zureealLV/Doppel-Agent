<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { FolderPlus, MessageSquarePlus, Search, Settings2, ChevronDown, MoreHorizontal, PanelRight, Archive, Plus, ArrowUp, ScanLine, Network, FlaskConical, ShieldCheck, Pencil, Trash2, Workflow } from "lucide-vue-next";

import ModelSettings from "./ModelSettings.vue";
import CodingTemplates from "./CodingTemplates.vue";
import RunInspector from "./RunInspector.vue";
import { nativeWorkspaceApi as workspaceApi } from "../workspaceApi";
import { browserStorage, safeMarkdown, searchIndex, NativeWorkspaceController } from "../workspace";
import type { Effort, Permissions, PrepareContext } from "../types";
import type { ConversationSummary, PublicSettings } from "../workspaceTypes";

const props = defineProps<{ active: boolean; sharedSettings?: PublicSettings | null; prepareContext?: PrepareContext }>();
const emit = defineEmits<{ settings: [settings: PublicSettings]; "open-changes": [runId: string] }>();
const controller = new NativeWorkspaceController(browserStorage(), scope => {
  if (!props.prepareContext) throw new Error("上下文面板尚未就绪。");
  return props.prepareContext(scope);
});
const state = controller.state;
const prompt = ref("");
const includeContext = ref(false);
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
const groupRenameDialog = ref<HTMLDialogElement | null>(null);
const groupRenameInput = ref<HTMLInputElement | null>(null);
const groupRename = reactive({ id: "", name: "", busy: false });
let poll: number | undefined;
let searchTimer: number | undefined;
let mounted = true;


const rows = computed(() => state.conversations.filter((item) => !state.groupId || item.group_id === state.groupId));
const reviewDraft = computed(() => !state.current?.messages.length && state.current?.title === "代码审查");
const composerBlocked = computed(() => controller.busy || !!state.current?.archived);
const permissionLabels: Record<keyof Permissions, string> = { workspace_write: "写入", command_execute: "命令", mcp_execute: "MCP", delegate: "委派" };
async function attempt(operation: () => Promise<unknown>): Promise<void> {
  state.error = "";
  try { await operation(); } catch (error) { controller.report(error); }
}

async function draft(mode: "agent" | "review"): Promise<void> {
  if (controller.busy) return;
  await attempt(async () => { await controller.prepareDraft(mode === "review"); prompt.value = ""; if (mode === "review") { effort.value = "quick"; Object.keys(grants).forEach(key => { grants[key as keyof Permissions] = false; }); } });
}

async function submit(): Promise<void> {
  if (composerBlocked.value || !prompt.value.trim()) return;
  await attempt(async () => {
    await controller.submit({ prompt: prompt.value, effort: effort.value, permissions: { ...grants } }, includeContext.value);
    if (state.current?.active_run_id) { prompt.value = ""; await controller.refreshRun(); }
  });
}

async function retrySubmission(): Promise<void> {
  await attempt(async () => { await controller.retrySubmission(); if (!controller.submissionUncertain && state.selectedRunId) prompt.value = ""; });
}

function keySend(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); }
}

function changedSettings(settings: PublicSettings): void { controller.updateSettings(settings); emit("settings", settings); }

async function selectProfile(event: Event): Promise<void> {
  const select = event.target as HTMLSelectElement;
  await attempt(() => controller.chooseProfile(select.value));
  select.value = state.profileId;
}

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
    await workspaceApi.update(manage.id, { title: manage.title, group_id: manage.groupId || null });
    await controller.open(manage.id);
    await controller.refreshList();
    manageDialog.value?.close();
  });
}

async function archiveCurrent(): Promise<void> {
  const current = state.current;
  if (!current || current.id !== manage.id || controller.busy) return;
  await attempt(async () => {
    await workspaceApi.update(current.id, { archived: !Boolean(current.archived) });
    await controller.open(current.id);
    await controller.refreshList();
    manageDialog.value?.close();
  });
}

async function deleteCurrent(): Promise<void> {
  if (controller.busy || !manage.id || !window.confirm("删除可见对话和消息？运行审计、事件和检查点仍会保留；这不是安全擦除。")) return;
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
  if (groupRename.busy) return;
  const group = state.groups.find(item => item.id === state.groupId);
  if (!group) return;
  if (remove) {
    if (!window.confirm(`删除分组“${group.name}”？其中对话会保留。`)) return;
    await attempt(async () => { await workspaceApi.deleteGroup(group.id); state.groupId = null; await controller.refreshList(); });
  } else {
    groupRename.id = group.id; groupRename.name = group.name;
    groupRenameDialog.value?.showModal();
    await nextTick(); groupRenameInput.value?.focus();
  }
}

async function saveGroupRename(): Promise<void> {
  if (groupRename.busy || !groupRename.id || !groupRename.name.trim()) return;
  groupRename.busy = true;
  await attempt(async () => {
    await workspaceApi.renameGroup(groupRename.id, groupRename.name.trim());
    await controller.refreshList(); groupRenameDialog.value?.close();
  });
  groupRename.busy = false;
}

async function openSearch(): Promise<void> {
  if (settingsOpen.value || manageDialog.value?.open || searchDialog.value?.open || groupRenameDialog.value?.open) return;
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

watch(() => props.sharedSettings, settings => { if (settings) controller.updateSettings(settings); });
watch(() => state.current?.id, () => { prompt.value = ""; });
watch(query, () => {
  window.clearTimeout(searchTimer);
  // Invalidate older responses immediately, including clearing a query.
  void controller.search("");
  selectedResult.value = 0;
  searchTimer = window.setTimeout(() => { void attempt(() => controller.search(query.value)); }, 140);
});
watch(() => props.active, (active) => { if (!active) { searchDialog.value?.close(); manageDialog.value?.close(); settingsOpen.value = false; } });

function beforeUnload(event: BeforeUnloadEvent) {
  if (state.submitting || controller.submissionUncertain) { event.preventDefault(); event.returnValue = ""; }
}

onMounted(async () => {
  window.addEventListener("beforeunload", beforeUnload);
  await controller.initialize();
  if (!mounted) return;
  if (state.settings) emit("settings", state.settings);
  poll = window.setInterval(() => { void attempt(() => controller.refreshRun()); }, 700);
});
onBeforeUnmount(() => { mounted = false; controller.dispose(); window.clearInterval(poll); window.clearTimeout(searchTimer); window.removeEventListener("beforeunload", beforeUnload); });
defineExpose({ openSearch, prepareClose: () => controller.prepareClose(), finishClose: () => controller.finishClose() });
</script>

<template>
  <section class="conversation-workspace native-workspace" :class="{ inspecting: inspector && props.active }" aria-label="原生对话工作区">
    <aside class="conversation-sidebar">
      <div class="codex-sidebar-heading">
        <h1 class="classic-product-name">Doppel</h1>
        <details class="runtime-selection"><summary aria-label="选择运行时" :title="`运行时：${state.current?.mode || state.mode}`"><ChevronDown :size="14" aria-hidden="true" /></summary><div class="codex-sidebar-popover">
          <p class="eyebrow">NATIVE CONVERSATION / V1 RUNTIME</p>
          <label class="field-label" for="native-draft-mode">新对话运行时（已创建后不可变）</label><select id="native-draft-mode" v-model="state.mode" :disabled="controller.busy"><option value="graph">Graph · LangGraph</option><option value="deep">Deep · DeepAgent</option><option value="legacy">Legacy · Core（受审原生运行，支持持久审批）</option></select>
          <p class="helper">此页使用原生 v1 API；运行时 {{ state.current?.mode || state.mode }}。旧 conversations.sqlite3 历史在独立 Legacy 入口，不迁移、不冒充 Graph / Deep。</p>
        </div></details>
        <button type="button" class="icon-button codex-search" title="搜索对话" aria-label="搜索对话" aria-keyshortcuts="Control+k" @click="openSearch"><Search :size="16" aria-hidden="true" /><span class="sr-only">搜索对话</span></button>
      </div>
      <div class="button-row workspace-actions codex-new-chat"><button type="button" title="新对话" :disabled="controller.busy || !state.profileId" @click="draft('agent')"><MessageSquarePlus :size="16" aria-hidden="true" />新对话</button><button type="button" class="icon-button" title="代码审查" aria-label="代码审查" :disabled="controller.busy || state.loading" @click="draft('review')"><ScanLine :size="17" aria-hidden="true" /><span class="sr-only">代码审查</span></button></div>
      <div class="codex-group-row"><label class="sr-only" for="native-conversation-group-filter">分组</label><select id="native-conversation-group-filter" v-model="state.groupId" title="筛选对话分组"><option :value="null">全部对话</option><option v-for="group in state.groups" :key="group.id" :value="group.id">{{ group.name }} · {{ group.conversation_count }}</option></select>
        <details class="codex-group-options"><summary title="管理分组" aria-label="管理分组"><FolderPlus :size="16" aria-hidden="true" /></summary><div class="codex-sidebar-popover">
          <form class="inline-form group-create" @submit.prevent="addGroup"><label class="sr-only" for="native-new-group-name">新分组名称</label><input id="native-new-group-name" v-model="groupName" maxlength="60" placeholder="新分组" /><button type="submit" class="icon-button" title="创建分组" aria-label="创建分组"><Plus :size="17" aria-hidden="true" /></button></form>
          <div v-if="state.groupId" class="button-row"><button type="button" title="重命名分组" aria-label="重命名分组" @click="editGroup()"><Pencil :size="16" aria-hidden="true" /><span class="sr-only">重命名分组</span></button><button type="button" title="删除分组" aria-label="删除分组" @click="editGroup(true)"><Trash2 :size="16" aria-hidden="true" /><span class="sr-only">删除分组</span></button></div>
        </div></details>
      </div>
      <div class="filter-row codex-recents"><button type="button" title="最近对话" :aria-pressed="!state.archived" @click="filterArchive(false)">最近</button><button type="button" class="icon-button" title="已归档" aria-label="已归档" :aria-pressed="state.archived" @click="filterArchive(true)"><Archive :size="16" aria-hidden="true" /><span class="sr-only">已归档</span></button></div>
      <nav class="conversation-list" aria-label="对话列表"><button v-for="item in rows" :key="item.id" type="button" :title="`${item.title} · ${item.mode} · ${item.group_name || item.updated_at} · ${item.message_count} 条消息`" :aria-current="state.current?.id === item.id ? 'page' : undefined" @click="attempt(() => controller.open(item.id))"><strong>{{ item.title }}</strong><span class="sr-only">{{ item.mode }} · {{ item.group_name || item.updated_at }} · {{ item.message_count }} 条消息</span></button><p v-if="!rows.length" class="helper">{{ state.loading ? '加载中…' : '这里还没有对话。' }}</p></nav>
      <div class="classic-sidebar-bottom"><button type="button" class="classic-model-card" title="模型与 API" aria-label="模型与 API" :disabled="!state.settings" @click="settingsOpen = true"><Settings2 :size="18" aria-hidden="true" /><span class="sr-only">模型与 API</span><small>{{ state.settings?.profiles.find(p => p.id === state.profileId)?.name || '模型设置' }}</small></button></div>
    </aside>

    <main :id="props.active ? 'main-content' : undefined" class="conversation-main" tabindex="-1">
      <div v-if="state.error" class="global-alert" role="alert"><span>{{ state.error }}</span><button type="button" aria-label="关闭对话错误提示" @click="state.error = ''">×</button></div>
      <p v-if="state.selectionSaving" class="helper" role="status">正在保存历史选择…</p>
      <p v-if="state.selectionError" class="global-alert" role="alert">{{ state.selectionError }}</p>
      <header class="section-heading classic-chat-header"><div :title="state.workspace"><h2>{{ state.current?.title || '新对话' }}</h2></div><div class="button-row"><button type="button" class="icon-button" title="管理对话" aria-label="管理对话" :disabled="!state.current || controller.busy" @click="openManage"><MoreHorizontal :size="18" aria-hidden="true" /><span class="sr-only">管理对话</span></button><button type="button" class="icon-button" title="执行详情" aria-label="执行详情" :aria-pressed="inspector" aria-controls="native-inspector" @click="inspector = !inspector"><PanelRight :size="18" aria-hidden="true" /><span class="sr-only">执行详情</span></button></div></header>
      <p v-if="state.loading" role="status">加载原生历史…</p><button v-if="state.error" type="button" @click="controller.initialize()">重试加载</button>
      <section class="conversation-messages" aria-live="polite" aria-label="对话消息">
        <template v-if="state.current?.messages.length"><article v-for="item in state.current.messages" :key="item.id" class="chat-message" :data-role="item.role"><header>{{ item.role === 'user' ? '你' : `Doppel${item.model ? ' · ' + item.model : ''}` }} · {{ item.status }} <button type="button" class="icon-button" title="查看此轮运行" aria-label="查看此轮运行" @click="controller.selectRun(item.run_id); inspector = true"><Workflow :size="15" aria-hidden="true" /><span class="sr-only">查看此轮运行</span></button></header><div v-if="item.role === 'assistant'" class="chat-markdown" v-html="safeMarkdown(item.content)" /><pre v-else>{{ item.content }}</pre></article></template>
        <div v-else class="empty-inline classic-welcome"><p class="eyebrow sr-only">LOCAL CODE AGENT</p><h2>{{ reviewDraft ? '选择一种审查方式。' : '今天想完成什么？' }}</h2><p>从一个任务开始。</p><p class="sr-only">模板只填入提示词，不会自动启动模型。</p><div class="classic-suggestions"><button type="button" :disabled="composerBlocked" @click="prompt = '先绘制项目结构，再定向搜索高风险代码。只读审查可复现问题，按严重度给出文件、行号、证据和最小修复建议。'"><ScanLine :size="19" aria-hidden="true" /><strong>审查当前项目</strong></button><button type="button" :disabled="composerBlocked" @click="prompt = '阅读项目结构和入口文件，说明模块与数据流。'"><Network :size="19" aria-hidden="true" /><strong>理解代码库</strong></button><button type="button" :disabled="composerBlocked" @click="prompt = '检查测试策略和失败风险，列出最值得补充的三个测试。'"><FlaskConical :size="19" aria-hidden="true" /><strong>检查测试</strong></button><button v-if="reviewDraft" type="button" :disabled="composerBlocked" @click="prompt = '只读审查安全与权限边界，优先检查凭据、路径越界、注入、鉴权与危险命令。'"><ShieldCheck :size="19" aria-hidden="true" /><strong>安全审查</strong></button></div></div>
      </section>
      <p v-if="state.current?.active_run_id" role="status">此对话有进行中或待审批运行：{{ state.current.active_run_id }}。在统一执行详情中取消 / 恢复。</p>
      <form class="chat-composer" @submit.prevent="submit">
        <label class="sr-only" for="native-chat-prompt">任务 · Enter 发送，Shift Enter 换行</label><textarea id="native-chat-prompt" v-model="prompt" maxlength="100000" rows="2" placeholder="给 Doppel 一个任务" :disabled="composerBlocked" required @keydown="keySend" />
        <div class="classic-composer-toolbar">
        <details class="classic-composer-options"><summary aria-label="运行权限、上下文与任务模板" title="运行权限、上下文与任务模板"><Plus :size="19" aria-hidden="true" /></summary><div class="classic-permission-popover">
        <CodingTemplates v-if="!reviewDraft" :blocked="composerBlocked" :has-draft="!!prompt" @choose="prompt = $event" />
        <div class="button-row"><button type="button" :disabled="composerBlocked" @click="prompt = '先绘制项目结构，再定向搜索高风险代码。只读审查可复现问题，按严重度给出文件、行号、证据和最小修复建议。'">全面审查</button><button type="button" :disabled="composerBlocked" @click="prompt = '只读审查安全与权限边界，优先检查凭据、路径越界、注入、鉴权与危险命令。'">安全审查</button></div>
        <fieldset class="permissions" :disabled="composerBlocked"><legend>显式权限</legend><label v-for="(name, key) in permissionLabels" :key="key"><input v-model="grants[key]" type="checkbox" /><span>{{ name }}</span></label></fieldset>
        <label class="context-confirm"><input v-model="includeContext" type="checkbox" :disabled="composerBlocked" />本次绑定已接受的项目上下文 / 已选笔记版本</label>
        <p class="helper">默认不绑定。勾选后先等待项目作用域选择保存；服务接收时检查来源并冻结输入，不随面板改选。权限仍由上方显式开关决定。</p>
        </div></details>
        <div class="field-grid"><label><span class="field-label">模型档案</span><select title="模型档案" :value="state.profileId" :disabled="composerBlocked || !state.settings" @change="selectProfile"><option v-for="profile in state.settings?.profiles" :key="profile.id" :value="profile.id">{{ profile.name }} · {{ profile.model }}</option></select></label><label><span class="field-label">推理力度</span><select title="推理力度" v-model="effort" :disabled="composerBlocked"><option value="quick">quick</option><option value="balanced">balanced</option><option value="deep">deep</option></select></label></div>
        <button class="primary-button classic-send" type="submit" :disabled="composerBlocked || !prompt.trim() || !state.profileId" title="发送任务"><ArrowUp :size="19" aria-hidden="true" /><span class="sr-only">{{ controller.busy ? '运行 / 提交中…' : '发送任务' }}</span></button>
        </div>
        <div v-if="controller.submissionUncertain" class="order-warning" role="alert">上次回复未知；不得改请求另发任务。<button type="button" :disabled="state.submitting || state.closing" @click="retrySubmission">重试原请求（相同上下文与 key）</button></div>
        <p v-if="state.current?.archived" class="helper">归档对话只读；在管理对话中取消归档后继续。</p>
      </form>
    </main>

    <aside v-if="inspector && props.active" id="native-inspector" class="native-inspector">
      <label class="field-label" for="native-run-select">此对话的运行审计</label><select id="native-run-select" :value="state.selectedRunId" @change="controller.selectRun(($event.target as HTMLSelectElement).value)"><option value="">尚无运行</option><option v-for="item in state.current?.runs" :key="item.run_id" :value="item.run_id">{{ item.status }} · {{ item.run_id }}</option></select>
      <button v-if="state.selectedRunId && state.current?.runs.some(item => item.run_id === state.selectedRunId)" type="button" :disabled="state.closing" @click="emit('open-changes', state.selectedRunId)">查看该原生运行的变更证据</button>
      <RunInspector :run-id="state.selectedRunId" :conversation-id="state.current?.id" />
    </aside>
    <dialog ref="searchDialog" class="workspace-dialog" aria-labelledby="native-workspace-search-title"><section class="workspace-dialog-content"><header class="section-heading"><h2 id="native-workspace-search-title">搜索标题与消息（包括归档）</h2><button type="button" class="icon-button" aria-label="关闭搜索" @click="searchDialog?.close()">×</button></header><label class="sr-only" for="native-workspace-search">关键词</label><input id="native-workspace-search" ref="searchInput" v-model="query" type="search" autocomplete="off" @keydown="searchKey" /><p role="status">{{ state.searching ? '搜索中…' : state.searchResults.length + ' 个结果 · ↑↓ 选择，Enter 打开' }}</p><nav class="conversation-list" aria-label="搜索结果"><button v-for="(row, index) in state.searchResults" :key="row.id" type="button" :class="{ selected: index === selectedResult }" @click="chooseResult(row)"><strong>{{ row.title }} {{ row.archived ? '· 已归档' : '' }}</strong><small>{{ row.preview }}</small></button></nav></section></dialog>
    <dialog ref="manageDialog" class="workspace-dialog" aria-labelledby="native-manage-conversation-title"><form class="workspace-dialog-content" @submit.prevent="saveManage"><header class="section-heading"><h2 id="native-manage-conversation-title">管理对话</h2><button type="button" class="icon-button" aria-label="关闭对话管理" @click="manageDialog?.close()">×</button></header><label><span class="field-label">标题</span><input v-model="manage.title" maxlength="80" required /></label><label><span class="field-label">分组</span><select v-model="manage.groupId"><option value="">未分组</option><option v-for="group in state.groups" :key="group.id" :value="group.id">{{ group.name }}</option></select></label><footer class="button-row"><button type="button" class="danger-button" @click="deleteCurrent">删除可见历史</button><button type="button" @click="archiveCurrent">{{ state.current?.archived ? '取消归档' : '归档' }}</button><button type="submit" class="primary-button">保存</button></footer></form></dialog>
    <dialog id="native-group-rename-dialog" ref="groupRenameDialog" class="workspace-dialog" aria-labelledby="native-group-rename-title" @cancel="groupRename.busy && $event.preventDefault()"><form @submit.prevent="saveGroupRename"><header class="dialog-heading"><h2 id="native-group-rename-title">重命名原生分组</h2><button type="button" :disabled="groupRename.busy" @click="groupRenameDialog?.close()">关闭</button></header><label class="field-label" for="native-group-rename-input">新的分组名称</label><input id="native-group-rename-input" ref="groupRenameInput" v-model="groupRename.name" maxlength="200" required :disabled="groupRename.busy" /><p v-if="state.error" role="alert">{{ state.error }}</p><footer class="button-row"><button type="submit" :disabled="groupRename.busy || !groupRename.name.trim()">保存分组名称</button></footer></form></dialog>
    <ModelSettings v-if="settingsOpen && state.settings" :settings="state.settings" @close="settingsOpen = false" @changed="changedSettings" />
  </section>
</template>
