<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import PlanPanel from "./PlanPanel.vue";
import TaskQueue from "./TaskQueue.vue";
import RunInspector from "./RunInspector.vue";
import { WorkOrderController, orderStatus, type ExecutionSettings } from "../workOrders";
import type { ModelProfile } from "../workspaceTypes";
import type { PrepareContext } from "../types";
const props = defineProps<{ active: boolean; profiles: ModelProfile[]; prepareContext?: PrepareContext }>();
const emit = defineEmits<{ "selected-order": [id: string | null]; "open-changes": [runId: string] }>();
const controller = new WorkOrderController(undefined, scope => {
  if (!props.prepareContext) throw new Error("上下文面板未就绪；没有激活或重绑。");
  return props.prepareContext(scope);
}), state = controller.state;
const historyRevision = ref(1);
const disabled = computed(() => state.busy || state.loading || state.closing || state.restoring || !!state.restoreError);
const actionDisabled = computed(() => disabled.value || controller.hasEdits || state.uncertain);
watch(() => state.selected?.work_order_id, () => {
  historyRevision.value = state.selected?.active_revision || 1; emit("selected-order", state.selected?.work_order_id || null);
}, { immediate: true });
async function refresh() { await controller.list(); await controller.refresh(); }
async function activate(settings: ExecutionSettings, include: boolean) { await controller.activate(settings, include); }
async function prepareClose() { await controller.prepareClose(); }
function finishClose() { controller.finishClose(); }
defineExpose({ prepareClose, finishClose });
function beforeUnload(event: BeforeUnloadEvent) { if (controller.hasEdits || state.uncertain || state.busy || state.restoring || state.restoreError || state.selectionPending || state.loading) { event.preventDefault(); event.returnValue = ""; } }
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  void controller.initialize();
  timer = setInterval(() => { if (props.active) void controller.refresh(); }, 1000);
  window.addEventListener("beforeunload", beforeUnload);
});
watch(() => props.active, active => { if (active) void controller.refresh(); });
onBeforeUnmount(() => { controller.dispose(); if (timer) clearInterval(timer); window.removeEventListener("beforeunload", beforeUnload); });
</script>
<template>
  <div class="orders-workspace" :class="{ inspecting: !!state.runId }">
    <aside class="orders-sidebar" aria-label="工作单列表">
      <div class="section-heading"><h2>工作单</h2><button type="button" :disabled="disabled || controller.hasEdits || state.uncertain" @click="controller.fresh()">新建</button></div>
      <form class="order-search" @submit.prevent="controller.list()"><label><span class="field-label">搜索全部工作单的标题或 ID</span><input v-model="state.query" maxlength="200" :disabled="disabled || state.listLoading" /></label><button type="submit" :disabled="disabled || state.listLoading">搜索</button></form>
      <button type="button" :disabled="disabled || state.listLoading" @click="refresh">刷新列表与队列</button>
      <details class="page-explanation"><summary aria-label="工作单列表说明" title="工作单列表说明">说明</summary><p class="helper">列表为最近一次请求的快照；不自动批准草稿或重试失败节点。</p></details>
      <ul class="orders-list"><li v-for="order in state.orders" :key="order.work_order_id"><button type="button" :disabled="disabled || controller.hasEdits || state.uncertain" :aria-pressed="state.selected?.work_order_id === order.work_order_id" @click="controller.select(order.work_order_id)"><strong>{{ order.title }}</strong><small>r{{ order.active_revision }} · {{ orderStatus[order.status] || order.status }}</small></button></li></ul>
      <button v-if="state.more" type="button" :disabled="disabled || state.listLoading" @click="controller.list(true)">加载更早工作单</button>
      <p v-if="state.capped" class="helper">当前列表最多显示 250 项；使用服务端搜索查找更早工作单，不删除历史。</p>
      <p v-if="!state.orders.length" class="helper">尚无已载入工作单；服务不可达不代表历史为空。</p>
    </aside>
    <main id="orders-main" class="orders-main" tabindex="-1" aria-label="计划与队列">
      <p v-if="state.loading" role="status">正在载入持久工作单…</p>
      <p v-if="state.restoring" role="status">正在恢复项目库中的工作单与关联运行选择…</p>
      <div v-if="state.restoreError" class="order-warning" role="alert">{{ state.restoreError }}<button type="button" :disabled="state.restoring || state.closing" @click="controller.initialize()">重试恢复，不覆盖未知记录</button></div>
      <p v-if="state.selectionPending && !state.selectionError" role="status">工作单选择尚在保存；关闭/切换项目会等待确认。</p>
      <div v-if="state.selectionError" class="order-warning" role="alert">选择未确认保存：{{ state.selectionError }}<button type="button" :disabled="disabled" @click="controller.retrySelection()">重试选择保存</button></div>
      <div v-if="state.error" class="order-warning" role="alert">{{ state.error }}</div>
      <p v-if="state.dirty && state.selected && state.baseRevision !== state.selected.active_revision" class="field-error" role="alert">编辑基于 r{{ state.baseRevision }}，服务端现为 r{{ state.selected.active_revision }}；内容仍保留，不自动覆盖或合并。</p>
      <PlanPanel :plan="state.draft" :order="state.selected" :dirty="state.dirty" :rebind-context="state.rebindContext" :disabled="disabled" :uncertain="state.uncertain" :uncertain-operation="state.uncertainOperation" :conflict="state.conflict" :profiles="profiles" @update="controller.update($event)" @save="controller.save()" @discard="controller.discard()" @rebind="controller.setContextRebind($event)" @activate="activate" @retry="controller.retryPending()" @reconcile="controller.reloadAfterUncertain()" @adopt="controller.adoptServerVersion(true)" />
      <details v-if="state.selected" class="plan-history"><summary>历史计划定义（只读，不是旧时点状态回放）</summary><div class="button-row"><label>revision <input v-model.number="historyRevision" type="number" min="1" :max="state.selected.active_revision" /></label><button type="button" :disabled="disabled || !Number.isInteger(historyRevision) || historyRevision < 1 || historyRevision > state.selected.active_revision" @click="controller.history(historyRevision)">读取历史版本</button></div><template v-if="state.history"><h3>r{{ state.history.revision }} · {{ state.history.plan.title }}</h3><ol><li v-for="task in state.history.plan.tasks" :key="task.id"><strong>{{ task.id }} · {{ task.title }}</strong><p class="helper">{{ task.mode }} / {{ task.access }} · 依赖 {{ task.dependencies.join('、') || '无' }}</p><pre class="historical-prompt">{{ task.prompt.slice(0, 20000) }}</pre><p v-if="task.prompt.length > 20000" class="helper">此处仅显示前 20000 字符；原版本仍完整保存在服务端。</p></li></ol></template></details>
      <TaskQueue :order="state.selected" :queue="state.queue" :queue-error="state.queueError" :disabled="actionDisabled" :view-disabled="disabled" @control="controller.control($event)" @retry="controller.retry($event)" @open-run="controller.openRun($event)" />
      <button v-if="state.runId" type="button" :disabled="disabled" @click="emit('open-changes', state.runId)">查看选中原生 run 的变更证据（不改变工作单关联）</button>
    </main>
    <aside v-if="state.runId" class="orders-inspector" aria-label="真实运行详情"><div class="section-heading"><h2>运行与审批</h2><button type="button" :disabled="disabled" @click="controller.hideRun()">{{ state.transientRun && state.rememberedRunId ? '返回关联运行' : '收起运行详情' }}</button></div><p v-if="state.transientRun" class="helper">临时查看其他运行，不改变工作单关联选择；关闭后不恢复此临时视图。</p><p class="helper">使用原 RunInspector；不是计划状态推断出的补丁或用量。</p><RunInspector :run-id="state.runId" /></aside>
  </div>
</template>
