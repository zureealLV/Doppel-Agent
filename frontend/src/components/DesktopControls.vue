<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";
import { Minus, Square, X } from "lucide-vue-next";
import { DesktopController, type DesktopApi } from "../desktopBridge";
const props = defineProps<{ beforeClose?: () => Promise<void>; afterClose?: (closed: boolean) => void }>();
const controller = new DesktopController({
  beforeClose: () => props.beforeClose?.() ?? Promise.resolve(),
  afterClose: closed => props.afterClose?.(closed),
});
const state = controller.state;
function ready() { controller.attach((window as Window & { pywebview?: { api?: DesktopApi } }).pywebview?.api); }
onMounted(() => { ready(); window.addEventListener("pywebviewready", ready); });
onBeforeUnmount(() => { window.removeEventListener("pywebviewready", ready); controller.attach(); });
</script>
<template>
  <div class="desktop-controls no-drag" role="group" aria-label="原生窗口控制">
    <button type="button" aria-label="最小化窗口" title="最小化窗口" :disabled="!state.ready || state.busy" @click="controller.act('minimize')"><Minus :size="16" /></button>
    <button type="button" aria-label="最大化或还原窗口" title="最大化或还原窗口" :disabled="!state.ready || state.busy" @click="controller.act('maximize')"><Square :size="14" /></button>
    <button type="button" aria-label="关闭窗口" title="关闭窗口" :disabled="!state.ready || state.busy" @click="controller.act('close')"><X :size="16" /></button>
    <span v-if="state.preparingClose" role="status">正在保存历史选择，稍后关闭…</span>
    <span v-if="state.error" role="alert">{{ state.error }}</span>
  </div>
</template>
