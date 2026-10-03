<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";

import { workspaceApi } from "../workspaceApi";
import { profileConfiguration } from "../workspace";
import type { PublicSettings } from "../workspaceTypes";

const props = defineProps<{ settings: PublicSettings }>();
const emit = defineEmits<{ close: []; changed: [settings: PublicSettings] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const profileId = ref("");
const busy = ref(false);
const visibleKey = ref(false);
const message = ref("");
const form = reactive({ name: "", preset: "openai", model: "", base_url: "", input_price: 0, output_price: 0, api_key: "" });

function fill(id: string): void {
  form.api_key = "";
  visibleKey.value = false;
  profileId.value = id;
  const profile = props.settings.profiles.find((item) => item.id === id);
  Object.assign(form, profile ? { name: profile.name, preset: profile.preset, model: profile.model, base_url: profile.base_url,
    input_price: profile.input_price, output_price: profile.output_price } : {
    name: "新模型", preset: "openai", model: "", base_url: "", input_price: 0, output_price: 0 });
  message.value = "";
}

function close(): void { form.api_key = ""; emit("close"); }

async function save(forget = false): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  try {
    const settings = await workspaceApi.saveProfile(profileId.value, profileConfiguration(form), forget);
    emit("changed", settings);
    profileId.value = settings.active_profile_id;
    message.value = forget ? "Key 已移除。" : "设置已保存；输入框不会回显已保存的 Key。";
  } catch (error) { message.value = error instanceof Error ? error.message : String(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

async function probe(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  message.value = "正在发送显式连接测试…";
  try {
    const config = profileConfiguration(form);
    const result = await workspaceApi.probe({ ...config, ...(profileId.value !== "__new__" ? { profile_id: profileId.value } : {}) });
    message.value = result.reply;
  } catch (error) { message.value = error instanceof Error ? error.message : String(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

async function remove(): Promise<void> {
  if (busy.value || profileId.value === "__new__" || !window.confirm("删除这个模型档案？至少保留一个档案。")) return;
  busy.value = true;
  try {
    const settings = await workspaceApi.deleteProfile(profileId.value);
    emit("changed", settings);
    const profile = settings.profiles.find((item) => item.id === settings.active_profile_id)!;
    profileId.value = profile.id;
    Object.assign(form, { name: profile.name, preset: profile.preset, model: profile.model, base_url: profile.base_url,
      input_price: profile.input_price, output_price: profile.output_price, api_key: "" });
    message.value = "模型档案已删除。";
  } catch (error) { message.value = error instanceof Error ? error.message : String(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

onMounted(() => { fill(props.settings.active_profile_id); dialog.value?.showModal(); });
onBeforeUnmount(() => { form.api_key = ""; dialog.value?.close(); });
</script>

<template>
  <dialog ref="dialog" class="workspace-dialog" aria-labelledby="model-settings-title" @cancel.prevent="close">
    <form class="workspace-dialog-content" autocomplete="off" @submit.prevent="save()">
      <header class="section-heading"><div><p class="eyebrow">MODEL PROFILES</p><h2 id="model-settings-title">模型与 API 设置</h2></div><button type="button" class="icon-button" aria-label="关闭模型设置" @click="close">×</button></header>
      <p class="helper">密钥保护：{{ props.settings.key_protection }}。密钥只用于本次保存/测试请求，不写浏览器存储。连接测试可能产生模型费用，不会自动运行。</p>
      <fieldset :disabled="busy">
        <div class="inline-form"><label class="sr-only" for="profile-selector">模型档案</label><select id="profile-selector" :value="profileId" @change="fill(($event.target as HTMLSelectElement).value)"><option v-for="profile in props.settings.profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option><option v-if="profileId === '__new__'" value="__new__">新模型</option></select><button type="button" @click="fill('__new__')">新建</button><button type="button" class="danger-button" :disabled="props.settings.profiles.length <= 1 || profileId === '__new__'" @click="remove">删除</button></div>
        <div class="field-grid settings-fields">
          <label><span class="field-label">档案名称</span><input v-model="form.name" maxlength="60" required /></label>
          <label><span class="field-label">服务预设</span><select v-model="form.preset"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI-compatible</option><option value="local">本地服务</option><option value="mock">离线 Mock</option></select></label>
          <label><span class="field-label">模型名称</span><input v-model="form.model" :required="form.preset !== 'mock'" /></label>
          <label><span class="field-label">API Base URL</span><input v-model="form.base_url" type="url" :required="form.preset !== 'mock'" /></label>
          <label><span class="field-label">输入单价 / 1M Token</span><input v-model.number="form.input_price" type="number" min="0" step="any" /></label>
          <label><span class="field-label">输出单价 / 1M Token</span><input v-model.number="form.output_price" type="number" min="0" step="any" /></label>
        </div>
        <label class="field-label" for="profile-key">API Key · {{ props.settings.profiles.find(item => item.id === profileId)?.api_key_saved ? '已安全保存，留空保留原 Key' : '尚未保存' }}</label>
        <div class="inline-form"><input id="profile-key" v-model="form.api_key" :type="visibleKey ? 'text' : 'password'" autocomplete="off" spellcheck="false" /><button type="button" :aria-pressed="visibleKey" @click="visibleKey = !visibleKey">{{ visibleKey ? '隐藏' : '显示' }}</button></div>
      </fieldset>
      <p role="status" aria-live="polite">{{ message }}</p>
      <footer class="button-row"><button type="button" :disabled="busy || profileId === '__new__'" @click="save(true)">移除 Key</button><button type="button" :disabled="busy" @click="probe">测试连接</button><button type="submit" class="primary-button" :disabled="busy">{{ busy ? '请求中…' : '保存' }}</button></footer>
    </form>
  </dialog>
</template>
