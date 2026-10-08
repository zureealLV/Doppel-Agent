<script setup lang="ts">
import { Folder, Home, ListTodo, GitCompareArrows, Blocks, Activity, History } from "lucide-vue-next";
import type { WorkspacePage } from "../navigation";
import type { ProjectIdentity } from "../projects";
import HelpMenu from "./HelpMenu.vue";

defineProps<{ page: WorkspacePage; closing: boolean; project: ProjectIdentity | null; projectError: boolean }>();
const emit = defineEmits<{ navigate: [page: WorkspacePage] }>();
const pages = [
  { page: "conversations" as const, label: "工作台", icon: Home },
  { page: "orders" as const, label: "计划与任务", icon: ListTodo },
  { page: "changes" as const, label: "变更与验证", icon: GitCompareArrows },
  { page: "extensions" as const, label: "扩展中心", icon: Blocks },
  { page: "runtime" as const, label: "独立 Runtime", icon: Activity },
  { page: "legacy" as const, label: "Legacy 历史", icon: History },
];
</script>

<template>
  <div class="app-frame workbench-shell classic-shell codex-shell">
    <header class="topbar classic-titlebar pywebview-drag-region">
      <button type="button" class="workbench-home classic-window-title no-drag" :disabled="closing" aria-label="返回编码工作台" :title="project?.path" @click="emit('navigate', 'conversations')">
        <Folder :size="14" aria-hidden="true" /><strong>Doppel-Agent</strong>
        <span class="project-identity" aria-label="当前项目">{{ project?.name || (projectError ? '无法读取当前项目' : '正在读取当前项目…') }}</span>
      </button>
      <div class="codex-window-actions no-drag"><HelpMenu :blocked="closing" /><slot name="status" /><slot name="window-controls" /></div>
    </header>
    <div class="codex-body">
      <nav class="codex-activity-rail" aria-label="工作区菜单" :inert="closing || undefined">
        <button v-for="item in pages" :key="item.page" type="button" class="rail-button" :disabled="closing" :aria-pressed="page === item.page" :aria-label="item.label" :title="item.label" @click="emit('navigate', item.page)">
          <component :is="item.icon" :size="19" aria-hidden="true" /><span class="sr-only">{{ item.label }}</span>
        </button>
        <span class="sr-only">高级入口：独立运行和旧版历史保留，不转换引擎状态。</span>
        <slot name="projects" />
        <div class="codex-rail-bottom"><slot name="context" /></div>
      </nav>
      <div class="codex-content"><slot /></div>
    </div>
  </div>
</template>
