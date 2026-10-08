<script setup lang="ts">
import type { ContextBinding } from "../workOrders";
defineProps<{ bindings?: ContextBinding[]; activeRevision: number }>();
</script>
<template>
  <section class="context-binding-receipt" aria-label="工作单已保存执行上下文绑定">
    <h3>已保存执行上下文 · 不是当前面板选择</h3>
    <p class="helper">按 execution revision 保留。修改未来节点不会改已开始节点或其重试输入；前驱结果在实际派发时另行固定，见真实 run 的输入回执。这里的记录不是来源目前未变或模型质量/验证通过的证明。</p>
    <p v-if="!bindings?.length" class="helper">没有文件/笔记上下文绑定回执（未显式绑定或旧记录）；不从当前面板补造。</p>
    <ul v-else><li v-for="binding in bindings" :key="binding.revision"><details><summary>execution r{{ binding.revision }}{{ binding.revision === activeRevision ? ' · 当前计划基底' : ' · 已开始节点保留' }} · {{ binding.bound_revision === binding.revision ? '本版本固定' : `沿用 r${binding.bound_revision} 固定快照` }}</summary>
      <p>固定输入 {{ binding.captured_at }} · {{ binding.context_bytes }} UTF-8 字节 · 估算 {{ binding.estimated_tokens }} tokens（{{ binding.estimate_method }}，不是实际用量或费用）</p>
      <p class="context-hash">上下文 SHA-256：{{ binding.context_sha256 }}</p>
      <p>文件 manifest：{{ binding.descriptor.manifest_id || '无' }}；笔记 {{ binding.descriptor.notes.length }} 项</p>
      <ul><li v-for="note in binding.descriptor.notes" :key="`${note.note_id}:${note.revision}`"><code>{{ note.note_id }}</code> · 固定 r{{ note.revision }}</li></ul>
      <p v-if="!binding.descriptor.manifest_id && !binding.descriptor.notes.length" class="helper">此处是显式空绑定，不是缺失回执。</p>
    </details></li></ul>
  </section>
</template>
