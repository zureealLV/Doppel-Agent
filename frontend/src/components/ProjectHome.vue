<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { FolderOpen } from "lucide-vue-next";
import { ProjectController, type ProjectDesktopApi } from "../projectController";

const props = defineProps<{ blocked: boolean; beforeSwitch: () => Promise<void>; afterSwitch: (navigating: boolean) => void }>();
const controller = new ProjectController({
  beforeSwitch: () => props.beforeSwitch(),
  afterSwitch: navigating => props.afterSwitch(navigating),
  navigate: url => window.location.assign(url),
});
const state = controller.state;
const dialog = ref<HTMLDialogElement | null>(null);
const selected = ref("");
const confirmed = ref(false);
function ready() { controller.attach((window as Window & { pywebview?: { api?: Partial<ProjectDesktopApi> } }).pywebview?.api); }
async function open() {
  if (props.blocked || state.busy) return;
  confirmed.value = false; selected.value = "";
  dialog.value?.showModal(); await controller.refresh();
}
function close() { if (!state.busy) dialog.value?.close(); }
onMounted(() => { ready(); window.addEventListener("pywebviewready", ready); });
onBeforeUnmount(() => { window.removeEventListener("pywebviewready", ready); controller.attach(); });
</script>

<template>
  <div class="project-home no-drag">
    <button type="button" class="project-open" title="项目" aria-label="项目" :disabled="blocked || !state.ready || state.busy" @click="open"><FolderOpen :size="18" aria-hidden="true" /><span class="sr-only">项目</span></button>
    <dialog ref="dialog" class="workspace-dialog project-dialog" aria-labelledby="project-dialog-title" @cancel="state.busy && $event.preventDefault()">
      <div class="workspace-dialog-content">
        <div class="section-heading"><h2 id="project-dialog-title">打开本地项目</h2><button type="button" :disabled="state.busy" @click="close">关闭</button></div>
        <p>一个窗口只有一个活动项目。目录选择仅登记最近项目，不会执行任务。</p>
        <div class="button-row"><button type="button" :disabled="state.busy" @click="controller.choose()">选择目录</button><button type="button" :disabled="state.busy" @click="controller.refresh()">刷新最近项目</button></div>
        <fieldset class="recent-projects" :disabled="state.busy"><legend>最近项目</legend>
          <label v-for="item in state.projects" :key="item.project_id"><input v-model="selected" type="radio" name="recent-project" :value="item.project_id" /><span><strong>{{ item.name }}</strong><small>{{ item.path }}</small></span></label>
          <p v-if="!state.projects.length">暂无最近项目，请先选择目录。</p>
        </fieldset>
        <label class="project-confirm"><input v-model="confirmed" type="checkbox" :disabled="state.busy" />我确认：切换会取消并排空原生运行；旧版任务须先结束，未发送草稿不会自动保存。</label>
        <p>选择先保存，运行与 IO 清理完成后才启动新项目；写入/命令权限不会继承。</p>
        <button type="button" class="primary-button" :disabled="!selected || !confirmed || state.busy" @click="controller.switchTo(selected, confirmed)">保存选择并切换</button>
        <p v-if="state.switching" role="status">正在保存选择并清理旧项目，请等待…</p>
        <p v-if="state.error" role="alert">{{ state.error }}</p>
      </div>
    </dialog>
  </div>
</template>
