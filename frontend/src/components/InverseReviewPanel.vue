<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { InverseReviewController } from "../inverseReview";
const props = defineProps<{ controller: InverseReviewController; blocked: boolean; preimageRetained: boolean }>();
const state = props.controller.state;
const prepareConsent = ref(false), prepareWrite = ref(false), decisionConsent = ref(false), decisionWrite = ref(false);
const storedConsent = ref(false), retryConsent = ref(false), reconcileConsent = ref(false), exitConsent = ref(false), discardConsent = ref(false);
const existingReviewId = ref("");
const unavailable = computed(() => props.blocked || !state.active || state.closing || state.busy || state.reading);
const pending = computed(() => state.review?.status === "pending" && state.review.lifecycle?.pending_expired !== true && !state.uncertain);
const metadataOnly = computed(() => state.review && ["applying", "indeterminate"].includes(state.review.status));
const unknown = computed(() => state.uncertain || !!metadataOnly.value);
function clearConsent() { prepareConsent.value = prepareWrite.value = decisionConsent.value = decisionWrite.value = false;
  storedConsent.value = retryConsent.value = reconcileConsent.value = exitConsent.value = discardConsent.value = false; }
watch(() => [state.review, state.intent, state.reading, state.armed, state.active, state.closing, props.blocked], clearConsent, { flush: "sync" });
async function prepare() {
  if (unavailable.value || !props.preimageRetained) return;
  const consent = prepareConsent.value, write = prepareWrite.value; clearConsent(); await props.controller.prepare(consent, write);
}
async function decide(action: "approve" | "reject") {
  const view = state.review; if (unavailable.value || !view) return;
  const consent = decisionConsent.value, write = decisionWrite.value; clearConsent();
  await props.controller.decide(action, view.review_id, view.patch_id, consent, write);
}
</script>
<template>
  <section class="inverse-review-panel" aria-labelledby="inverse-title" :inert="blocked || undefined">
    <header class="section-heading"><h2 id="inverse-title">精确逆向补丁</h2><span class="helper">手动操作，不是 Git reset／通用 undo</span></header>
    <p class="helper">仅从已封存 applied 回执生成逆向预览，重新检查当前文件是否仍匹配该补丁 after hash／mode；后续用户修改须保留，冲突不自动覆盖。预览不应用补丁；准备与批准分别确认新的写入授权，旧运行写入权限不继承。</p>
    <p v-if="!state.source" class="helper">先选择已确认 applied 的来源补丁证据；没有来源不生成伪造预览。</p>
    <p v-else class="helper">来源 run：<code>{{ state.source.run_id }}</code> · tool call：{{ state.source.tool_call_id }} · 原 patch：<code>{{ state.source.patch_id }}</code></p>
    <p v-if="state.busy" role="status">原始操作仍在进行；不设置效果超时或自动重发，关闭／切换须等原回执。</p>
    <p v-if="state.reading" role="status">正在只读查询持久 review；读取不是批准、对账或未执行证明。</p>
    <p v-if="state.error" class="order-warning" role="alert">{{ state.error }}</p>
    <div v-if="state.source && !state.review && !state.intent" class="inverse-prepare">
      <p v-if="!preimageRetained" class="order-warning">来源 preimage 已过期或未观测，不能据历史 diff 重建原内容或准备新逆向；仍可只读查已存 review。</p>
      <label class="context-confirm"><input v-model="prepareConsent" type="checkbox" :disabled="unavailable || !preimageRetained" />我明确请求读取该来源 preimage、检查当前文件并创建新的持久预览；这不是应用批准。</label>
      <label class="context-confirm"><input v-model="prepareWrite" type="checkbox" :disabled="unavailable || !preimageRetained" />为准备操作提供本次新的写入授权（服务端仍不应用补丁）；不借用历史权限。</label>
      <button type="button" :disabled="unavailable || !preimageRetained || !prepareConsent || !prepareWrite" @click="prepare">准备新的精确逆向预览</button>
    </div>
    <form v-if="state.source && !state.intent" class="inverse-recover-existing" @submit.prevent="controller.readExisting(existingReviewId.trim())">
      <label>已有 review ID（只读，来源必须精确相同）<input v-model="existingReviewId" maxlength="32" autocomplete="off" spellcheck="false" :disabled="unavailable || controller.selectionLocked" /></label>
      <button type="submit" :disabled="unavailable || controller.selectionLocked || !/^[0-9a-f]{32}$/.test(existingReviewId.trim())">只读已有 review，不创建新预览</button>
    </form>
    <div v-if="state.intent" class="inverse-intent">
      <h3>保留的原请求（不是已执行证明）</h3><p>{{ state.intent.kind }} · run {{ state.intent.source.run_id }} · review／operation {{ state.intent.review_id }}<span v-if="state.intent.patch_id"> · inverse patch {{ state.intent.patch_id }}</span></p>
      <details><summary>精确原 payload（仅内存，不随复选框变化）</summary><pre>{{ JSON.stringify(state.intent.body, null, 2) }}</pre></details>
      <p v-if="state.uncertain" class="order-warning">回复未知，不新建 ID、不替换来源、不重发批准／拒绝。GET pending／404 不能证明原请求未进入。</p>
      <template v-if="state.uncertain && state.intent.kind === 'prepare'"><label class="context-confirm"><input v-model="retryConsent" type="checkbox" :disabled="unavailable" />我确认仅重发原准备请求：相同来源、ID 与 payload，不更新 base／TTL，不应用补丁。</label><button type="button" :disabled="unavailable || !retryConsent" @click="controller.retryPrepare(retryConsent)">显式重发原准备请求</button></template>
    </div>
    <div v-if="state.review" class="inverse-preview">
      <h3>持久精确预览：{{ state.review.review_id }}</h3>
      <dl class="changes-metrics"><div><dt>inverse patch ID</dt><dd><code>{{ state.review.patch_id }}</code></dd></div><div><dt>状态／有效期</dt><dd>{{ state.review.status }} / {{ state.review.lifecycle?.effective_status ?? state.review.status }} · {{ state.review.expires_at }}</dd></div><div><dt>实际 effect tool call</dt><dd>{{ state.review.effect_tool_call_id }}</dd></div><div><dt>证据级别</dt><dd>{{ state.review.effect_evidence }} · {{ state.review.error_code ?? '未报告错误代码' }}</dd></div></dl>
      <p class="order-warning">敏感完整逆向预览，未做 S8 全局脱敏；diff 可能包含旧原文。下方不截断审批预览，不写浏览器存储，不将 hash／已读状态当作新的权限。服务器批准前再次检查 TTL、来源与当前文件。</p>
      <ul><li v-for="file in state.review.review.files" :key="file.path"><strong>{{ file.path }} · {{ file.action }}</strong><small>当前 base：{{ file.base_hash }} / mode {{ file.base_mode ?? 'missing' }}</small><small>目标：{{ file.target_hash }} / mode {{ file.target_mode ?? 'missing' }}</small></li></ul>
      <pre tabindex="0" aria-label="完整精确逆向预览">{{ state.review.review.unified_diff }}</pre>
      <template v-if="pending">
        <div v-if="!state.armed"><p class="helper">只读恢复不继承批准；若要决定，先显式确认这份存储预览，再重新勾选新的权限。未重新捕获 config／当前文件，最终服务端复查可能拒绝。</p><label class="context-confirm"><input v-model="storedConsent" type="checkbox" :disabled="unavailable" />我确认精确预览的来源、review ID、inverse patch ID、完整 diff／路径／hash／mode；不是旧写入权限。</label><button type="button" :disabled="unavailable || !storedConsent" @click="controller.armStored(storedConsent)">为这份存储预览开始一次新的确认</button></div>
        <label class="context-confirm"><input v-model="decisionConsent" type="checkbox" :disabled="unavailable" />我确认仅决定上述精确 review／inverse patch，不改变文件集合或自动合并冲突。</label>
        <label class="context-confirm"><input v-model="decisionWrite" type="checkbox" :disabled="unavailable || !state.armed" />为本次应用重新提供新的写入授权；不使用准备操作或原运行的授权。</label>
        <div class="button-row"><button type="button" class="danger-button" :disabled="unavailable || !state.armed || !decisionConsent || !decisionWrite" @click="decide('approve')">批准并应用这份精确逆向</button><button type="button" :disabled="unavailable || !decisionConsent" @click="decide('reject')">拒绝该预览（不修改工作区）</button></div>
      </template>
      <p v-if="state.review.status === 'applied'" class="helper">已封存逆向 applied 回执（不是当前 Git clean／项目验证证明）：{{ state.review.result?.receipt_source.durability }}</p>
      <p v-if="state.review.status === 'failed'" class="order-warning">操作失败不证明工作区没有部分效果，也不允许重跑该已消耗 review；需单独查看实际账本／当前文件。</p>
      <p v-if="state.review.status === 'expired' || state.review.lifecycle?.pending_expired" class="order-warning">预览已过期；读取不更新 TTL，不能批准。新预览必须独立确认并重新检查，不复活原 review。</p>
      <template v-if="metadataOnly"><label class="context-confirm"><input v-model="reconcileConsent" type="checkbox" :disabled="unavailable" />我仅请求将已封存账本证据对账到当前未知 review，不读写项目文件或重新执行补丁。</label><button type="button" :disabled="unavailable || !reconcileConsent" @click="controller.reconcile(reconcileConsent)">显式元数据对账（不是补丁重试）</button></template>
      <template v-if="!controller.selectionLocked"><label class="context-confirm"><input v-model="discardConsent" type="checkbox" :disabled="unavailable" />仅收起当前 UI 预览选择，服务器记录保留；不删除、拒绝、撤销或推断效果不存在。</label><button type="button" :disabled="unavailable || !discardConsent" @click="controller.discardPreview(discardConsent)">收起预览，保留服务器记录</button></template>
    </div>
    <button v-if="state.intent || state.review" type="button" :disabled="unavailable" @click="controller.recover()">只读恢复原 ID（不批准／不重跑）</button>
    <div v-if="unknown" class="inverse-exit-warning"><p class="order-warning">未知仍保留，原始 IO 不能由前端宣称取消。关闭时等原 promise／host owner 清理。UI 内存不跨重启保存；请记下 run、tool call、来源 patch 与 review ID，重开只读查询；即使 404 也不是没有执行的证明。</p><label class="context-confirm"><input v-model="exitConsent" type="checkbox" :disabled="unavailable" />我已记下原 ID，知悉工作区可能已改变；允许本次退出／切换后只读恢复，不再批准或重跑未知操作。</label><button type="button" :disabled="unavailable || !exitConsent" @click="controller.acknowledgeExit(exitConsent)">仅确认允许退出，未知与原请求仍保留</button><p v-if="state.exitAcknowledged" class="helper">仅解除退出阻塞；没有清除未知、自动取消、开放新效果或安全擦除。</p></div>
  </section>
</template>
