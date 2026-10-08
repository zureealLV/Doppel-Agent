<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { VerificationReviewController } from "../verificationReview";
import { validVerificationNames } from "../verificationApi";
const props = defineProps<{ controller: VerificationReviewController; blocked: boolean }>();
const state = props.controller.state;
const namesMode = ref("all"), namesJson = ref('["unit"]'), selectionError = ref(""), existingId = ref("");
const prepareConsent = ref(false), prepareCommand = ref(false), prepareWrite = ref(false);
const decisionConsent = ref(false), decisionCommand = ref(false), decisionWrite = ref(false);
const storedConsent = ref(false), retryConsent = ref(false), cancelConsent = ref(false), reconcileConsent = ref(false), exitConsent = ref(false), discardConsent = ref(false);
const unavailable = computed(() => props.blocked || !state.active || state.closing || state.busy || state.controlling || state.reading);
const pending = computed(() => state.review?.status === "pending" && !state.review.lifecycle?.pending_expired && !props.controller.outcomeUnknown);
const planned = computed(() => state.review?.plan.commands.length ?? 0);
const attempted = computed(() => state.review?.steps.length ?? 0);
const sealed = computed(() => state.review?.steps.filter(step => step.result !== null).length ?? 0);
function clearConsent() { prepareConsent.value = prepareCommand.value = prepareWrite.value = decisionConsent.value = decisionCommand.value = decisionWrite.value = false;
  storedConsent.value = retryConsent.value = cancelConsent.value = reconcileConsent.value = exitConsent.value = discardConsent.value = false; }
watch(() => [state.review, state.intent, state.controlling, state.reading, state.armed, state.active, state.closing, props.blocked, namesMode.value, namesJson.value], clearConsent, { flush: "sync" });
async function prepare() {
  if (unavailable.value) return;
  let names: string[] | null = null;
  try { if (namesMode.value !== "all") names = JSON.parse(namesJson.value) as string[]; if (!validVerificationNames(names)) throw new Error(); }
  catch { selectionError.value = "名称必须是 1–16 个唯一非空名称的 JSON 数组，每项 UTF-8 ≤128 字节；不接受 argv。没有发送。"; clearConsent(); return; }
  selectionError.value = "";
  const confirmed = prepareConsent.value, command = prepareCommand.value, write = prepareWrite.value; clearConsent();
  await props.controller.prepare(names, confirmed, command, write);
}
async function decide(action: "approve" | "reject") {
  const view = state.review; if (unavailable.value || !view) return;
  const confirmed = decisionConsent.value, command = decisionCommand.value, write = decisionWrite.value; clearConsent();
  await props.controller.decide(action, view.review_id, view.plan.plan_id, confirmed, command, write);
}
function armStored() { if (unavailable.value) return; const confirmed = storedConsent.value; clearConsent(); props.controller.armStored(confirmed); }
async function cancel() { if (props.blocked || !props.controller.canCancel) return; const confirmed = cancelConsent.value; clearConsent(); await props.controller.cancel(confirmed); }
</script>
<template>
  <section class="verification-review-panel" aria-labelledby="verification-title" :inert="blocked || undefined">
    <header class="section-heading"><h2 id="verification-title">精确命令验证</h2><span class="helper">独立于补丁回执，非全项目验收</span></header>
    <p class="helper">准备只读取当前项目受约束配置，不执行命令；批准必须确认完整 argv 数组/配置指纹与新的命令与写入授权。不会从历史 run、逆向审批或“已读”继承权限。验证对象是当前工作区，不是原补丁时刻的快照。</p>
    <p v-if="state.runId" class="helper">历史查询范围 run：<code>{{ state.runId }}</code>；已注册不等于当前静止、允许执行或来源补丁成功。</p>
    <p v-else class="helper">尚无来源 run；不构造计划、命令或验证成功记录。</p>
    <p v-if="state.error" class="order-warning" role="alert">{{ state.error }}</p>
    <p v-if="state.busy" role="status">原验证请求仍在进行，保留原 promise；不设置前端效果超时/重发。可明确取消同一 review/plan，并等待服务器原任务与控制请求分别排空。</p>
    <p v-if="state.controlling" role="status">正在等待原取消/对账回执；不自动重发、不据 HTTP 断开宣称进程已停止。</p>
    <p v-if="state.reading" role="status">只读查询原持久证据；不更新 TTL/config，不启动、取消或授予权限。</p>
    <div v-if="!state.review && !state.intent" class="verification-prepare">
      <p v-if="!state.source" class="helper">新准备须先选择实际 confirmed applied 的补丁回执；可独立读取本 run 的历史验证，但不能据验证结果反推补丁成功。</p>
      <p v-else class="helper">准备来源：{{ state.source.tool_call_id }} / {{ state.source.patch_id }}；preimage 保留与否不决定命令验证，不从历史 Diff 拼 argv。</p>
      <label>配置命令选择<select v-model="namesMode" :disabled="unavailable || !state.source"><option value="all">全部配置命令（并非全部项目测试）</option><option value="names">明确名称列表</option></select></label>
      <label v-if="namesMode === 'names'">精确名称 JSON 数组<input v-model="namesJson" maxlength="8192" autocomplete="off" spellcheck="false" :disabled="unavailable || !state.source" /></label>
      <p v-if="selectionError" class="order-warning" role="alert">{{ selectionError }}</p>
      <label class="context-confirm"><input v-model="prepareConsent" type="checkbox" :disabled="unavailable || !state.source" />我确认该来源与命令名称范围，创建新的持久配置预览；这不是执行批准。</label>
      <label class="context-confirm"><input v-model="prepareCommand" type="checkbox" :disabled="unavailable || !state.source" />仅为准备请求提供新的 command_execute 授权；预览不执行。</label>
      <label class="context-confirm"><input v-model="prepareWrite" type="checkbox" :disabled="unavailable || !state.source" />仅为准备请求提供新的 workspace_write 授权；不会继承到决定。</label>
      <button type="button" :disabled="unavailable || !state.source || !prepareConsent || !prepareCommand || !prepareWrite" @click="prepare">准备新的精确命令预览</button>
    </div>
    <div v-if="state.intent" class="verification-intent">
      <h3>原 {{ state.intent.kind }} 请求</h3><p>{{ state.intent.source.run_id }} / {{ state.intent.review_id }} / {{ state.intent.plan_id ?? '准备时尚未收到 plan ID' }}</p>
      <details><summary>精确原 payload（仅内存，复选框不改写）</summary><pre>{{ JSON.stringify(state.intent.body, null, 2) }}</pre></details>
      <p v-if="state.uncertain" class="order-warning">原回复未知；GET pending/404 不证明没执行，不替换来源/ID/配置，不重复已消耗决定。</p>
      <template v-if="state.uncertain && state.intent.kind === 'prepare'"><label class="context-confirm"><input v-model="retryConsent" type="checkbox" :disabled="unavailable" />我明确仅重发原准备 ID/名称/权限/payload，不刷新 config/base/TTL，不执行命令。</label><button type="button" :disabled="unavailable || !retryConsent" @click="controller.retryPrepare(retryConsent)">仅重发原准备</button></template>
    </div>
    <details v-if="state.controlIntents.length"><summary>原控制请求 IDs/payload（本次 UI 最多保留 16 条，不是成功证明）</summary><div v-for="(intent, index) in state.controlIntents" :key="index"><p>{{ intent.kind }} / {{ intent.source.run_id }} / {{ intent.review_id }} / {{ intent.plan_id }}</p><pre>{{ JSON.stringify(intent.body, null, 2) }}</pre></div></details>
    <p v-if="state.controlUncertain" class="order-warning">原控制/排空回执未知；保留精确 payload，仅恢复原证据，不自动重复取消或宣称原进程不存在。</p>
    <div v-if="state.review" class="verification-preview">
      <h3>精确 review：{{ state.review.review_id }}</h3>
      <p class="helper">来源：{{ state.review.source.run_id }} / {{ state.review.source.tool_call_id }} / {{ state.review.source.patch_id }}；operation：{{ state.review.operation_id }}</p>
      <dl class="changes-metrics"><div><dt>状态/有效期</dt><dd>{{ state.review.status }} / {{ state.review.lifecycle?.effective_status ?? state.review.status }} · {{ state.review.expires_at }}</dd></div><div><dt>plan ID</dt><dd><code>{{ state.review.plan.plan_id }}</code></dd></div><div><dt>配置路径</dt><dd>{{ state.review.plan.source.path }}</dd></div><div><dt>config hash</dt><dd><code>{{ state.review.plan.source.config_hash }}</code></dd></div><div><dt>snapshot hash</dt><dd><code>{{ state.review.plan.source.snapshot_hash }}</code></dd></div><div><dt>workspace identity</dt><dd><code>{{ state.review.plan.source.workspace_id }}</code></dd></div></dl>
      <p class="order-warning">敏感完整 argv/结果，尚未 S8 全局脱敏，不写浏览器存储，不以 shell 字符串代替 argv 或截断审批。opaque plan ID 不由 JS JSON.stringify 重算；服务器按自身 canonical JSON 验证，批准及每条命令前复查配置/身份。可执行文件、依赖与工作区内容未被此 hash 全部固定，非 OS 沙箱。</p>
      <p class="helper">配置 operation_timeout_seconds：{{ state.review.plan.operation_timeout_seconds }} 秒（后端执行阶段含 command 资源等待，不是 HTTP/准备 IO/工作区锁等待/物理排空的硬总时限）；输出字节上限：{{ state.review.plan.max_output_bytes }}；stop_on_failure={{ state.review.plan.stop_on_failure }}。不据超时推断效果不存在，也不设置前端效果超时。</p>
      <ol><li v-for="command in state.review.plan.commands" :key="command.name"><strong>{{ command.name }} · timeout {{ command.timeout_seconds }} 秒</strong><pre tabindex="0" aria-label="完整 argv 参数数组">{{ JSON.stringify(command.argv, null, 2) }}</pre></li></ol>
      <template v-if="pending">
        <div v-if="!state.armed"><label class="context-confirm"><input v-model="storedConsent" type="checkbox" :disabled="unavailable" />我重新确认这份存储预览的精确来源/review/plan/config/完整 argv 与限制；读取不授予权限，不重新捕获配置。</label><button type="button" :disabled="unavailable || !storedConsent" @click="armStored">重新确认该存储预览</button></div>
        <label class="context-confirm"><input v-model="decisionConsent" type="checkbox" :disabled="unavailable" />我只决定上述精确 review/plan，不改变 argv/名称/超时或自动合并配置变化。</label>
        <label class="context-confirm"><input v-model="decisionCommand" type="checkbox" :disabled="unavailable || !state.armed" />为本次执行重新提供新的 command_execute 授权。</label>
        <label class="context-confirm"><input v-model="decisionWrite" type="checkbox" :disabled="unavailable || !state.armed" />为本次执行重新提供新的 workspace_write 授权；命令可能修改项目。</label>
        <div class="button-row"><button type="button" class="danger-button" :disabled="unavailable || !state.armed || !decisionConsent || !decisionCommand || !decisionWrite" @click="decide('approve')">批准执行这份精确计划</button><button type="button" :disabled="unavailable || !decisionConsent" @click="decide('reject')">拒绝（不执行命令）</button></div>
      </template>
      <div v-if="controller.canCancel"><label class="context-confirm"><input v-model="cancelConsent" type="checkbox" :disabled="blocked || !state.active || state.controlling" />我明确取消同一 review/plan 的待批准/原 live 操作，等待原任务/后代/锁及控制请求排空；不是前端 abort 或未执行证明。</label><button type="button" :disabled="blocked || !state.active || !cancelConsent || !controller.canCancel" @click="cancel">取消原操作并等待排空回执</button></div>
      <div v-if="controller.canReconcile"><label class="context-confirm"><input v-model="reconcileConsent" type="checkbox" :disabled="unavailable" />只对账已封存步骤/原未知 review；不重新读取配置、不执行命令、不补跑缺失步骤。</label><button type="button" :disabled="unavailable || !reconcileConsent" @click="controller.reconcile(reconcileConsent)">显式元数据对账</button></div>
      <h3>实际证据与分母</h3><p class="helper">计划 {{ planned }} · 已记录 attempt {{ attempted }} · 已封存 {{ sealed }} · 尚未记录 attempt {{ planned - attempted }} · 未封存/未知 {{ attempted - sealed }}。{{ state.review.evidence }} / {{ state.review.error_code ?? '未报告错误代码' }}</p>
      <p v-if="state.review.status === 'completed'" class="helper">该已封存操作 success={{ state.review.success }}；可能因 stop_on_failure 提前停止，并不代表全部计划命令都运行，更非全项目验收/当前代码正确或补丁成功。</p>
      <p v-if="state.review.status === 'cancelled' || state.review.has_unknown_command" class="order-warning">cancelled 不抹去已执行的工作区效果；存在未封存步骤时结果仍未知，不补跑、不宣称进程不存在。SQL 读取不是实际 OS 排空证明。</p>
      <p v-if="state.review.status === 'failed' || state.review.status === 'indeterminate'" class="order-warning">验证失败/未知独立于原补丁状态，不回滚、不覆盖原 applied 回执；已执行命令仍可能有部分写入。</p>
      <p v-if="state.review.status === 'expired' || state.review.lifecycle?.pending_expired" class="order-warning">存储预览已过期，GET 不刷新 TTL，不可批准；新准备必须独立确认并重新捕获配置。</p>
      <div v-for="step in state.review.steps" :key="step.index" class="verification-step"><h4>步骤 {{ step.index + 1 }} · {{ step.status }}</h4><template v-if="step.result"><p>{{ step.result.name }} · exit={{ step.result.exit_code ?? '无 exit code' }} · success={{ step.result.success }} · {{ step.result.duration_ms }}ms · supervision={{ step.result.supervision }} · {{ step.result.error ?? '无步骤错误代码' }}</p><details><summary>完整敏感 stdout/stderr（UTF-8 replacement 展示，非原字节无损档案）</summary><h5>stdout</h5><pre tabindex="0">{{ step.result.stdout }}</pre><h5>stderr</h5><pre tabindex="0">{{ step.result.stderr }}</pre></details></template><p v-else class="order-warning">只有原 attempt intent，没有已封存结果；不是完成、失败或未执行证明。</p></div>
      <template v-if="!controller.selectionLocked"><label class="context-confirm"><input v-model="discardConsent" type="checkbox" :disabled="unavailable" />仅收起当前 UI 预览；持久 review/步骤保留，不删除、取消、重跑或抹除工作区效果。</label><button type="button" :disabled="unavailable || !discardConsent" @click="controller.discardPreview(discardConsent)">收起预览，保留持久证据</button></template>
    </div>
    <button v-if="state.intent || state.review" type="button" :disabled="blocked || !controller.canRecover" @click="controller.recover()">只读恢复原 ID（不执行/授予/取消）</button>
    <div v-if="controller.outcomeUnknown" class="verification-exit-warning"><p class="order-warning">未知保留，关闭/切换等待所有原 mutation/control/read promises 与 host owner。UI IDs/payload 仅内存、不跨重启持久化，请记下 run/tool call/patch/review/plan IDs 供手动只读查询；404 不是未执行证明。前端退出/dispose 不证明服务器或 OS 清理成功。</p><label class="context-confirm"><input v-model="exitConsent" type="checkbox" :disabled="unavailable" />我已记下原 IDs，知悉工作区可能已改变，仅允许退出/切换后恢复，不重复未知操作。</label><button type="button" :disabled="unavailable || !exitConsent" @click="controller.acknowledgeExit(exitConsent)">仅确认允许退出，未知仍保留</button><p v-if="state.exitAcknowledged" class="helper">只解除退出阻塞，不开放新操作、不丢弃原 payload、不自动取消或安全擦除。</p></div>
    <section class="verification-history" aria-labelledby="verification-history-title"><h3 id="verification-history-title">该 run 的验证历史</h3>
      <p class="helper">只读 review ID keyset 顺序（不是时间顺序），每页 ≤16 条、服务端聚合解码 ≤8 MiB；不是总记录数/项目成功率，摘要不含 argv/输出。跨页不保证原子快照。SQLite ro/query_only 不保证 WAL sidecar 零物理写入。</p>
      <button type="button" :disabled="unavailable || controller.selectionLocked || !state.runId" @click="controller.loadHistory()">显式读取/重读历史首屏</button>
      <p v-if="state.historyError" class="order-warning" role="alert">{{ state.historyError }}</p><p v-if="state.history && state.historyStale" class="order-warning">旧页已过期，不能据其选择新决定；需显式读取，不自动轮询。</p>
      <template v-if="state.history"><p class="helper">cursor {{ state.historyCursor || '起点' }} · navigation depth {{ state.historyDepth }} · decoded {{ state.history.decode_bytes }} bytes · budget_limited={{ state.history.budget_limited }}</p>
        <p v-if="!state.history.items.length" class="helper">本页无已读 review，不证明没有命令/项目变更。</p>
        <ul><li v-for="item in state.history.items" :key="item.review_id"><button type="button" :disabled="unavailable || controller.selectionLocked || state.historyStale" @click="controller.readHistory(item)"><strong>{{ item.review_id }} · {{ item.status }} / {{ item.lifecycle?.effective_status }}</strong><small>{{ item.source.tool_call_id }} / {{ item.source.patch_id }}</small><small>success={{ item.success ?? '无终态 success' }} · planned={{ item.lifecycle?.planned_commands }} · sealed={{ item.lifecycle?.sealed_steps }} · unknown={{ item.lifecycle?.outcome_unknown }}</small></button></li></ul>
        <div class="button-row"><button type="button" :disabled="unavailable || controller.selectionLocked || state.historyDepth === 0" @click="controller.previousHistory()">上一页</button><button type="button" :disabled="unavailable || controller.selectionLocked || state.historyStale || !state.history.has_more || !state.history.next_after_id" @click="controller.nextHistory()">下一页（不猜总数/时间）</button></div>
      </template>
      <form @submit.prevent="controller.readExisting(existingId.trim())"><label>已有 review ID（限定当前 run，只读）<input v-model="existingId" maxlength="32" autocomplete="off" spellcheck="false" :disabled="unavailable || controller.selectionLocked" /></label><button type="submit" :disabled="unavailable || controller.selectionLocked || !state.runId || !/^[0-9a-f]{32}$/.test(existingId.trim())">只读已有精确 review</button></form>
    </section>
  </section>
</template>
