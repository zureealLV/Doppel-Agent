<script setup lang="ts">
import { codingTemplates } from "../codingTemplates";
defineProps<{ blocked: boolean; hasDraft: boolean }>();
const emit = defineEmits<{ choose: [prompt: string] }>();
</script>

<template>
  <section class="coding-templates" aria-label="编码任务模板">
    <div class="button-row"><button v-for="item in codingTemplates" :key="item.id" type="button"
      :disabled="blocked || hasDraft" @click="emit('choose', item.prompt)">{{ item.title }}</button></div>
    <p class="helper">仅填入提示词，不自动发送或授予权限；已有草稿时禁用，避免覆盖。</p>
  </section>
</template>
