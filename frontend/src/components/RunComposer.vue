<script setup lang="ts">
import { Play, RotateCcw } from "lucide-vue-next";
import { reactive } from "vue";

import type { Effort, RunMode, RunRequest } from "../types";

const props = defineProps<{ busy: boolean }>();
const emit = defineEmits<{ submit: [request: RunRequest] }>();

const form = reactive<RunRequest>({
  prompt: "分析当前仓库结构，给出一条可验证的改进建议。",
  mode: "graph",
  effort: "balanced",
  deadline_seconds: 600,
  permissions: { workspace_write: false, command_execute: false, mcp_execute: false, delegate: false },
});

const modes: Array<{ value: RunMode; name: string; detail: string }> = [
  { value: "legacy", name: "Legacy", detail: "Core 兼容路径" },
  { value: "graph", name: "Graph", detail: "LangGraph 持久状态" },
  { value: "deep", name: "Deep", detail: "DeepAgent 编排" },
];
const efforts: Effort[] = ["quick", "balanced", "deep"];

function reset(): void {
  form.prompt = "分析当前仓库结构，给出一条可验证的改进建议。";
  form.mode = "graph";
  form.effort = "balanced";
  form.profile_id = undefined;
  Object.assign(form.permissions, { workspace_write: false, command_execute: false, mcp_execute: false, delegate: false });
}
</script>

<template>
  <form class="composer" @submit.prevent="emit('submit', JSON.parse(JSON.stringify(form)))">
    <div class="section-heading">
      <div><p class="eyebrow">CONTROL PLANE</p><h2>创建运行</h2></div>
      <button type="button" class="icon-button" aria-label="重置运行表单" title="重置" @click="reset"><RotateCcw :size="17" aria-hidden="true" /></button>
    </div>

    <fieldset>
      <legend>运行时</legend>
      <label v-for="mode in modes" :key="mode.value" class="mode-card" :class="{ active: form.mode === mode.value }">
        <input v-model="form.mode" type="radio" name="mode" :value="mode.value" />
        <span><strong>{{ mode.name }}</strong><small>{{ mode.detail }}</small></span>
      </label>
    </fieldset>

    <label class="field-label" for="prompt">任务提示词</label>
    <textarea id="prompt" v-model="form.prompt" rows="7" maxlength="100000" required aria-describedby="prompt-hint" />
    <small id="prompt-hint" class="helper">会发送到本机 API；运行事件将持久化并实时回放。</small>

    <div class="field-grid">
      <label><span class="field-label">推理力度</span><select v-model="form.effort"><option v-for="item in efforts" :key="item">{{ item }}</option></select></label>
      <label><span class="field-label">配置 ID</span><input v-model.trim="form.profile_id" placeholder="default" autocomplete="off" /></label>
    </div>

    <fieldset class="permissions">
      <legend>权限边界</legend>
      <label><input v-model="form.permissions.workspace_write" type="checkbox" /><span><strong>写入工作区</strong><small>允许生成补丁</small></span></label>
      <label><input v-model="form.permissions.command_execute" type="checkbox" /><span><strong>执行命令</strong><small>运行验证流水线</small></span></label>
      <label><input v-model="form.permissions.mcp_execute" type="checkbox" /><span><strong>MCP 工具</strong><small>调用已配置服务</small></span></label>
      <label><input v-model="form.permissions.delegate" type="checkbox" /><span><strong>子代理</strong><small>允许异步委派</small></span></label>
    </fieldset>

    <button class="primary-button" type="submit" :disabled="props.busy || !form.prompt.trim()">
      <Play :size="18" aria-hidden="true" />{{ props.busy ? "提交中…" : "启动运行" }}
    </button>
  </form>
</template>
