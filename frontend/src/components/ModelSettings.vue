<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";

import { workspaceApi } from "../workspaceApi";
import { profileConfiguration } from "../workspace";
import type { ModelProfile, PublicSettings } from "../workspaceTypes";
import { canonicalTariff, emptyTariffEdit, tariffEditFromSaved, tariffErrorMessage, type BillingTariff } from '../billingTariff';
import { modelSettingsLifetimeKey, type ModelSettingsReply } from '../modelSettingsLifetime';

const props = defineProps<{ settings: PublicSettings }>();
const emit = defineEmits<{ close: []; changed: [settings: PublicSettings] }>();
const owner = inject(modelSettingsLifetimeKey);
if (!owner) throw new Error('settings_owner_missing'); // Production MUST use the original App owner, never a dialog-local flight.
const lifetime = owner, session = lifetime.attach();
const dialog = ref<HTMLDialogElement | null>(null);
const profileId = ref("");
const busy = ref(false);
const blocked = computed(() => busy.value || lifetime.state.busy || lifetime.state.closing || lifetime.state.uncertain || lifetime.state.deferred);
const replyBlocked = computed(() => busy.value || lifetime.state.busy || lifetime.state.closing);
const visibleKey = ref(false);
const message = ref("");
const savedProfile = shallowRef<ModelProfile | null>(null);
const savedTariff = ref<BillingTariff | null>(null);
const form = reactive({ name: "", preset: "openai", model: "", base_url: "", input_price: 0, output_price: 0, api_key: "",
  billing_tariff_edit: emptyTariffEdit() });

// Filter keys BEFORE reading values: Object.entries also reads `confirmed`,
// making its own checkbox trigger this synchronous consent-reset watcher.
watch(() => [form.preset, form.model, form.base_url, ...Object.keys(form.billing_tariff_edit)
  .filter(key => key !== 'confirmed').map(key => form.billing_tariff_edit[key as keyof typeof form.billing_tariff_edit])], () => {
  form.billing_tariff_edit.confirmed = false;
}, { flush: 'sync' }); // Price/model identity edits cannot reuse prior consent.

function fill(id: string, settings: PublicSettings = props.settings): void {
  form.api_key = "";
  visibleKey.value = false;
  profileId.value = id;
  const profile = settings.profiles.find((item) => item.id === id);
  savedProfile.value = profile ?? null;
  try { savedTariff.value = canonicalTariff(profile?.billing_tariff); }
  catch { savedTariff.value = null; }
  form.billing_tariff_edit = tariffEditFromSaved(profile?.billing_tariff);
  Object.assign(form, profile ? { name: profile.name, preset: profile.preset, model: profile.model, base_url: profile.base_url,
    input_price: profile.input_price, output_price: profile.output_price } : {
    name: "新模型", preset: "openai", model: "", base_url: "", input_price: 0, output_price: 0 });
  message.value = "";
}

function close(): void {
  if (busy.value || lifetime.state.busy) { message.value = '设置请求仍在进行，不能提前丢弃原请求。'; return; }
  form.api_key = ""; emit("close");
}

function applyReply(reply: ModelSettingsReply, adopted = false): void {
  form.api_key = ''; visibleKey.value = false; form.billing_tariff_edit.confirmed = false;
  if (reply.kind === 'probe') { message.value = `${adopted ? `原 ${reply.profileId} 的迟到测试回复：` : ''}${reply.value.reply}`; return; }
  emit('changed', reply.value);
  if (reply.kind === 'forget' && !adopted && profileId.value === reply.profileId) {
    savedProfile.value = reply.value.profiles.find(item => item.id === reply.profileId) ?? null;
  } else fill(reply.value.active_profile_id, reply.value);
  message.value = reply.kind === 'read_settings' ? '只读查看当前设置，不重发保存或测试；旧请求结果仍不据此证明，本窗口未知门禁不解除。'
    : reply.kind === 'forget' ? 'Key 已移除。' : reply.kind === 'delete' ? '模型档案已删除。' : '设置已保存；输入框不会回显已保存的 Key。';
}
function receiveReply(explicit = false): void {
  if (!lifetime.isActive(session)) return;
  const reply = lifetime.takeReply(session, explicit);
  if (reply) applyReply(reply, explicit);
  else message.value = lifetime.state.error || '原回复已由 App 保留；需明确采用，不会自动更新设置。';
}
async function readCurrent(): Promise<void> {
  if (busy.value || !lifetime.canRead(session)) return;
  busy.value = true;
  try { await lifetime.perform(session, { kind: 'read_settings', profileId: profileId.value, invoke: () => workspaceApi.settings() }); receiveReply(); }
  finally { form.api_key = ''; visibleKey.value = false; busy.value = false; }
}

async function save(forget = false): Promise<void> {
  if (busy.value || !lifetime.canMutate(session)) return;
  busy.value = true;
  try {
    // A key-only action cannot accidentally save unsaved identity or tariff.
    const original = savedProfile.value;
    if (forget && !original) throw new Error('profile_missing');
    const config = forget ? { ...profileConfiguration({ ...original!, api_key: '' }, 'forget_key'), provider: original!.provider }
      : profileConfiguration(form);
    form.billing_tariff_edit.confirmed = false; // Consume before IO, also on unknown/failed reply.
    const source = profileId.value;
    await lifetime.perform(session, { kind: forget ? 'forget' : 'save', profileId: source,
      invoke: () => workspaceApi.saveProfile(source, config, forget) });
    receiveReply();
  } catch (error) { message.value = tariffErrorMessage(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

async function probe(): Promise<void> {
  if (busy.value || !lifetime.canMutate(session)) return;
  busy.value = true;
  message.value = "正在发送显式连接测试…";
  try {
    const config = profileConfiguration(form, 'probe'); // No declaration/reference/consent in a paid probe.
    const source = profileId.value, body = { ...config, ...(source !== '__new__' ? { profile_id: source } : {}) };
    await lifetime.perform(session, { kind: 'probe', profileId: source, invoke: () => workspaceApi.probe(body) }); receiveReply();
  } catch (error) { message.value = tariffErrorMessage(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

async function remove(): Promise<void> {
  if (busy.value || !lifetime.canMutate(session) || profileId.value === "__new__" || !window.confirm("删除这个模型档案？至少保留一个档案。")) return;
  busy.value = true;
  try {
    const source = profileId.value;
    await lifetime.perform(session, { kind: 'delete', profileId: source, invoke: () => workspaceApi.deleteProfile(source) }); receiveReply();
  } catch (error) { message.value = tariffErrorMessage(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

onMounted(() => { fill(props.settings.active_profile_id); dialog.value?.showModal(); });
onBeforeUnmount(() => { lifetime.detach(session); form.api_key = ""; form.billing_tariff_edit = emptyTariffEdit(); savedTariff.value = null; savedProfile.value = null; dialog.value?.close(); });
</script>

<template>
  <dialog ref="dialog" class="workspace-dialog" aria-labelledby="model-settings-title" @cancel.prevent="close">
    <form class="workspace-dialog-content" autocomplete="off" @submit.prevent="save()">
      <header class="section-heading"><div><p class="eyebrow">MODEL PROFILES</p><h2 id="model-settings-title">模型与 API 设置</h2></div><button type="button" class="icon-button" :disabled="busy || lifetime.state.busy" aria-label="关闭模型设置" @click="close">×</button></header>
      <p class="helper">密钥保护：{{ props.settings.key_protection }}。密钥只用于本次保存/测试请求，不写浏览器存储。连接测试可能产生模型费用，不会自动运行。</p>
      <p v-if="lifetime.state.busy" role="status">原 {{ lifetime.state.lastAction }} 请求仍由 App 保留；隐藏／卸载弹窗不撤销请求，关闭或切项目等待同一原请求。</p>
      <p v-if="lifetime.state.uncertain" role="alert">原 {{ lifetime.state.uncertainAction }} / {{ lifetime.state.uncertainProfileId }} 请求结果未知；本窗口不重试保存／删除／测试，只读查看不证明旧请求成功或未执行。实际主机资源排空和持久未知恢复仍需独立确认。</p>
      <p v-if="lifetime.state.deferred" role="status">原 {{ lifetime.state.lastAction }} / {{ lifetime.state.sourceProfileId }} 的迟到回复已保留；不自动更新设置、不重发。明确采用设置回复会替换当前本地草稿并清空 Key。</p>
      <div class="button-row"><button v-if="lifetime.state.deferred" type="button" :disabled="replyBlocked" @click="receiveReply(true)">明确显示原请求的迟到回复（不重发）</button>
        <button v-if="lifetime.state.uncertain" type="button" :disabled="replyBlocked || lifetime.state.deferred" @click="readCurrent">明确只读查看当前设置（不解除未知门禁）</button></div>
      <fieldset :disabled="blocked">
        <div class="inline-form"><label class="sr-only" for="profile-selector">模型档案</label><select id="profile-selector" :value="profileId" @change="fill(($event.target as HTMLSelectElement).value)"><option v-for="profile in props.settings.profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option><option v-if="profileId === '__new__'" value="__new__">新模型</option></select><button type="button" @click="fill('__new__')">新建</button><button type="button" class="danger-button" :disabled="props.settings.profiles.length <= 1 || profileId === '__new__'" @click="remove">删除</button></div>
        <div class="field-grid settings-fields">
          <label><span class="field-label">档案名称</span><input v-model="form.name" maxlength="60" required /></label>
          <label><span class="field-label">服务预设</span><select id="profile-preset" v-model="form.preset"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI-compatible</option><option value="local">本地服务</option><option value="mock">离线 Mock</option></select></label>
          <label><span class="field-label">模型名称</span><input id="profile-model" v-model="form.model" :required="form.preset !== 'mock'" /></label>
          <label><span class="field-label">API Base URL</span><input v-model="form.base_url" type="url" :required="form.preset !== 'mock'" /></label>
          <label><span class="field-label">旧兼容输入数值（不是计费声明）</span><input v-model.number="form.input_price" type="number" min="0" step="any" /></label>
          <label><span class="field-label">旧兼容输出数值（不是计费声明）</span><input v-model.number="form.output_price" type="number" min="0" step="any" /></label>
        </div>
        <p class="helper">单价配置不是历史账单回执：未固定币种、日期与来源，默认 0 不代表免费。历史报告只用已封存价格凭据；凭据缺失时费用仍未知，不把当前档案价格套回旧运行，也不承诺账户费用上限。</p>
        <section class="tariff-settings" aria-labelledby="tariff-title">
          <h3 id="tariff-title">固定价格声明</h3>
          <p class="helper">仅用于后续运行的声明估算，不是官方核验、完整账单或账户 cap；不自动换汇。来源参考只随明确保存发送至私有设置，不用于连接测试或脱敏报告。请勿填入密钥。</p>
          <div v-if="savedTariff" class="tariff-saved">
            <p>已存声明：{{ savedTariff.currency }} / 1M Token · 生效日 {{ savedTariff.effective_date }} · {{ savedTariff.billing_basis }} · reasoning 已含于输出。</p>
            <p>来源类型 {{ savedTariff.source_kind }}（未核验）；私有参考：{{ savedTariff.source_reference }}</p>
            <p>输入 {{ savedTariff.rates.input ?? '分区计费' }} · 缓存命中 {{ savedTariff.rates.cached_input ?? '不单列' }} · 未命中 {{ savedTariff.rates.uncached_input ?? '不单列' }} · 输出 {{ savedTariff.rates.output }}。显式 0 也不是账户免费保证。</p>
          </div>
          <p v-else class="helper">无有效价格声明：费用未知。旧数值／默认 0 不升级为价格。</p>
          <label><span class="field-label">本次价格操作</span><select id="tariff-action" v-model="form.billing_tariff_edit.action">
            <option value="preserve">保留声明（模型／服务地址身份变化时清空）</option><option value="replace">明确替换／新增声明</option><option value="clear">明确清除声明，恢复未知</option>
          </select></label>
          <div v-if="form.billing_tariff_edit.action === 'replace'">
            <div class="field-grid settings-fields">
              <label><span class="field-label">币种（不换汇）</span><select v-model="form.billing_tariff_edit.currency" required><option value="">请选择</option><option v-for="currency in ['CNY', 'USD', 'EUR', 'GBP', 'JPY']" :key="currency" :value="currency">{{ currency }}</option></select></label>
              <label><span class="field-label">价格生效日期</span><input v-model="form.billing_tariff_edit.effective_date" type="date" min="0001-01-01" max="9999-12-31" required /></label>
              <label><span class="field-label">声明来源（均未核验）</span><select v-model="form.billing_tariff_edit.source_kind" required><option value="">请选择</option><option value="user_declared">用户声明</option><option value="offline_fixture">离线 fixture</option><option value="official_reference">官方参考声明（未核验）</option></select></label>
              <label><span class="field-label">计费口径 · 每 1M Token</span><select v-model="form.billing_tariff_edit.billing_basis" required><option value="">请选择</option><option value="input_output_inclusive">全部输入含缓存＋输出</option><option value="cache_partition_output_inclusive">实际缓存命中／未命中＋输出</option></select></label>
              <label><span class="field-label">Reasoning 口径</span><select v-model="form.billing_tariff_edit.reasoning_basis" required><option value="">请明确选择</option><option value="included_in_output">已含于输出，不再次收费</option></select></label>
              <label v-if="form.billing_tariff_edit.billing_basis === 'input_output_inclusive'"><span class="field-label">全部输入单价（十进制字符串）</span><input v-model="form.billing_tariff_edit.input" type="text" inputmode="decimal" maxlength="18" required /></label>
              <template v-if="form.billing_tariff_edit.billing_basis === 'cache_partition_output_inclusive'">
                <label><span class="field-label">缓存命中单价</span><input v-model="form.billing_tariff_edit.cached_input" type="text" inputmode="decimal" maxlength="18" required /></label>
                <label><span class="field-label">未命中单价</span><input v-model="form.billing_tariff_edit.uncached_input" type="text" inputmode="decimal" maxlength="18" required /></label>
              </template>
              <label><span class="field-label">输出单价（已含 reasoning）</span><input id="tariff-output" v-model="form.billing_tariff_edit.output" type="text" inputmode="decimal" maxlength="18" required /></label>
            </div>
            <label><span class="field-label">私有来源参考（不填密钥，不自动访问）</span><input v-model="form.billing_tariff_edit.source_reference" type="text" maxlength="2048" required /></label>
            <p class="helper">最多 8 位小数，不接受指数／负数／NaN。缓存分区必须同时观测到实际 hit/miss，否则估算未知；reasoning 不重复收费。未来生效声明在当前运行保持未知。保存不重算历史。</p>
            <label class="tariff-confirm"><input v-model="form.billing_tariff_edit.confirmed" type="checkbox" /><span>我确认本次声明对应当前模型／服务地址，币种、来源、日期、每百万单位及 cache/reasoning 口径均由我明确声明；这是估算，不是已核验账单或账户上限。</span></label>
          </div>
        </section>
        <label class="field-label" for="profile-key">API Key · {{ props.settings.profiles.find(item => item.id === profileId)?.api_key_saved ? '已安全保存，留空保留原 Key' : '尚未保存' }}</label>
        <div class="inline-form"><input id="profile-key" v-model="form.api_key" :type="visibleKey ? 'text' : 'password'" autocomplete="off" spellcheck="false" /><button type="button" :aria-pressed="visibleKey" @click="visibleKey = !visibleKey">{{ visibleKey ? '隐藏' : '显示' }}</button></div>
      </fieldset>
      <p role="status" aria-live="polite">{{ message }}</p>
      <footer class="button-row"><button type="button" :disabled="blocked || profileId === '__new__'" @click="save(true)">移除 Key</button><button type="button" :disabled="blocked" @click="probe">测试连接</button><button type="submit" class="primary-button" :disabled="blocked">{{ busy ? '请求中…' : '保存' }}</button></footer>
    </form>
  </dialog>
</template>
