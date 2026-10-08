<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from "vue";
import type { ChildAction, ChildReviewController } from "../childReview";
import StatusBadge from "./StatusBadge.vue";
const props = defineProps<{ controller: ChildReviewController; active: boolean; blocked: boolean }>();
const c = props.controller, s = c.state;
const disabled = computed(() => !props.active || props.blocked || c.selectionLocked);
const localDisabled = computed(() => !props.active || props.blocked || s.closing || s.busy);
const selected = computed(() => c.selectedChild), shown = computed(() => s.page ?? selected.value);
const spawnDraft = computed({ get: () => s.spawnDraft, set: value => c.setDraft("spawn", value) });
const followDraft = computed({ get: () => s.followDraft, set: value => c.setDraft("follow_up", value) });
const action = computed({ get: () => s.action, set: (value: ChildAction) => c.chooseAction(value) });
const child = computed({ get: () => s.selectedChildId, set: value => c.selectChild(value) });
const consent = computed({ get: () => s.confirmed, set: value => c.confirmReview(value) });
const exitConsent = computed({ get: () => s.exitAcknowledged, set: value => c.acknowledgeExit(value) });
const draftExit = computed({ get: () => s.draftExitAcknowledged, set: value => c.acknowledgeDraftExit(value) });
const label = (value: ChildAction) => value === "spawn" ? "新建只读子任务" : value === "follow_up" ? "追问已完成子任务" : "请求取消原代子任务";
watch(() => [props.active, props.blocked] as const, () => c.activate(props.active && !props.blocked), { immediate: true, flush: "sync" });
onBeforeUnmount(() => c.activate(false)); // App owns original controller/promise; no panel dispose
</script>
<template>
  <section class="child-review" aria-labelledby="child-review-heading" :inert="blocked || undefined">
    <header><p class="eyebrow">READ-ONLY CHILD WORKSPACE</p><h2 id="child-review-heading">父子任务审查</h2>
      <p>原父任务 <code>{{ s.parentRunId || '尚未选择' }}</code>。从运行详情明确打开来源；进入、隐藏或重新打开不自动请求。</p></header>
    <p v-if="s.sourceNotice" role="status">{{ s.sourceNotice }}</p><p v-if="s.error" role="alert" class="field-error">{{ s.error }}</p>
    <p class="helper">子任务固定 Graph，只读项目；不能写入、执行命令、执行 MCP 或再委派。所有输出敏感且不可信，尚未全局脱敏。</p>
    <button type="button" :disabled="disabled || !s.parentRunId" @click="c.readSnapshot()">明确读取该父任务目录</button>
    <template v-if="s.snapshot">
      <p>累计记录 {{ s.snapshot.counts.lifetime_for_parent }} / {{ s.snapshot.limits.lifetime_per_parent }}；已完成、失败和取消仍占累计额度。</p>
      <p>该父任务持久 active {{ s.snapshot.counts.durable_active_for_parent }}；全局内存 active {{ s.snapshot.scheduler_observation.active }} / {{ s.snapshot.limits.global_active }}，queued {{ s.snapshot.scheduler_observation.queued }} / {{ s.snapshot.limits.global_queue }}。</p>
      <p class="helper">这是读取时快照，不证明物理排空。Profile 来源：{{ s.snapshot.profile_inheritance === 'parent_snapshot' ? '父任务冻结快照' : '旧父任务 profile 解析 fallback，非冻结保证' }}。</p>
      <p v-if="s.snapshot.service.execution_admission === 'quarantined'" role="alert">原执行准入已隔离；不能新建或追问，也不重试未知调用。仅在原服务未关闭失败时，仍可明确审查并请求取消原 generation；取消回执不证明资源排空。</p>
      <p v-if="s.snapshot.truncated" class="helper">显示 {{ s.snapshot.items.length }} / {{ s.snapshot.total }} 条历史记录；未显示记录不会被当成不存在。</p>
      <label>原子任务<select v-model="child" :disabled="disabled" aria-label="原子任务"><option value="" disabled>明确选择子任务</option>
        <option v-for="item in s.snapshot.items" :key="item.subagent_id" :value="item.subagent_id">{{ item.subagent_id }} · generation {{ item.generation }} · {{ item.status }}</option></select></label>
    </template>
    <article v-if="selected" class="child-record"><h3><code>{{ selected.subagent_id }}</code></h3><StatusBadge :status="selected.status" />
      <p>原 parent <code>{{ selected.parent_run_id }}</code> · generation {{ selected.generation }}。子任务不是独立注册的 parent run。</p>
      <h4>当前任务／记录回答</h4><pre>{{ selected.prompt }}</pre><pre v-if="selected.answer">{{ selected.answer }}</pre>
      <p v-if="selected.error_present">记录了执行错误；不返回原私有异常正文，状态不是质量或排空证明。</p>
      <template v-if="shown"><h4>完整历史的分页视图</h4><p>总回合 {{ shown.history_total }}，本页 offset {{ shown.history_offset }}，显示 {{ shown.history.length }}，limit {{ shown.history_limit }}；{{ shown.history_truncated ? '还有其他页' : '本页包含全部历史' }}。</p>
        <div class="child-buttons"><button type="button" :disabled="disabled || !shown.history_total" @click="c.readHistory(0)">读取最早一页</button>
          <button type="button" :disabled="disabled || shown.history_offset === 0" @click="c.readHistory(Math.max(0, shown.history_offset - 16))">读取前一页</button>
          <button type="button" :disabled="disabled || shown.history_offset + shown.history.length >= shown.history_total" @click="c.readHistory(shown.history_offset + shown.history.length)">读取后一页</button></div>
        <article v-for="(turn, i) in shown.history" :key="`${shown.generation}:${shown.history_offset + i}`"><p>历史回合 {{ shown.history_offset + i + 1 }}（当前记录 generation {{ shown.generation }}）</p><pre>{{ turn.prompt }}</pre><pre>{{ turn.answer }}</pre></article>
      </template>
    </article>
    <fieldset :disabled="disabled"><legend>新的明确动作，不自动追问或重发</legend>
      <label>子任务动作<select v-model="action" aria-label="子任务动作"><option value="spawn">新建只读子任务</option><option value="follow_up">追问已完成子任务</option><option value="cancel">请求取消原代子任务</option></select></label>
      <label v-if="s.action === 'spawn'">子任务原草稿<textarea v-model="spawnDraft" maxlength="8000" rows="3" /></label>
      <template v-if="s.action === 'follow_up'"><label>追问原草稿<textarea v-model="followDraft" :disabled="!selected" maxlength="8000" rows="3" /></label>
        <p>草稿原 generation {{ s.draftGeneration ?? '未固定' }}；当前所读 generation {{ selected?.generation ?? '未读取' }}。新代不会自动继承草稿确认。</p>
        <button v-if="selected && s.draftGeneration !== selected.generation" type="button" @click="c.adoptChildGeneration()">明确将保留草稿绑定到所读 generation</button></template>
      <button type="button" @click="c.reviewAction()">准备并展示本次确认请求</button>
      <article v-if="s.review" class="child-intent"><h3>{{ label(s.review.action) }}</h3>
        <p>parent <code>{{ s.review.parent_run_id }}</code> · child <code>{{ s.review.subagent_id || '服务端新建后回执给出 ID' }}</code> · generation {{ s.review.expected_generation ?? '初始新建' }}</p>
        <pre v-if="s.review.prompt !== null">{{ s.review.prompt }}</pre><p class="helper">上方为去除原 manager 边界空白后的实际提交文本；原草稿保留不清空。确认不是新增权限或幂等 token。</p>
        <label><input v-model="consent" type="checkbox" />我确认该父任务、子任务、原 generation 与本次动作</label>
        <button type="button" :disabled="!s.confirmed" @click="c.dispatch()">执行本次确认动作</button>
      </article>
    </fieldset>
    <article v-if="s.intent" class="child-intent" role="status"><h3>固定的原请求：{{ label(s.intent.action) }}</h3>
      <p>parent <code>{{ s.intent.parent_run_id }}</code> · child <code>{{ s.intent.subagent_id || '新建 ID 回复尚未知' }}</code> · generation {{ s.intent.expected_generation ?? '初始新建' }}</p><pre v-if="s.intent.prompt !== null">{{ s.intent.prompt }}</pre>
      <p>{{ s.busy ? '仍等待同一个原请求，不取消等待、不创建替代请求。' : s.uncertain ? '回复未知：不能靠最新列表或相同 prompt 猜测结果，不自动重发。' : '已收到原回执，等待明确本地展示。' }}</p>
    </article>
    <button v-if="s.deferredAcknowledgement" type="button" :disabled="localDisabled || s.uncertain" @click="c.showOriginalReceipt()">明确展示迟到的原回执（不请求服务器）</button>
    <article v-if="s.lastReceipt" class="child-intent"><h3>原动作回执，不是当前状态</h3><p>{{ label(s.lastReceipt.intent.action) }} · parent {{ s.lastReceipt.intent.parent_run_id }} · reviewed generation {{ s.lastReceipt.intent.expected_generation ?? '初始新建' }}</p>
      <pre>{{ JSON.stringify(s.lastReceipt.receipt, null, 2) }}</pre><p>只确认 admission 或 cancel 请求；不证明完成、质量、无副作用或物理排空。继续动作须明确重新读取目录，草稿仍保留。</p></article>
    <label v-if="s.uncertain"><input v-model="exitConsent" type="checkbox" :disabled="localDisabled" />我知悉原请求仍未知，只允许退出，不解锁来源或再次执行</label>
    <label v-if="c.hasDrafts"><input v-model="draftExit" type="checkbox" :disabled="localDisabled" />我知悉本页全部子任务草稿仅存内存，关闭或切项目会丢失；不删除草稿或重发请求</label>
  </section>
</template>
<style scoped>
.child-review { margin: 16px; padding: 20px; border: 1px solid var(--border-soft); border-radius: 12px; background: var(--surface); min-width: 0; }
.child-review label { display: grid; gap: 8px; margin: 12px 0; }.child-review label:has(input[type="checkbox"]) { display: flex; align-items: center; min-height: 44px; }
.child-review button, .child-review select { min-height: 44px; }.child-review textarea, .child-review select { width: 100%; box-sizing: border-box; }
.child-review pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; }.child-review code { overflow-wrap: anywhere; }
.child-review fieldset { min-width: 0; margin-top: 16px; border: 1px solid var(--border-soft); border-radius: 8px; }
.child-record, .child-intent { margin-top: 16px; padding: 12px; border: 1px solid var(--border-soft); border-radius: 8px; }.child-buttons { display: flex; flex-wrap: wrap; gap: 8px; }
@media (max-width: 700px) { .child-review { margin: 8px; padding: 12px; }.child-buttons button { flex: 1 1 160px; } }
</style>
