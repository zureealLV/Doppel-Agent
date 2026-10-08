<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import ChangesSnapshot from "./ChangesSnapshot.vue";
import InverseReviewPanel from "./InverseReviewPanel.vue";
import VerificationReviewPanel from "./VerificationReviewPanel.vue";
import { ChangesController } from "../changes";
import { InverseReviewController, inverseSourceFromEvidence } from "../inverseReview";
import { VerificationReviewController } from "../verificationReview";
import { PATCH_PAGE_SIZE } from "../changesApi";
import type { ChangesDesktopApi, ConflictStage, DiffPlane } from "../changesTypes";
const props = defineProps<{ active: boolean; blocked: boolean; sourceRunId: string }>();
const controller = new ChangesController(), state = controller.state;
const inverse = new InverseReviewController();
const verification = new VerificationReviewController();
const sourceInput = ref("");
const disabled = computed(() => props.blocked || controller.busy || inverse.selectionLocked || verification.selectionLocked || !props.active);
function ready() {
  controller.attach((window as Window & { pywebview?: { api?: Partial<ChangesDesktopApi> } }).pywebview?.api);
  // Initial attachment must not start a native flight before activate's ordered
  // Git -> scoped patches -> authorization chain. A late bridge-ready event while
  // reading likewise leaves the original activation continuation in charge.
  if (props.active && !props.blocked && state.active && !controller.busy && !inverse.selectionLocked && !verification.selectionLocked) void controller.refreshAuthorization();
}
async function readSource() { if (disabled.value) return; inverse.selectSource(null); verification.selectSource(null);
  controller.setSource(sourceInput.value.trim()); verification.selectRun(state.sourceRunId); await controller.loadPatches(); }
function selectDiff(path: string, plane: DiffPlane, stage: ConflictStage) { if (!disabled.value) void controller.selectDiff(path, plane, stage); }
watch(() => [props.sourceRunId, props.blocked] as const, ([value, blocked]) => {
  if (blocked || inverse.selectionLocked || verification.selectionLocked) {
    if (value !== state.sourceRunId) state.error = "项目切换/关闭准备或原逆向/验证仍在进行或未知；保留原来源，不替换为另一运行。";
    return;
  }
  sourceInput.value = value;
  if (value !== state.sourceRunId) { inverse.selectSource(null); verification.selectSource(null); controller.setSource(value); }
  verification.selectRun(state.sourceRunId);
  // The unblock activation chain owns its initial reads. Do not race it with a
  // source-watch read while inactive; an ordinary active source change is explicit.
  if (props.active && !blocked && state.active) void controller.loadPatches();
}, { immediate: true });
watch(() => state.evidence, evidence => { if (evidence) { const source = inverseSourceFromEvidence(evidence);
  inverse.selectSource(source); verification.selectSource(source); } }, { flush: "sync" });
watch(() => inverse.state.effectEpoch, () => controller.invalidateSnapshot(), { flush: "sync" });
watch(() => verification.state.effectEpoch, () => controller.invalidateSnapshot(), { flush: "sync" });
function activateWorkspace() {
  const active = props.active && !props.blocked;
  inverse.activate(active); verification.activate(active);
  void controller.activate(active, !inverse.selectionLocked && !verification.selectionLocked);
}
// Project/close freeze also fences read presentation/continuations and consumes
// old review consent. No abort/unmount is taken as backend/native drain proof.
watch(() => [props.active, props.blocked], activateWorkspace);
function beforeUnload(event: BeforeUnloadEvent) { if (controller.busy || inverse.selectionLocked && !inverse.state.exitAcknowledged
  || verification.selectionLocked && !verification.state.exitAcknowledged) { event.preventDefault(); event.returnValue = ""; } }
onMounted(() => { ready(); window.addEventListener("pywebviewready", ready);
  window.addEventListener("beforeunload", beforeUnload); activateWorkspace(); });
onBeforeUnmount(() => { window.removeEventListener("pywebviewready", ready); window.removeEventListener("beforeunload", beforeUnload);
  inverse.dispose(); verification.dispose(); controller.dispose(); });
let closeEpoch = 0;
async function prepareClose() {
  const epoch = ++closeEpoch;
  await inverse.prepareClose();
  if (epoch !== closeEpoch) throw new Error("changes preparation superseded; no late read barrier");
  await verification.prepareClose();
  if (epoch !== closeEpoch) throw new Error("changes preparation superseded; no late verification/read barrier");
  await controller.prepareClose();
  if (epoch !== closeEpoch) throw new Error("changes preparation superseded; no late host transition");
}
function finishClose() { closeEpoch++; inverse.finishClose(); verification.finishClose(); controller.finishClose(); }
function canChangeSource() { return !inverse.selectionLocked && !verification.selectionLocked && !props.blocked; }
defineExpose({ prepareClose, finishClose, canChangeSource });
</script>
<template>
  <main id="changes-main" class="changes-workspace" tabindex="-1" aria-labelledby="changes-title" :inert="blocked || state.closing || undefined">
    <header><h1 id="changes-title">变更与验证</h1><details class="page-explanation"><summary aria-label="变更与验证说明" title="变更与验证说明">说明</summary><p class="helper">当前项目 Git 快照与原生运行账本分开展示。历史完成补丁不证明当前文件仍相同；精确逆向与配置 argv 验证分别审批，不继承权限，不互相覆盖结果，也不冒充整版验收。</p></details></header>
    <p v-if="state.error" class="order-warning" role="alert">{{ state.error }}</p>
    <div class="changes-columns">
      <div><ChangesSnapshot :status="state.status" :diff="state.diff" :stale="state.stale" :disabled="disabled" :reading="state.reading" @refresh="controller.refresh()" @select="selectDiff" />
        <InverseReviewPanel :controller="inverse" :blocked="props.blocked || controller.busy || verification.selectionLocked || !props.active" :preimage-retained="state.evidence?.preimage_state === 'retained'" />
        <VerificationReviewPanel :controller="verification" :blocked="props.blocked || controller.busy || inverse.selectionLocked || !props.active" />
      </div>
      <aside class="changes-evidence" aria-labelledby="changes-evidence-title">
        <section class="changes-authority" aria-labelledby="changes-authority-title">
          <h2 id="changes-authority-title">外部 worktree 元数据授权</h2>
          <p class="helper">仅桌面原生桥可授权：系统目录选择后必须原生确认精确 workspace／metadata root。网页路径、复选框或 gitfile 不授予权限。</p>
          <p class="helper">仅当前 owner 的精确 linked worktree 只读检查；不落盘，不继承到切换／恢复后的项目，不授予任意命令或写入。授权失效后不能自动再确认。</p>
          <p v-if="!state.ready" role="status">当前没有完整原生桥；浏览器不模拟或绕过授权。</p>
          <p v-if="state.nativeBusy" role="status">原生操作仍在进行，等待原始回执；不会因等待而重复弹窗。</p>
          <p v-if="state.authorizationUnknown" class="order-warning" role="alert">当前授权结果未知／尚未重新读取；先读取原生授权，不重复批准或撤销。</p>
          <p v-if="state.nativeError" class="order-warning" role="alert">{{ state.nativeError }}</p>
          <div v-if="state.authorization" class="helper"><strong>最近一次原生读取：{{ state.authorization.active ? '存在临时授权（非新的执行批准）' : '没有临时授权' }}</strong><p>workspace：{{ state.authorization.workspace }}</p><p v-if="state.authorization.metadata_root">metadata root：{{ state.authorization.metadata_root }}</p><p v-if="state.authorization.grant_id">grant ID：{{ state.authorization.grant_id }}</p></div>
          <div class="button-row"><button type="button" :disabled="disabled || !state.ready" @click="controller.refreshAuthorization()">只读当前原生授权</button><button type="button" :disabled="disabled || !state.ready || state.authorizationUnknown" @click="controller.authorize()">原生选择并确认授权</button><button type="button" :disabled="disabled || !state.ready || state.authorizationUnknown || !state.authorization?.active" @click="controller.revoke()">撤销并等待已进入读取排空</button></div>
        </section>
        <section aria-labelledby="changes-evidence-title">
          <h2 id="changes-evidence-title">历史补丁证据</h2>
          <form class="changes-source" @submit.prevent="readSource"><label for="changes-source-run">来源 run ID（原生注册范围，不是 Git 作者）</label><input id="changes-source-run" v-model="sourceInput" maxlength="32" autocomplete="off" spellcheck="false" :disabled="disabled" /><button type="submit" :disabled="disabled">读取该来源补丁</button></form>
          <p class="helper">工作台／工作单／独立 Runtime 可显式带入实际选中的 run。Legacy 不转换为原生记录。没有已登记来源就不造补丁列表。显式更换来源会收起未批准的 UI 预览，服务器记录保留；进行中／未知逆向或验证不换来源。</p>
          <p v-if="state.evidenceError" class="order-warning" role="alert">{{ state.evidenceError }}</p>
          <p v-if="state.sourceRunId" class="helper">当前查询范围：<code>{{ state.sourceRunId }}</code> · page offset {{ state.patchOffset }}</p>
          <p v-if="state.patchesLoaded && !state.patches.length" class="helper">已注册来源在本页没有补丁回执；不等于没有项目变更。</p>
          <ul class="changes-patches"><li v-for="item in state.patches" :key="item.tool_call_id"><button type="button" :disabled="disabled" @click="controller.openEvidence(item.tool_call_id)"><strong>{{ item.tool_name }} · {{ item.receipt.patch_id }}</strong><small>{{ item.tool_call_id }} · {{ item.operation_status }} / receipt {{ item.receipt.status }}</small><small>{{ item.confirmed_applied ? '账本与回执确认历史 applied' : '未确认完整 applied；不是成功补丁' }} · preimage {{ item.preimage_state }}</small></button><p class="helper">来源 quiescent 提示：{{ item.source_run_quiescent }}；不是新批准，逆向仍须重新检查当前文件。</p></li></ul>
          <div v-if="state.patchesLoaded" class="button-row"><button type="button" :disabled="disabled || state.patchOffset === 0" @click="controller.loadPatches(Math.max(0, state.patchOffset - PATCH_PAGE_SIZE))">上一页</button><button type="button" :disabled="disabled || !state.patchesMore || state.patchOffset + PATCH_PAGE_SIZE > 100000" @click="controller.loadPatches(state.patchOffset + PATCH_PAGE_SIZE)">下一页（不推断总数）</button></div>
          <section v-if="state.evidence" class="changes-patch-detail" aria-label="选中来源历史证据">
            <h3>{{ state.evidence.source.tool_call_id }}</h3><p class="helper">来源 {{ state.evidence.source.run_id }} · {{ state.evidence.source_run.status }} · lease_active={{ state.evidence.source_run.lease_active }}</p>
            <p class="order-warning">敏感历史证据，尚非 S8 全局脱敏。不导出 accepted preimage 原文；下列历史 diff 仍可能含旧内容。不是当前 Git 文件归属或项目验收。</p>
            <ul><li v-for="file in state.evidence.receipt?.files || []" :key="file.path"><strong>{{ file.path }} · {{ file.outcome }}</strong><small>base {{ file.base_hash }} / mode {{ file.base_mode ?? 'missing' }}</small><small>after {{ file.after_hash }} / mode {{ file.after_mode ?? 'missing' }}</small></li></ul>
            <details v-if="state.evidence.result"><summary>查看敏感历史 Diff（显示上限 20000 字符）</summary><pre tabindex="0" aria-label="选中来源历史 Diff">{{ state.evidence.result.unified_diff.slice(0, 20000) }}</pre><p v-if="state.evidence.result.unified_diff.length > 20000" class="helper">这里只截取展示，非完整 diff；服务端证据未删除，不据此判断补丁范围或审批。</p></details>
          </section>
        </section>
      </aside>
    </div>
  </main>
</template>
