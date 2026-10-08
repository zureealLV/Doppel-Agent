<script setup lang="ts">
import { Radio } from "lucide-vue-next";
import { nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from "vue";
import { runtimeApi } from "./api";
import { RunSubmissionController } from "./runSubmission";
import ConversationWorkspace from "./components/ConversationWorkspace.vue";
import LegacyConversationWorkspace from "./components/LegacyConversationWorkspace.vue";
import RunComposer from "./components/RunComposer.vue";
import RunInspector from "./components/RunInspector.vue";
import DesktopControls from "./components/DesktopControls.vue";
import WorkbenchShell from "./components/WorkbenchShell.vue";
import ProjectHome from "./components/ProjectHome.vue";
import WorkOrdersWorkspace from "./components/WorkOrdersWorkspace.vue";
import ContextPanel from "./components/ContextPanel.vue";
import ChangesWorkspace from "./components/ChangesWorkspace.vue";
import ExtensionCenter from "./components/ExtensionCenter.vue";
import SubagentPanel from "./components/SubagentPanel.vue";
import { ChildReviewController, openChildReviewKey } from "./childReview";
import RunReportPanel from "./components/RunReportPanel.vue";
import { RunReportController, openRunReportKey } from "./runReport";
import { ModelSettingsLifetime, modelSettingsLifetimeKey } from './modelSettingsLifetime';
import { runIdentifier } from "./changesApi";
import { projectApi, type ProjectIdentity } from "./projects";
import { pageFromHash, pageHash, pageAfterHashChange, type WorkspacePage } from "./navigation";
import { browserStorage } from "./workspace";
import type { RunRequest } from "./types";
import type { PublicSettings } from "./workspaceTypes";
const page = ref<WorkspacePage>(pageFromHash(typeof window === "undefined" ? "" : window.location.hash));
function restorePage() {
  if (closing.value || switching.value) { if (window.location.hash !== pageHash(page.value)) window.location.hash = pageHash(page.value); return; }
  page.value = pageAfterHashChange(window.location.hash, page.value);
}
watch(page, value => { if (window.location.hash !== pageHash(value)) window.location.hash = pageHash(value); });
const settings = ref<PublicSettings | null>(null);
const project = ref<ProjectIdentity | null>(null), projectError = ref(false);
const workspace = ref<InstanceType<typeof ConversationWorkspace> | null>(null);
const legacyWorkspace = ref<InstanceType<typeof LegacyConversationWorkspace> | null>(null);
const ordersWorkspace = ref<InstanceType<typeof WorkOrdersWorkspace> | null>(null);
const changesWorkspace = ref<InstanceType<typeof ChangesWorkspace> | null>(null), changesSourceRun = ref("");
const extensionCenter = ref<InstanceType<typeof ExtensionCenter> | null>(null);
const contextPanel = ref<InstanceType<typeof ContextPanel> | null>(null), selectedOrderId = ref<string | null>(null);
const apiState = ref("checking"), runId = ref(""), busy = ref(false), error = ref("");
const closing = ref(false);
const switching = ref(false);
const childReview = new ChildReviewController(), childReviewOpen = ref(false);
const runReport = new RunReportController(), runReportOpen = ref(false);
const modelSettingsLifetime = new ModelSettingsLifetime();
provide(modelSettingsLifetimeKey, modelSettingsLifetime);
provide(openRunReportKey, run => {
  if (closing.value || switching.value) return;
  runReportOpen.value = true; runReport.activate(true); runReport.selectSource(run);
});
function toggleRunReport() {
  if (closing.value || switching.value) return;
  runReportOpen.value = !runReportOpen.value; runReport.activate(runReportOpen.value);
}
provide(openChildReviewKey, parent => {
  if (closing.value || switching.value) return;
  childReviewOpen.value = true; childReview.activate(true); childReview.selectSource(parent);
});
function toggleChildReview() {
  if (closing.value || switching.value) return;
  childReviewOpen.value = !childReviewOpen.value; childReview.activate(childReviewOpen.value);
}
let transitionEpoch = 0;
function assertTransition(epoch: number): void {
  if (epoch !== transitionEpoch) throw new Error("workspace preparation superseded; no late host transition");
}
function navigate(value: WorkspacePage) { if (!closing.value && !switching.value) page.value = value; }
function openChanges(run: string) {
  if (closing.value || switching.value || !runIdentifier(run) || changesWorkspace.value && !changesWorkspace.value.canChangeSource()) return;
  changesSourceRun.value = run; page.value = "changes";
}
async function prepareWindowClose(): Promise<void> {
  closing.value = true;
  const epoch = ++transitionEpoch;
  submission.prepareClose();
  await modelSettingsLifetime.prepareClose(); assertTransition(epoch);
  await runReport.prepareClose(); assertTransition(epoch);
  await childReview.prepareClose(); assertTransition(epoch);
  await extensionCenter.value?.prepareClose(); assertTransition(epoch);
  await changesWorkspace.value?.prepareClose(); assertTransition(epoch);
  await legacyWorkspace.value?.prepareClose(); assertTransition(epoch);
  await workspace.value?.prepareClose(); assertTransition(epoch);
  await ordersWorkspace.value?.prepareClose(); assertTransition(epoch);
  await contextPanel.value?.prepareClose(); assertTransition(epoch);
}
function finishWindowClose(): void { transitionEpoch++; modelSettingsLifetime.finishClose(); runReport.finishClose(); childReview.finishClose(); extensionCenter.value?.finishClose(); changesWorkspace.value?.finishClose(); workspace.value?.finishClose(); ordersWorkspace.value?.finishClose(); contextPanel.value?.finishClose(); closing.value = false; }
async function prepareProjectSwitch(): Promise<void> {
  if (closing.value || busy.value || switching.value) throw new Error("workspace transition is busy");
  submission.prepareClose();
  switching.value = true;
  const epoch = ++transitionEpoch;
  await modelSettingsLifetime.prepareClose(); assertTransition(epoch);
  await runReport.prepareClose(); assertTransition(epoch);
  await childReview.prepareClose(); assertTransition(epoch);
  await extensionCenter.value?.prepareClose(); assertTransition(epoch);
  await changesWorkspace.value?.prepareClose(); assertTransition(epoch);
  await legacyWorkspace.value?.prepareProjectSwitch(); assertTransition(epoch);
  await workspace.value?.prepareClose(); assertTransition(epoch);
  await ordersWorkspace.value?.prepareClose(); assertTransition(epoch);
  await contextPanel.value?.prepareClose(); assertTransition(epoch);
}
function finishProjectSwitch(navigating: boolean): void {
  if (!navigating) { transitionEpoch++; modelSettingsLifetime.finishClose(); runReport.finishClose(); childReview.finishClose(); extensionCenter.value?.finishClose(); changesWorkspace.value?.finishClose(); workspace.value?.finishClose(); ordersWorkspace.value?.finishClose(); contextPanel.value?.finishClose(); switching.value = false; }
}
const submission = new RunSubmissionController(runtimeApi.createRun), submissionState = submission.state;
async function prepareContext(scope: string | null) {
  if (!contextPanel.value) throw new Error("上下文面板尚未就绪；没有提交运行。");
  return contextPanel.value.prepareInput(scope);
}
function ownStandalone(accepted: { run_id: string }) {
  runId.value = accepted.run_id;
  try { browserStorage()?.setItem("doppel.runtime.lastRun", accepted.run_id); } catch { /* Optional. */ }
}
async function createRun(body: RunRequest, includeContext = false) {
  if (busy.value || closing.value || switching.value || submissionState.uncertain) return;
  busy.value = true; error.value = "";
  try {
    const accepted = await submission.submit(body, includeContext ? () => prepareContext(null) : undefined);
    if (accepted) ownStandalone(accepted);
  } catch (e) { error.value = e instanceof Error ? e.message : String(e); }
  finally { busy.value = false; }
}
async function retryStandalone() {
  if (busy.value || closing.value || switching.value) return;
  busy.value = true; error.value = "";
  try { const accepted = await submission.retry(); if (accepted) ownStandalone(accepted); }
  catch (e) { error.value = e instanceof Error ? e.message : "重试未完成。"; }
  finally { busy.value = false; }
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (busy.value || submissionState.uncertain || childReview.unloadBlocked || runReport.unloadBlocked || modelSettingsLifetime.unloadBlocked) { event.preventDefault(); event.returnValue = ""; }
}
async function shortcut(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    if (closing.value || switching.value) { event.preventDefault(); return; }
    event.preventDefault(); if (!["conversations", "legacy"].includes(page.value)) page.value = "conversations";
    await nextTick(); await (page.value === "legacy" ? legacyWorkspace.value : workspace.value)?.openSearch();
  }
}
onMounted(async () => {
  document.addEventListener("keydown", shortcut);
  window.addEventListener("hashchange", restorePage);
  window.addEventListener("beforeunload", beforeUnload);
  try { await runtimeApi.health(); apiState.value = "online"; } catch { apiState.value = "offline"; }
  try { project.value = await projectApi.current(); } catch { projectError.value = true; }
  try { runId.value = browserStorage()?.getItem("doppel.runtime.lastRun") || ""; } catch { /* Optional. */ }
});
onBeforeUnmount(() => { modelSettingsLifetime.dispose(); runReport.dispose(); childReview.dispose(); document.removeEventListener("keydown", shortcut); window.removeEventListener("hashchange", restorePage); window.removeEventListener("beforeunload", beforeUnload); });
</script>
<template>
  <WorkbenchShell :page="page" :closing="closing || switching" :project="project" :project-error="projectError" @navigate="navigate">
    <template #projects><ProjectHome :blocked="closing" :before-switch="prepareProjectSwitch" :after-switch="finishProjectSwitch" /></template>
    <template #status><div class="api-health" :data-state="apiState" role="status" :title="`API ${apiState}`"><Radio :size="14" aria-hidden="true" /><span class="sr-only">API {{ apiState }}</span></div></template>
    <template #window-controls><div :inert="switching || undefined"><DesktopControls :before-close="prepareWindowClose" :after-close="finishWindowClose" /></div></template>
    <template #context><ContextPanel ref="contextPanel" :work-order-id="selectedOrderId" :blocked="closing || switching" /></template>
    <div v-if="runReport.state.runId" class="report-entry"><button type="button" :disabled="closing || switching" :aria-expanded="runReportOpen" aria-controls="root-run-report" @click="toggleRunReport">{{ runReportOpen ? '隐藏运行报告（保留原请求）' : '打开已固定的运行报告' }}</button></div>
    <div id="root-run-report" v-show="runReportOpen"><RunReportPanel :controller="runReport" :active="runReportOpen" :blocked="closing || switching" /></div>
    <div v-if="childReview.state.parentRunId" class="child-review-entry"><button type="button" :disabled="closing || switching" :aria-expanded="childReviewOpen" aria-controls="root-child-review" @click="toggleChildReview">{{ childReviewOpen ? '隐藏子任务审查（保留原请求和草稿）' : '打开已固定的子任务审查' }}</button><span v-if="childReview.state.intent" role="status">原子任务请求仍固定在父任务 {{ childReview.state.parentRunId }}；未自动解锁或重发。</span></div>
    <div id="root-child-review" v-show="childReviewOpen"><SubagentPanel :controller="childReview" :active="childReviewOpen" :blocked="closing || switching" /></div>
    <ConversationWorkspace ref="workspace" v-show="page === 'conversations'" :inert="closing || switching || undefined" :active="page === 'conversations'" :shared-settings="settings" :prepare-context="prepareContext" @settings="settings = $event" @open-changes="openChanges" />
    <LegacyConversationWorkspace v-if="page === 'legacy'" ref="legacyWorkspace" :inert="closing || switching || undefined" :active="true" @settings="settings = $event" />
    <WorkOrdersWorkspace ref="ordersWorkspace" v-show="page === 'orders'" :inert="closing || switching || undefined" :active="page === 'orders'" :profiles="settings?.profiles || []" :prepare-context="prepareContext" @selected-order="selectedOrderId = $event" @open-changes="openChanges" />
    <ChangesWorkspace ref="changesWorkspace" v-show="page === 'changes'" :active="page === 'changes'" :blocked="closing || switching" :source-run-id="changesSourceRun" />
    <ExtensionCenter ref="extensionCenter" v-show="page === 'extensions'" :active="page === 'extensions'" :blocked="closing || switching" />
    <div v-if="page === 'runtime'" class="standalone-workspace" :inert="closing || switching || undefined">
      <aside class="control-panel"><RunComposer :busy="busy || submissionState.uncertain || apiState !== 'online'" :profiles="settings?.profiles || []" @submit="createRun" /><div v-if="submissionState.uncertain" class="order-warning" role="alert">提交回复未知，表单已锁定。<button type="button" :disabled="busy || closing || switching || apiState !== 'online'" @click="retryStandalone">重试原请求（相同输入与 key）</button></div></aside>
      <main id="main-content" class="main-panel" tabindex="-1"><p v-if="error" role="alert">{{ error }}</p><button v-if="runId" type="button" :disabled="closing || switching" @click="openChanges(runId)">读取该原生 run 的变更证据（服务端核对注册范围）</button><RunInspector :run-id="runId" /></main>
    </div>
  </WorkbenchShell>
</template>
