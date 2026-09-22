<script setup lang="ts">
import { Check, FilePenLine, ShieldAlert, X } from "lucide-vue-next";
import { computed, ref, watch } from "vue";

import type { InterruptRecord } from "../types";

const props = defineProps<{ interrupt: InterruptRecord; busy: boolean }>();
const emit = defineEmits<{ decide: [action: "approve" | "reject" | "edit", toolCalls?: Array<Record<string, unknown>>] }>();
const editing = ref(false);
const editValue = ref("");
const parseError = ref("");
const toolCalls = computed(() => props.interrupt.value.tool_calls ?? props.interrupt.value.action_requests ?? []);

watch(() => props.interrupt.id, () => {
  editing.value = false;
  editValue.value = JSON.stringify(toolCalls.value, null, 2);
}, { immediate: true });

function submitEdit(): void {
  try {
    const parsed = JSON.parse(editValue.value) as unknown;
    if (!Array.isArray(parsed)) throw new Error("必须是工具调用数组");
    parseError.value = "";
    emit("decide", "edit", parsed as Array<Record<string, unknown>>);
  } catch (error) {
    parseError.value = error instanceof Error ? error.message : "JSON 无效";
  }
}
</script>

<template>
  <section class="approval-card" aria-labelledby="approval-title">
    <div class="approval-title"><ShieldAlert :size="20" aria-hidden="true" /><div><p class="eyebrow">HUMAN-IN-THE-LOOP</p><h2 id="approval-title">等待批准</h2></div></div>
    <p>运行已在持久检查点暂停。批准、拒绝，或编辑工具调用后恢复。</p>
    <pre class="request-preview">{{ JSON.stringify(interrupt.value, null, 2) }}</pre>
    <div v-if="editing" class="edit-box">
      <label for="tool-calls-json">工具调用 JSON</label>
      <textarea id="tool-calls-json" v-model="editValue" rows="8" :aria-invalid="Boolean(parseError)" aria-describedby="edit-error" />
      <p v-if="parseError" id="edit-error" role="alert" class="field-error">{{ parseError }}</p>
      <div class="button-row"><button type="button" @click="editing = false">取消编辑</button><button type="button" class="primary-button" :disabled="busy" @click="submitEdit">提交修改</button></div>
    </div>
    <div v-else class="button-row">
      <button type="button" class="danger-button" :disabled="busy" @click="emit('decide', 'reject')"><X :size="17" aria-hidden="true" />拒绝</button>
      <button type="button" :disabled="busy" @click="editing = true"><FilePenLine :size="17" aria-hidden="true" />编辑</button>
      <button type="button" class="primary-button" :disabled="busy" @click="emit('decide', 'approve')"><Check :size="17" aria-hidden="true" />批准</button>
    </div>
  </section>
</template>
