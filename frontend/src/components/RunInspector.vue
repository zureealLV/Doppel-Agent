<script setup lang="ts">
import { Activity, Clock3, Copy, FileDiff, Gauge, OctagonX, TerminalSquare } from "lucide-vue-next";
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ApprovalPanel from "./ApprovalPanel.vue";
import EventTimeline from "./EventTimeline.vue";
import StatusBadge from "./StatusBadge.vue";
import ContextInputReceipt from "./ContextInputReceipt.vue";
import { RunController, terminalStatuses } from "../runController";
import { openChildReviewKey } from "../childReview";
import { openRunReportKey } from "../runReport";
import { elapsed, extractDiff, formatDuration, runtimeEvidence } from "../runtime";
const props = defineProps<{ runId: string; conversationId?: string }>();
const controller = new RunController();
const state = controller.state;
const run = computed(() => state.run), events = computed(() => state.events);
const openChildReview = inject(openChildReviewKey, undefined);
const openRunReport = inject(openRunReportKey, undefined);
const busy = computed(() => state.busy), connected = computed(() => state.connected);
const copied = ref(false), clock = ref(Date.now());
const interrupt = computed(() => run.value?.metadata.interrupts?.[0] ?? null);
const permissions = computed(() => run.value?.request.permissions);
const evidence = computed(() => runtimeEvidence(events.value));
const diff = computed(() => extractDiff(events.value, run.value));
const queueTime = computed(() => formatDuration(elapsed(run.value?.created_at, run.value?.started_at)));
const runtimeTime = computed(() => formatDuration(elapsed(run.value?.started_at,
  run.value?.finished_at ?? (run.value?.status === "running" ? new Date(clock.value).toISOString() : run.value?.updated_at))));
const cancelRun = () => controller.cancel();
const decide = (action: "approve" | "reject" | "edit", calls?: Array<Record<string, unknown>>) => controller.decide(action, calls);
function inspectChildren() { if (run.value && permissions.value?.delegate && openChildReview) openChildReview(run.value.run_id); }
function inspectReport() { if (run.value && openRunReport) openRunReport(run.value.run_id); }
async function copyRunId() {
  try { if (run.value) { await navigator.clipboard.writeText(run.value.run_id); copied.value = true; } }
  catch (error) { controller.report(error); }
}
watch(() => [props.runId, props.conversationId] as const, ([rid, cid]) => { copied.value = false; void controller.select(rid, cid); }, { immediate: true });
let timer: number | undefined;
onMounted(() => { timer = window.setInterval(() => { clock.value = Date.now(); void controller.refresh(); }, 900); });
onBeforeUnmount(() => { controller.dispose(); window.clearInterval(timer); });
</script>
<template>
  <section class="run-inspector" aria-label="统一运行详情">
    <p v-if="state.loading" role="status">加载运行…</p>
    <div v-if="state.error" class="global-alert" role="alert"><span>{{ state.error }}</span><button type="button" @click="controller.refresh()">重试</button></div>
        <template v-if="run">
          <section class="run-hero" aria-labelledby="run-title">
            <div><p class="eyebrow">ACTIVE RUN</p><h1 id="run-title">{{ run.request.prompt }}</h1></div>
            <StatusBadge :status="run.status" />
            <div class="run-id"><code>{{ run.run_id }}</code><button class="icon-button" type="button" :aria-label="copied ? '已复制运行 ID' : '复制运行 ID'" @click="copyRunId"><Copy :size="16" aria-hidden="true" /></button></div>
          </section>

          <p v-if="run.metadata.fallback_runtime" class="field-error" role="status">请求模式 {{ run.mode }}；实际已回退到 {{ run.metadata.fallback_runtime }}。这不算原生 {{ run.mode }} 成功。</p>

          <section class="metric-strip" aria-label="运行指标">
            <div><Clock3 :size="17" aria-hidden="true" /><span>排队</span><strong>{{ queueTime }}</strong></div>
            <div><Gauge :size="17" aria-hidden="true" /><span>运行墙钟</span><strong>{{ runtimeTime }}</strong></div>
            <div><Activity :size="17" aria-hidden="true" /><span>事件</span><strong>{{ events.length }}</strong></div>
            <div><TerminalSquare :size="17" aria-hidden="true" /><span>模式</span><strong>{{ run.mode }}</strong></div>
          </section>

          <ContextInputReceipt :snapshot="run.input_snapshot" />
          <ApprovalPanel v-if="run.status === 'interrupted' && interrupt" :interrupt="interrupt" :busy="busy" @decide="decide" />

          <section class="output-card" aria-labelledby="answer-title">
            <div class="section-heading"><div><p class="eyebrow">MODEL OUTPUT</p><h2 id="answer-title">运行结果</h2></div><button v-if="['queued', 'running', 'interrupted'].includes(run.status)" type="button" class="danger-button compact" :disabled="busy" @click="cancelRun"><OctagonX :size="16" aria-hidden="true" />取消</button></div>
            <pre v-if="run.answer" class="answer">{{ run.answer }}</pre>
            <p v-else-if="run.error" class="field-error">{{ run.error }}</p>
            <p v-else-if="terminalStatuses.has(run.status)" class="helper">此运行已结束，没有助手回复；请查看状态和审计事件。</p>
            <div v-else class="skeleton" aria-label="等待运行输出"><span /><span /><span /></div>
          </section>

          <section class="diff-card" aria-labelledby="diff-title">
            <div class="section-heading"><div><p class="eyebrow">EXACT PATCH</p><h2 id="diff-title">统一差异</h2></div><FileDiff :size="19" aria-hidden="true" /></div>
            <pre v-if="diff" class="diff-output">{{ diff }}</pre><div v-else class="empty-inline">尚未提出补丁；不会伪造差异或验证结果。</div>
          </section>

          <section class="output-card" aria-labelledby="verification-title"><p class="eyebrow">MEASURED / NOT INFERRED</p><h2 id="verification-title">工具、用量与验证</h2>
            <p>已记录工具启动 {{ evidence.tools }} 次；{{ evidence.usageReports ? `模型完成回调 Token ${evidence.input} 输入 / ${evidence.output} 输出（${evidence.usageReports} 条有效回调）` : '没有有效的模型完成回调用量，不能视为零费用。' }}</p>
            <p class="helper">墙钟包含暂停审批时间；工具次数和 Token 不代表质量。未报告费用时不推算收费。</p>
            <p class="helper">此平铺指标仅统计模型完成回调，不累加 transport / provider 层的同源回执，也不是去重后的 provider 主计量或完整账单。查看主计量需明确打开白名单报告；这里不自动读库。不合并已归属子任务的嵌套用量；旧无归属事件可能无法拆分，不能视为整批工具或费用总计。</p>
            <pre v-if="evidence.verifications.length" class="answer">{{ JSON.stringify(evidence.verifications, null, 2) }}</pre>
            <p v-else class="helper">尚无配置验证的实际结果；完成回答 / 应用补丁不等于测试通过。</p>
          </section>
          <section class="output-card"><h2>白名单报告</h2><p class="helper">报告由主工作台持有，打开入口不读库。有效记录小计不是完整账单，费用未知；原始运行详情不会被改写为脱敏报告。</p>
            <button type="button" :disabled="!openRunReport" @click="inspectReport">明确打开此原生任务的脱敏报告</button></section>
          <section class="output-card"><h2>父子任务</h2><p class="helper">子任务面板由主工作台持有；切换或卸载本详情不会忘记原请求。只读 Graph，不自动读取目录或追问。</p>
            <button type="button" :disabled="!openChildReview || !permissions?.delegate" @click="inspectChildren">明确打开此父任务的子任务审查</button>
            <p v-if="!permissions?.delegate" class="helper">原父任务没有委派权限，不能从此入口授予新权限。</p></section>
        </template>
        <section v-else class="welcome-card">
          <div class="radar" aria-hidden="true"><span /><span /><span /></div>
          <p class="eyebrow">LOCAL-FIRST / AUDITABLE / DURABLE</p>
          <h1>让 Agent 的每一步都可见。</h1>
          <p>选择 Legacy、LangGraph 或 DeepAgent 运行时。这里会把状态迁移、Skill、MCP、子代理、审批和真实补丁放在同一条可审计时间线上。</p>
          <ul><li>SQLite 持久事件 + SSE 增量回放</li><li>中断后批准、拒绝或编辑工具调用</li><li>有界异步子代理与精确运行耗时</li></ul>
        </section>

    <EventTimeline :events="events" :connected="connected" />
    <p class="sr-only" aria-live="polite">{{ copied ? '运行 ID 已复制' : '' }}</p>
  </section>
</template>
