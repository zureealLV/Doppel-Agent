<script setup lang="ts">
import type { InputSnapshot } from "../types";
defineProps<{ snapshot?: InputSnapshot | null }>();
</script>
<template>
  <section class="context-input-receipt output-card" aria-label="服务保存的固定输入回执">
    <p class="eyebrow">SAVED INPUT / NOT CURRENT PANEL</p><h2>固定上下文输入</h2>
    <p class="helper">只读显示此 run 的已存输入；面板改选不改这份回执。它不是文件当前状态、模型理解程度或测试通过的证明。上下文是用户角色数据，不授予工具权限。</p>
    <p v-if="!snapshot" class="helper">此记录没有上下文快照（旧记录或未显式绑定）；不会用当前面板选择补造历史输入。</p>
    <template v-else>
      <dl class="input-receipt-metrics"><div><dt>作用域</dt><dd>{{ snapshot.scope_work_order_id ? `工作单 ${snapshot.scope_work_order_id}` : '项目' }}</dd></div><div><dt>输入冻结时间</dt><dd>{{ snapshot.captured_at }}</dd></div><div><dt>上下文实际 UTF-8 字节</dt><dd>{{ snapshot.context_bytes }}</dd></div><div><dt>Token 估算</dt><dd>{{ snapshot.estimated_tokens }} · {{ snapshot.estimate_method }}（不是实际用量）</dd></div></dl>
      <p class="context-hash">上下文 SHA-256：{{ snapshot.context_sha256 }}</p>
      <p class="helper">provider 实际用量见运行计量；此字节估算不能推算费用。来源的采集/确认时间和本次输入冻结时间分别保留。</p>
      <details v-if="snapshot.manifest"><summary>文件快照 {{ snapshot.manifest.manifest_id }} · {{ snapshot.manifest.entries.length }} 项 · 原接受时间 {{ snapshot.manifest.created_at }}</summary><ul><li v-for="entry in snapshot.manifest.entries" :key="entry.index"><strong>{{ entry.path }} · 实际 L{{ entry.start_line }}–L{{ entry.end_line }} / 共 {{ entry.total_lines }} 行</strong><p>请求到 {{ entry.requested_end_line === null ? '文件末尾' : `L${entry.requested_end_line}` }}；收录 {{ entry.bytes }} 字节；采集 {{ entry.captured_at }}</p><p class="context-hash">完整文件：{{ entry.file_sha256 }}<br />收录行段：{{ entry.content_sha256 }}</p><p>截断：{{ entry.truncation_reasons.length ? entry.truncation_reasons.join(', ') : '无' }}（不代表文件目前未变）</p></li></ul></details>
      <details v-if="snapshot.notes.length"><summary>固定笔记版本 · {{ snapshot.notes.length }} 项</summary><ul><li v-for="note in snapshot.notes" :key="`${note.note_id}:${note.revision}`"><strong>{{ note.title }} · {{ note.kind }} · r{{ note.revision }}</strong><p>{{ note.note_id }} · {{ note.scope_work_order_id ? `工作单 ${note.scope_work_order_id}` : '项目' }} · 确认 {{ note.confirmed_at }}</p><p class="context-hash">正文 SHA-256：{{ note.body_sha256 }}</p><pre>{{ note.body }}</pre><pre aria-label="确认时保存的来源">{{ JSON.stringify(note.sources, null, 2) }}</pre></li></ul></details>
      <details v-if="snapshot.predecessors.length"><summary>固定直接前驱结果 · {{ snapshot.predecessors.length }} 项</summary><ul><li v-for="parent in snapshot.predecessors" :key="parent.attempt_id"><strong>{{ parent.task_id }} · execution r{{ parent.execution_revision }}</strong><p>工作单 {{ parent.work_order_id }} · attempt {{ parent.attempt_id }} · run {{ parent.run_id }}</p><p>冻结时实际状态 {{ parent.status }} · lease 已排空 {{ parent.lease_drained ? '是' : '否' }} · 结束 {{ parent.result_finished_at }}</p><p class="context-hash">完整结果 {{ parent.result_bytes }} 字节：{{ parent.result_sha256 }}<br />收录 {{ parent.included_bytes }} 字节：{{ parent.included_sha256 }}</p><p>截断：{{ parent.truncation_reasons.length ? parent.truncation_reasons.join(', ') : '无' }}</p><pre>{{ parent.text }}</pre></li></ul></details>
      <details><summary>查看此 run 保存的完整上下文文本（含数据边界）</summary><pre>{{ snapshot.rendered_context || '已显式选择空上下文；没有附加文本。' }}</pre></details>
    </template>
  </section>
</template>
