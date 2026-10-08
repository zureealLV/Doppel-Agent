<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from "vue";
import type { RunReportController } from "../runReport";
import { costReasonKeys, type CostSubset } from '../providerCost';
const props = defineProps<{ controller: RunReportController; active: boolean; blocked: boolean }>();
const c = props.controller, s = c.state;
const disabled = computed(() => !props.active || props.blocked || c.selectionLocked);
const consent = computed({ get: () => s.confirmed, set: value => c.confirmExport(value) });
const counter = (value: number | null) => value === null ? "未知" : String(value);
const provider = computed(() => s.report?.provider_usage);
const cost = computed(() => {
  const report = s.report;
  return report?.version === 4 && 'version' in report.cost ? report.cost : null;
});
const exportCost = computed(() => {
  const report = s.exportSnapshot;
  return report?.version === 4 && 'version' in report.cost ? report.cost : null;
});
const costText = (part: CostSubset) => part.currencies.length ? part.currencies.map(row => `${row.amount} ${row.currency}`).join('；') : '未知（不是 0）';
const costReasonLabels = { no_frozen_tariff: '缺少冻结费率', receipt_mismatch: '与原冻结费率不符',
  source_not_eligible: '原观测不具备计价来源', usage_unknown: '原用量未知', cache_partition_unknown: '实际缓存分区未知' };
const evidenceSections = computed(() => {
  const evidence = s.report?.evidence;
  return evidence ? [{ label: '原补丁效果账本', section: evidence.patches }, { label: '原逆补丁审查', section: evidence.inverses },
    { label: '原验证尝试', section: evidence.verifications }, { label: '原工作单绑定', section: evidence.work_orders }] : [];
});
watch(() => [props.active, props.blocked] as const, () => c.activate(props.active && !props.blocked), { immediate: true, flush: "sync" });
onBeforeUnmount(() => c.activate(false)); // root keeps original promise; no dispose here
</script>
<template>
  <section class="run-report" aria-labelledby="run-report-heading" :inert="blocked || undefined">
    <header><p class="eyebrow">ALLOWLIST / RECORDED EVIDENCE</p><h2 id="run-report-heading">脱敏运行报告</h2>
      <p>原生来源 <code>{{ s.runId || '尚未选择' }}</code>。打开、隐藏与重新进入不自动读取或导出。</p></header>
    <p class="helper">白名单只导出标识、枚举、日期、数字与规范费率／金额字符串，不包含提示词、回答、错误、路径、命令、工具正文、来源引用正文或推理。脱敏不是匿名：标识、哈希和计数仍可关联。</p>
    <p v-if="s.sourceNotice" role="status">{{ s.sourceNotice }}</p><p v-if="s.error" role="alert" class="field-error">{{ s.error }}</p>
    <div class="report-actions"><button type="button" :disabled="disabled || !s.runId" @click="c.readReport()">明确读取白名单报告</button>
      <button type="button" :disabled="disabled || !s.runId" @click="c.prepareExport()">明确准备导出快照（尚不写文件）</button>
      <button v-if="s.deferred" type="button" :disabled="disabled" @click="c.adoptDeferred()">明确显示原请求的迟到快照（不重读）</button></div>
    <p v-if="s.busy" role="status">等待同一原请求；隐藏、卸载详情、关闭或切项目不会中止、重发或丢弃它。</p>
    <article v-if="s.report" class="report-summary"><h3>所读快照，不是实时仪表盘</h3>
      <p>模式 {{ s.report.source.mode }} · 状态 {{ s.report.source.status }} · lease {{ s.report.source.lease_active ? 'active' : 'inactive' }}，都不证明物理排空。</p>
      <p>扫描 {{ s.report.snapshot.scanned_events }} / {{ s.report.snapshot.event_total }} 条事件；sequence high-water {{ s.report.snapshot.event_high_water }}。
        {{ s.report.snapshot.truncated ? '已截断，证据不完整。' : '本快照未因条数截断。' }}</p>
      <section v-if="provider" class="provider-observation" aria-labelledby="provider-observation-heading">
        <h3 id="provider-observation-heading">主计量：所选原回执观测</h3>
        <p>状态 {{ provider.state }}；已知 {{ provider.selected.known_units }} / 未知 {{ provider.selected.unknown_units }} 个观测单位。
          请求回执单位 {{ provider.selected.request_units }} · 逻辑方法单位 {{ provider.selected.logical_call_units }}，不是三层相加。</p>
        <dl class="usage-counters"><div><dt>观测输入</dt><dd>{{ counter(provider.selected.input_tokens) }}</dd></div>
          <div><dt>观测输出</dt><dd>{{ counter(provider.selected.output_tokens) }}</dd></div>
          <div><dt>观测合计</dt><dd>{{ counter(provider.selected.total_tokens) }}</dd></div></dl>
        <p v-if="provider.selected.subtotal_overflow" class="field-error">所选小计超出 safe integer，输出未知与溢出标记，不四舍五入或舍弃观测。</p>
        <p class="helper">有独立请求回执就优先采用其观测（包括失败响应），不再加入同一逻辑方法／回调计数。
          无请求回执时只采用合法逻辑观测；marker 本身不能去重。未知不是零，已知零才是 0。</p>
        <h4>所选观测归属：原外层标签，不重建所有权</h4>
        <p>父任务：已知 {{ provider.scopes.root.known_units }} / 未知 {{ provider.scopes.root.unknown_units }}；输入 {{ counter(provider.scopes.root.input_tokens) }} / 输出 {{ counter(provider.scopes.root.output_tokens) }}。</p>
        <p>child scopes {{ provider.scopes.children.length }} / {{ provider.scopes.children_total }}；未逐项展示 {{ provider.scopes.children_omitted }}。
          {{ provider.scopes.truncated ? '仅展示前 16 个子作用域，其余仍进入数字汇总。' : '子作用域条数未截断。' }}</p>
        <ul><li v-for="child in provider.scopes.children" :key="`${child.subagent_id}:${child.generation}`"><code>{{ child.subagent_id }}</code> · 原 generation {{ child.generation }}：
          已知 {{ child.known_units }} / 未知 {{ child.unknown_units }}，输入 {{ counter(child.input_tokens) }} / 输出 {{ counter(child.output_tokens) }}，溢出 {{ child.subtotal_overflow ? '是' : '否' }}。</li></ul>
        <p v-if="provider.scopes.children_omitted">其余子作用域：已知 {{ provider.scopes.other_children.known_units }} / 未知 {{ provider.scopes.other_children.unknown_units }}；
          输入 {{ counter(provider.scopes.other_children.input_tokens) }} / 输出 {{ counter(provider.scopes.other_children.output_tokens) }}，溢出 {{ provider.scopes.other_children.subtotal_overflow ? '是' : '否' }}。</p>
        <h4>完整扫描前缀的回执计数（不是 wire 请求数）</h4>
        <p>逻辑配对 {{ provider.counts.calls_matched }}：返回 {{ provider.counts.call_returned }} / 失败 {{ provider.counts.call_failed }} / 取消 {{ provider.counts.call_cancelled }}；
          method entered {{ provider.counts.call_method_entered }}。原 call 行 {{ provider.counts.call_rows }} / IDs {{ provider.counts.call_ids }}。</p>
        <p>请求配对 {{ provider.counts.requests_matched }}：返回 {{ provider.counts.request_returned }} / 失败 {{ provider.counts.request_failed }} / 取消 {{ provider.counts.request_cancelled }}；
          method entered {{ provider.counts.request_method_entered }}。原 request 行 {{ provider.counts.request_rows }} / IDs {{ provider.counts.request_ids }}。</p>
        <p>未完成 call {{ provider.counts.calls_unsettled }} / request {{ provider.counts.requests_unsettled }}；有效关联 {{ provider.counts.requests_linked }} / 孤儿或拒绝关联 {{ provider.counts.request_orphans }}。</p>
        <p>重复 call IDs {{ provider.counts.call_duplicate_ids }} / attempt IDs {{ provider.counts.request_duplicate_ids }}；无效 call IDs {{ provider.counts.call_invalid_ids }} / attempt IDs {{ provider.counts.request_invalid_ids }}；
          ordinal 冲突 {{ provider.counts.request_ordinal_collisions }} / 缺口 {{ provider.counts.request_ordinal_gaps }}。</p>
        <p>summary 不符 {{ provider.counts.summary_mismatches }}；历史 call 无 request summary {{ provider.counts.historical_calls_with_requests }}；
          无效外层 scope {{ provider.counts.invalid_scope_frames }} / 帧 {{ provider.counts.invalid_frames }}；未分类省略 {{ provider.counts.unclassified_rows }}。</p>
        <h4>未匹配兼容观测（不加账）</h4>
        <p>回调匹配 {{ provider.counts.callbacks_matched }} / 未匹配 {{ provider.counts.callbacks_unmatched }} / 总数 {{ provider.counts.callbacks }}；
          未知用量 {{ provider.counts.callbacks_invalid_usage }} / 无效 scope {{ provider.counts.callbacks_invalid_scope }}。</p>
        <p>仅未匹配、合法 scope 的兼容观测：已知 {{ provider.compatibility.known_units }} / 未知 {{ provider.compatibility.unknown_units }}；
          输入 {{ counter(provider.compatibility.input_tokens) }} / 输出 {{ counter(provider.compatibility.output_tokens) }} / 合计 {{ counter(provider.compatibility.total_tokens) }}，
          溢出 {{ provider.compatibility.subtotal_overflow ? '是' : '否' }}。不加入主计量或价格，不自动用它补齐未知请求。</p>
        <details><summary>白名单回执样本 {{ provider.samples.emitted }} / {{ provider.samples.total }} · 未逐项展示 {{ provider.samples.omitted }}</summary>
          <p class="helper">最多前 16 条，按原 sequence；样本不是完整账本。未完成 start 仅进入计数／未知单位，不能从样本缺失推断未调用。
            未入选已知回执 {{ provider.counts.unselected_known_receipts }}，也不额外加账。</p>
          <ol><li v-for="sample in provider.samples.items" :key="sample.seq"><p>seq {{ sample.seq }} · {{ sample.kind }} · receipt v{{ sample.receipt_version ?? '无' }}；
            {{ sample.scope.kind === 'root' ? '父任务 scope' : 'child scope' }} <template v-if="sample.scope.kind === 'child'"><code>{{ sample.scope.subagent_id }}</code> / generation {{ sample.scope.generation }}</template>。</p>
            <p>{{ sample.engine ?? '兼容回调' }} / {{ sample.actor ?? '无 actor 标记' }} · {{ sample.outcome ?? '无方法 outcome' }}；
              method entered {{ sample.method_entered === null ? '未标记' : sample.method_entered ? 'true' : 'false' }} · 所选观测 {{ sample.selected ? '是' : '否' }}。
              {{ sample.callback_linkage === null ? '' : sample.callback_linkage === 'matched' ? '严格匹配的兼容回调，不加账。' : '未匹配兼容回调，不加到主计量。' }}</p>
            <p v-if="sample.call_id">原 call <code>{{ sample.call_id }}</code><template v-if="sample.attempt_id"> · attempt <code>{{ sample.attempt_id }}</code> / ordinal {{ sample.attempt_index }}</template>；标识只用于关联，不授予执行。</p>
            <p>输入 {{ counter(sample.usage?.input_tokens ?? null) }} / 输出 {{ counter(sample.usage?.output_tokens ?? null) }}；
              cache {{ sample.usage_details.cache_state }}，hit {{ counter(sample.usage_details.cached_input_tokens) }} / miss {{ counter(sample.usage_details.uncached_input_tokens) }}；
              reasoning {{ sample.usage_details.reasoning_state }} / {{ counter(sample.usage_details.reasoning_output_tokens) }}。</p>
          </li></ol>
        </details>
        <p class="helper">所选观测不等于完整账单；wire 覆盖未知，隐藏重试／redirect／provider 账单／账户 cap 都未证明。
          cache/reasoning 是原数值明细，缺失不推算为零，也不额外加价。{{ cost ? '费用只按下面的原冻结声明估算，不授予执行、恢复或审批。' : '费用未知，不是免费；报告不授予执行、恢复或审批。' }}</p>
      </section>
      <p v-else class="helper">旧版报告没有原 call/request 关联覆盖：主计量未知，只保留完成事件兼容观测。不能据此推算请求、升级明细或自动重读。</p>
      <details class="compatibility-events"><summary>历史完成事件兼容层（不加到主计量）</summary>
      <p>已知有效用量事件 {{ s.report.usage.observed.known_model_events }}；兼容输入 {{ counter(s.report.usage.observed.input_tokens) }} / 输出 {{ counter(s.report.usage.observed.output_tokens) }}。
        {{ s.report.usage.observed.subtotal_overflow ? '小计溢出，不能取舍或四舍五入为实际用量。' : '' }}</p>
      <p>父任务：{{ s.report.usage.root.known_model_events }} 条已知用量事件，输入 {{ counter(s.report.usage.root.input_tokens) }} / 输出 {{ counter(s.report.usage.root.output_tokens) }}。</p>
      <ul><li v-for="child in s.report.usage.children" :key="`${child.subagent_id}:${child.generation}`"><code>{{ child.subagent_id }}</code> · 原 generation {{ child.generation }}：
        {{ child.known_model_events }} 条已知事件，输入 {{ counter(child.input_tokens) }} / 输出 {{ counter(child.output_tokens) }}。</li></ul>
      <p>用量状态 {{ s.report.usage.state }}；未知模型事件 {{ s.report.usage.unknown_model_events }}；未分类用量信封 {{ s.report.usage.unclassified_usage_envelopes }}。</p>
      </details>
      <section v-if="cost" class="declared-cost" aria-labelledby="declared-cost-heading">
        <h3 id="declared-cost-heading">声明费率估算：不是账单</h3>
        <p>费用状态 {{ cost.state }} · 覆盖 {{ cost.coverage }} · 冻结来源 {{ cost.source_state }}。
          所选 {{ cost.selected_units }}；可计价 {{ cost.known_units }} / 未知 {{ cost.unknown_units }}；请求单位 {{ cost.request_units }} / 逻辑单位 {{ cost.logical_call_units }}。</p>
        <p v-if="cost.state === 'unknown'" class="helper">费用未知，不是免费；无可计价观测不等于发生了零费用。</p>
        <p v-else-if="cost.state === 'partial'" class="helper">只有下列可计价子集的声明估算，未知或省略的观测不补零，部分前缀不代表完整消耗。</p>
        <p v-else class="helper">所读前缀的声明费率估算已知；不表示全部 wire 调用或完整账单已知。</p>
        <ul class="cost-currencies"><li v-for="currency in cost.currencies" :key="currency.currency">
          <strong>{{ currency.amount }} {{ currency.currency }}</strong> · 可计价 {{ currency.known_units }}，请求 {{ currency.request_units }} / 逻辑 {{ currency.logical_call_units }}。
          <p>精确可计价子集见证：输入 {{ currency.input_tokens }} / 输出 {{ currency.output_tokens }}。
            <template v-if="currency.cached_input_tokens !== null">实际 cache hit {{ currency.cached_input_tokens }} / miss {{ currency.uncached_input_tokens }}。</template></p>
        </li></ul>
        <h4>计价未知原因（完整所选集合，不按样本推算）</h4>
        <ul><li v-for="reason in costReasonKeys" :key="reason">{{ costReasonLabels[reason] }}：{{ cost.unknown_reasons[reason] }}</li></ul>
        <h4>原外层标签的费用归属</h4>
        <p>父任务费用归属：{{ costText(cost.scopes.root) }}；可计价 {{ cost.scopes.root.known_units }} / 未知 {{ cost.scopes.root.unknown_units }}。</p>
        <p>子费用作用域 {{ cost.scopes.children.length }} / {{ cost.scopes.children_total }}；未逐项展示 {{ cost.scopes.children_omitted }}，仍在其余子任务和总额里，不丢账。</p>
        <ul><li v-for="child in cost.scopes.children" :key="`${child.subagent_id}:${child.generation}`"><code>{{ child.subagent_id }}</code> · 原 generation {{ child.generation }}：
          {{ costText(child) }}；可计价 {{ child.known_units }} / 未知 {{ child.unknown_units }}。
          <span v-if="child.unknown_units">未知原因：<template v-for="reason in costReasonKeys" :key="reason">{{ costReasonLabels[reason] }} {{ child.unknown_reasons[reason] }}；</template></span></li></ul>
        <p v-if="cost.scopes.children_omitted">其余子任务费用归属：{{ costText(cost.scopes.other_children) }}；可计价 {{ cost.scopes.other_children.known_units }} / 未知 {{ cost.scopes.other_children.unknown_units }}。
          未知原因：<template v-for="reason in costReasonKeys" :key="reason">{{ costReasonLabels[reason] }} {{ cost.scopes.other_children.unknown_reasons[reason] }}；</template></p>
        <details v-if="cost.price_snapshot"><summary>原运行冻结声明（来源未验证）</summary>
          <p>币种 {{ cost.price_snapshot.currency }}；生效 {{ cost.price_snapshot.effective_date }} / 冻结 {{ cost.price_snapshot.frozen_date }}；
            原声明类型 {{ cost.price_snapshot.source_kind }}，不是已验证官方价格。单位 {{ cost.price_snapshot.unit_tokens }} tokens。</p>
          <p>原计价 basis {{ cost.price_snapshot.billing_basis }}；输入 {{ cost.price_snapshot.rates.input ?? '不采用平价输入' }} / 实际缓存 hit {{ cost.price_snapshot.rates.cached_input ?? '不单独采用' }} /
            miss {{ cost.price_snapshot.rates.uncached_input ?? '不单独采用' }} / 输出 {{ cost.price_snapshot.rates.output }}。
            reasoning 已包含在输出，缺缓存分区不自行推算。</p>
          <p>冻结 tariff SHA-256 <code>{{ cost.price_snapshot.tariff_sha256 }}</code>；来源引用 SHA-256 <code>{{ cost.price_snapshot.source_sha256 }}</code>；
            原模型绑定 SHA-256 <code>{{ cost.price_snapshot.binding_sha256 }}</code>。哈希仅关联，不认证来源、账单或匿名性；不显示私有引用／模型／endpoint。</p>
        </details>
        <p class="helper">有效观测估算不等于完整账单。只用原运行冻结声明，不从当前设置或前 16 个样本补价；不汇兑、不将兼容回调加账，来源未验证，不保证账户上限。
          完整性、provider 发票、隐藏请求、实际付款和物理排空均未证明。已知零才显示 0，未知不显示 0。</p>
      </section>
      <p v-else class="helper">有效观测小计不等于完整账单；旧无子任务标记不能重建归属，空记录不是零调用或零费用。当前报告未提供计费价格投影：费用未知，不是免费或账户上限保证。</p>
      <p>记录的完成事件：Graph 工具 {{ s.report.recorded_effect_counts['graph.tool_finished'] }}，Deep 工具 {{ s.report.recorded_effect_counts['deep.tool_finished'] }}，MCP {{ s.report.recorded_effect_counts['mcp.tool_executed'] }}，patch {{ s.report.recorded_effect_counts['patch.applied'] }}。</p>
      <p class="helper">事件计数不是原补丁／逆补丁账本、验证成功、当前 Diff 归属或任务质量证明；下面的原账本证据单独读取和核对。</p>
      <p v-if="s.report.service.failed_close_diagnostic" class="field-error">原服务关闭失败，仅诊断读取；不开放执行。</p>
      <p v-if="s.report.service.execution_admission === 'quarantined'" class="field-error">原执行准入已隔离：provider 凭据／清理不确定时只保留读取与原取消／排空路径，不重试未知调用。报告不是故障解除或资源排空证明。</p>
      <p class="helper">SQL 只读快照，WAL 协调不是文件系统零写入保证。此报告不证明资源排空或模型质量。</p>
      <template v-if="s.report.evidence"><h3>原账本白名单证据（跨快照不原子）</h3>
        <p class="helper">运行事件与原账本来自独立只读事务，不能拼成原子交付回执。每类最多扫描 16 条，累计原正文 decode 预算 8MiB；遗漏、截断或不可用都不证明无记录。
          原 tool-call／task ID 只给域隔离 SHA-256，不能用于审批，也不是匿名。当前 Git/Diff 不参与归属判断。</p>
        <div v-for="entry in evidenceSections" :key="entry.label"><h4>{{ entry.label }}</h4>
          <p>{{ entry.section.state }} · 扫描 {{ entry.section.scanned }} / {{ counter(entry.section.total) }}，展示 {{ entry.section.emitted }}，遗漏 {{ entry.section.omitted }}；
            {{ entry.section.truncated === null ? '截断状态未知' : entry.section.truncated ? '已截断' : '条数未截断' }}。{{ entry.section.reason || '' }}</p></div>
        <article v-for="item in s.report.evidence.patches.items" :key="item.tool_call_sha256"><h4>补丁 <code>{{ item.patch_id || '无可验证 receipt' }}</code></h4>
          <p>{{ item.operation }} · {{ item.status }} · receipt {{ item.receipt_status || '未知' }} · 已核对实际 applied receipt：{{ item.confirmed_applied ? '是' : '未确认' }}。
            文件数 {{ counter(item.files) }}，preimage {{ item.preimage_state }}。</p><p>tool-call SHA-256 <code>{{ item.tool_call_sha256 }}</code>，不导出文件路径、哈希或私有内容。</p></article>
        <article v-for="item in s.report.evidence.inverses.items" :key="item.review_id"><h4>逆补丁审查 <code>{{ item.review_id }}</code></h4>
          <p>{{ item.status }} · {{ item.files }} 个文件；source patch <code>{{ item.source_patch_id }}</code> → inverse patch <code>{{ item.patch_id }}</code>。
            原 source receipt {{ item.source_receipt_state }}；原 inverse effect receipt {{ item.effect_receipt_state }}。
            {{ item.outcome_unknown ? '原效果仍未知，不允许据此重试。' : '' }}</p>
          <p v-if="item.pending_expired" class="helper">原 pending 已过期，报告仅投影、不修改或恢复执行。</p>
          <p class="helper">审查状态与实际效果账本分开；未确认 receipt 不是“没有执行”，报告读取不授予逆补丁执行。</p></article>
        <article v-for="item in s.report.evidence.verifications.items" :key="item.review_id"><h4>验证尝试 <code>{{ item.review_id }}</code></h4>
          <p>{{ item.status }} · 原 source patch <code>{{ item.source_patch_id }}</code>，receipt {{ item.source_receipt_state }}。</p>
          <p v-if="item.pending_expired" class="helper">原 pending 已过期，报告仅投影、不修改或恢复执行。</p>
          <p>计划 {{ item.planned_commands }} · 已封存 {{ item.sealed_steps }} · 已退出 {{ item.exited_commands }} · 失败尝试 {{ item.failed_attempts }} · 剩余 {{ item.remaining_commands }}。
            {{ item.has_unknown_command ? '存在原 running 步骤，结果未知。' : '' }} 已封存 attempts success {{ item.attempts_success === null ? '未知' : item.attempts_success ? 'true' : 'false' }}。</p>
          <ol><li v-for="step in item.steps" :key="step.index">原 step {{ step.index }} · {{ step.status }} · exit {{ step.exit_code === null ? '未知' : step.exit_code }}
            · {{ step.failure || '无封存失败分类' }} · duration {{ counter(step.duration_ms) }} ms，不导出 argv/stdout/stderr。</li></ol>
          <p class="helper">completed 可能只封存失败前缀，不代表所有命令都执行，更不是项目验收通过；目标是执行时当前 workspace，不是原 patch 快照。</p></article>
        <article v-for="item in s.report.evidence.work_orders.items" :key="item.attempt_id"><h4>工作单 <code>{{ item.work_order_id }}</code></h4>
          <p>原 attempt <code>{{ item.attempt_id }}</code> · 第 {{ item.attempt_number }} 次 · {{ item.attempt_status }}；order {{ item.order_status }}，dispatch {{ item.dispatch_state || '未知' }}。</p>
          <p>原执行 revision {{ item.execution_revision }}；当前计划 revision {{ item.active_plan_revision }}。task SHA-256 <code>{{ item.task_id_sha256 }}</code>，不自动绑定新版或推断全工作单完成。</p></article>
      </template>
      <p v-else class="helper">旧 v1 数字报告不含原账本证据；未读取不表示不存在，不自动升级或重新读库。</p>
    </article>
    <article v-if="s.exportSnapshot" class="report-export"><h3>已固定的导出快照</h3>
      <p>report v{{ s.exportSnapshot.version }} · run <code>{{ s.exportSnapshot.run_id }}</code>，sequence high-water {{ s.exportSnapshot.snapshot.event_high_water }}，扫描 {{ s.exportSnapshot.snapshot.scanned_events }} / {{ s.exportSnapshot.snapshot.event_total }}。
        这是另一次明确读取，与展示快照可能不同；本地下载不会再次读库。</p>
      <p class="helper">{{ s.exportSnapshot.provider_usage ? 'v3/v4 只以所选原回执为主计量，兼容层单独保留，不相加。' : '历史 v1/v2 无 canonical 请求覆盖，主计量未知；不自动升级。' }}</p>
      <template v-if="exportCost"><p>导出费用状态 {{ exportCost.state }} · 可计价 {{ exportCost.known_units }} / 未知 {{ exportCost.unknown_units }} · 来源 {{ exportCost.source_state }}。</p>
        <p v-for="currency in exportCost.currencies" :key="currency.currency">导出估算 {{ currency.amount }} {{ currency.currency }}，只来自已固定的导出快照。</p>
        <p class="helper">原冻结声明与计价未知原因保留在同一不可变 JSON 中；不是当前展示价格、完整账单、来源认证或账户上限。下载不重新计价或查设置。</p></template>
      <label><input v-model="consent" type="checkbox" :disabled="disabled" />我知悉标识可关联；只下载这一已准备的白名单 JSON，浏览器下载请求不证明文件写入完成。</label>
      <button type="button" :disabled="disabled || !s.confirmed" @click="c.exportPrepared()">明确请求下载已准备的 JSON</button>
      <p class="helper">原生 WebView 下载能力尚待 S9 验收；未宣称文件已保存，也不申请任意路径或覆盖用户文件。每次导出都需要新的明确确认。</p>
    </article>
    <p v-if="s.exportState !== 'none'" role="status">原 run <code>{{ s.exportRunId }}</code> 的本地下载请求：{{ s.exportState }}。不证明文件写入完成；未知结果不会自动重试。</p>
  </section>
</template>
<style scoped>
.run-report { padding: 1.25rem; border: 1px solid var(--border, #344052); border-radius: 12px; margin: 1rem 0; }
.report-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.report-summary, .report-export { margin-top: 1rem; padding: 1rem; background: var(--surface, #151d2a); border-radius: 8px; }
.report-export label { display: block; margin: .8rem 0; }
.provider-observation { border-left: 3px solid var(--accent, #73b9b0); padding-left: 1rem; margin: 1rem 0; }
.usage-counters { display: flex; flex-wrap: wrap; gap: 1.5rem; }
.usage-counters dt { font-size: .85rem; opacity: .8; }
.usage-counters dd { margin: .2rem 0; font-size: 1.4rem; font-variant-numeric: tabular-nums; }
.compatibility-events { margin: 1rem 0; }
.declared-cost { border-left: 3px solid var(--accent, #73b9b0); padding-left: 1rem; margin: 1rem 0; }
.cost-currencies { font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
summary { cursor: pointer; }
code { overflow-wrap: anywhere; }
</style>
