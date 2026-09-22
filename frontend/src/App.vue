<script setup lang="ts">
import { Activity, AlertTriangle, Braces, Clock3, Copy, FileDiff, Gauge, OctagonX, Radio, TerminalSquare } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { runtimeApi } from "./api";
import ApprovalPanel from "./components/ApprovalPanel.vue";
import EventTimeline from "./components/EventTimeline.vue";
import RunComposer from "./components/RunComposer.vue";
import StatusBadge from "./components/StatusBadge.vue";
import SubagentPanel from "./components/SubagentPanel.vue";
import { elapsed, extractDiff, formatDuration, mergeEvents } from "./runtime";
import type { RunRecord, RunRequest, RuntimeEvent, SubagentRecord } from "./types";

const apiState = ref<"checking" | "online" | "offline">("checking");
const run = ref<RunRecord | null>(null);
const events = ref<RuntimeEvent[]>([]);
const subagents = ref<SubagentRecord[]>([]);
const busy = ref(false);
const connected = ref(false);
const message = ref("");
const copied = ref(false);
const clock = ref(Date.now());
let streamController: AbortController | null = null;
let pollTimer: number | undefined;

const terminal = new Set(["completed", "failed", "cancelled", "interrupted_expired"]);
const interrupt = computed(() => run.value?.metadata.interrupts?.[0] ?? null);
const diff = computed(() => extractDiff(events.value, run.value));
const queueTime = computed(() => formatDuration(elapsed(run.value?.created_at, run.value?.started_at)));
const runtimeTime = computed(() => formatDuration(elapsed(
  run.value?.started_at,
  run.value?.finished_at ?? (run.value?.status === "running" ? new Date(clock.value).toISOString() : run.value?.updated_at),
)));
const permissions = computed(() => run.value?.request.permissions);

function report(error: unknown): void {
  message.value = error instanceof Error ? error.message : String(error);
}

async function refresh(): Promise<void> {
  if (!run.value) return;
  const runId = run.value.run_id;
  try {
    run.value = await runtimeApi.getRun(runId);
    const last = events.value.at(-1)?.seq ?? 0;
    events.value = mergeEvents(events.value, await runtimeApi.events(runId, last));
    if (permissions.value?.delegate) subagents.value = await runtimeApi.subagents(runId);
    if (terminal.has(run.value.status)) {
      disconnect();
      window.clearInterval(pollTimer);
    }
  } catch (error) {
    report(error);
  }
}

function schedulePoll(): void {
  window.clearInterval(pollTimer);
  pollTimer = window.setInterval(() => { clock.value = Date.now(); void refresh(); }, 900);
}

function connect(runId: string): void {
  streamController?.abort();
  streamController = new AbortController();
  const after = events.value.at(-1)?.seq ?? 0;
  connected.value = true;
  void runtimeApi.streamEvents(runId, after, streamController.signal, (item) => {
      events.value = mergeEvents(events.value, [item]);
      void refresh();
    }).catch((error: unknown) => {
      if (!(error instanceof DOMException && error.name === "AbortError")) report(error);
    }).finally(() => { connected.value = false; });
}

function disconnect(): void {
  streamController?.abort();
  streamController = null;
  connected.value = false;
}

async function createRun(request: RunRequest): Promise<void> {
  busy.value = true;
  message.value = "";
  events.value = [];
  subagents.value = [];
  try {
    const accepted = await runtimeApi.createRun(request);
    localStorage.setItem("doppel.runtime.lastRun", accepted.run_id);
    run.value = await runtimeApi.getRun(accepted.run_id);
    connect(accepted.run_id);
    schedulePoll();
  } catch (error) { report(error); }
  finally { busy.value = false; }
}

async function cancelRun(): Promise<void> {
  if (!run.value) return;
  busy.value = true;
  try { await runtimeApi.cancel(run.value.run_id); await refresh(); }
  catch (error) { report(error); }
  finally { busy.value = false; }
}

async function decide(action: "approve" | "reject" | "edit", toolCalls?: Array<Record<string, unknown>>): Promise<void> {
  if (!run.value || !interrupt.value) return;
  busy.value = true;
  try {
    await runtimeApi.resume(run.value.run_id, interrupt.value.id, { action, ...(toolCalls ? { tool_calls: toolCalls } : {}) });
    await refresh();
    connect(run.value.run_id);
  } catch (error) { report(error); }
  finally { busy.value = false; }
}

async function spawnSubagent(prompt: string): Promise<void> {
  if (!run.value) return;
  busy.value = true;
  try { await runtimeApi.spawnSubagent(run.value.run_id, prompt); await refresh(); }
  catch (error) { report(error); }
  finally { busy.value = false; }
}

async function followup(id: string, prompt: string): Promise<void> {
  if (!run.value || !prompt.trim()) return;
  busy.value = true;
  try { await runtimeApi.followUpSubagent(run.value.run_id, id, prompt.trim()); await refresh(); }
  catch (error) { report(error); }
  finally { busy.value = false; }
}

async function cancelSubagent(id: string): Promise<void> {
  if (!run.value) return;
  busy.value = true;
  try { await runtimeApi.cancelSubagent(run.value.run_id, id); await refresh(); }
  catch (error) { report(error); }
  finally { busy.value = false; }
}

async function copyRunId(): Promise<void> {
  if (!run.value) return;
  await navigator.clipboard.writeText(run.value.run_id);
  copied.value = true;
  window.setTimeout(() => { copied.value = false; }, 1600);
}

onMounted(async () => {
  try {
    await runtimeApi.health();
    apiState.value = "online";
    const saved = localStorage.getItem("doppel.runtime.lastRun");
    if (saved) {
      run.value = await runtimeApi.getRun(saved);
      events.value = await runtimeApi.events(saved);
      if (permissions.value?.delegate) subagents.value = await runtimeApi.subagents(saved);
      if (!terminal.has(run.value.status)) {
        connect(saved);
        schedulePoll();
      }
    } else {
      schedulePoll();
    }
  } catch (error) {
    apiState.value = "offline";
    message.value = "v1 Runtime API 不可用。请通过 Doppel Desktop 或 doppel-api 启动混合服务。";
  }
});

onBeforeUnmount(() => { disconnect(); window.clearInterval(pollTimer); });
</script>

<template>
  <div class="app-frame">
    <header class="topbar">
      <a class="brand" href="/runtime/" aria-label="Doppel Runtime Workbench 首页">
        <span class="brand-mark" aria-hidden="true"><Braces :size="20" /></span>
        <span><strong>DOPPEL</strong><small>RUNTIME WORKBENCH</small></span>
      </a>
      <div class="api-health" :data-state="apiState" role="status" aria-live="polite"><Radio :size="15" aria-hidden="true" />API {{ apiState }}</div>
    </header>

    <div v-if="message" class="global-alert" role="alert"><AlertTriangle :size="18" aria-hidden="true" /><span>{{ message }}</span><button type="button" aria-label="关闭错误提示" @click="message = ''">×</button></div>

    <div class="workspace-grid">
      <aside class="control-panel"><RunComposer :busy="busy || apiState !== 'online'" @submit="createRun" /></aside>

      <main id="main-content" class="main-panel" tabindex="-1">
        <template v-if="run">
          <section class="run-hero" aria-labelledby="run-title">
            <div><p class="eyebrow">ACTIVE RUN</p><h1 id="run-title">{{ run.request.prompt }}</h1></div>
            <StatusBadge :status="run.status" />
            <div class="run-id"><code>{{ run.run_id }}</code><button class="icon-button" type="button" :aria-label="copied ? '已复制运行 ID' : '复制运行 ID'" @click="copyRunId"><Copy :size="16" aria-hidden="true" /></button></div>
          </section>

          <section class="metric-strip" aria-label="运行指标">
            <div><Clock3 :size="17" aria-hidden="true" /><span>排队</span><strong>{{ queueTime }}</strong></div>
            <div><Gauge :size="17" aria-hidden="true" /><span>执行</span><strong>{{ runtimeTime }}</strong></div>
            <div><Activity :size="17" aria-hidden="true" /><span>事件</span><strong>{{ events.length }}</strong></div>
            <div><TerminalSquare :size="17" aria-hidden="true" /><span>模式</span><strong>{{ run.mode }}</strong></div>
          </section>

          <ApprovalPanel v-if="run.status === 'interrupted' && interrupt" :interrupt="interrupt" :busy="busy" @decide="decide" />

          <section class="output-card" aria-labelledby="answer-title">
            <div class="section-heading"><div><p class="eyebrow">MODEL OUTPUT</p><h2 id="answer-title">运行结果</h2></div><button v-if="['queued', 'running'].includes(run.status)" type="button" class="danger-button compact" :disabled="busy" @click="cancelRun"><OctagonX :size="16" aria-hidden="true" />取消</button></div>
            <pre v-if="run.answer" class="answer">{{ run.answer }}</pre>
            <p v-else-if="run.error" class="field-error">{{ run.error }}</p>
            <div v-else class="skeleton" aria-label="等待运行输出"><span /><span /><span /></div>
          </section>

          <section class="diff-card" aria-labelledby="diff-title">
            <div class="section-heading"><div><p class="eyebrow">EXACT PATCH</p><h2 id="diff-title">统一差异</h2></div><FileDiff :size="19" aria-hidden="true" /></div>
            <pre v-if="diff" class="diff-output">{{ diff }}</pre><div v-else class="empty-inline">尚未提出补丁；不会伪造差异或验证结果。</div>
          </section>

          <SubagentPanel :items="subagents" :enabled="Boolean(permissions?.delegate)" :busy="busy" @spawn="spawnSubagent" @followup="followup" @cancel="cancelSubagent" />
        </template>
        <section v-else class="welcome-card">
          <div class="radar" aria-hidden="true"><span /><span /><span /></div>
          <p class="eyebrow">LOCAL-FIRST / AUDITABLE / DURABLE</p>
          <h1>让 Agent 的每一步都可见。</h1>
          <p>选择 Legacy、LangGraph 或 DeepAgent 运行时。这里会把状态迁移、Skill、MCP、子代理、审批和真实补丁放在同一条可审计时间线上。</p>
          <ul><li>SQLite 持久事件 + SSE 增量回放</li><li>中断后批准、拒绝或编辑工具调用</li><li>有界异步子代理与精确运行耗时</li></ul>
        </section>
      </main>

      <aside class="inspector-panel"><EventTimeline :events="events" :connected="connected" /></aside>
    </div>
    <div class="sr-only" aria-live="polite">{{ copied ? '运行 ID 已复制' : '' }}</div>
  </div>
</template>
