<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";

import ProviderConnectionEditor from './ProviderConnectionEditor.vue';
import { searchProviders, providerForEndpoint, type ProviderRecipe } from '../providerCatalog';
import { workspaceApi } from "../workspaceApi";
import { profileConfiguration } from "../workspace";
import type { ModelProfile, PublicSettings } from "../workspaceTypes";
import { canonicalTariff, emptyTariffEdit, tariffEditFromSaved, tariffErrorMessage, type BillingTariff } from '../billingTariff';
import { modelSettingsLifetimeKey, type ModelSettingsReply } from '../modelSettingsLifetime';

const props = defineProps<{ settings: PublicSettings; initialProfileId?: string; applyProfile?: (profileId: string) => Promise<void>; applyDisabled?: boolean }>();
const emit = defineEmits<{ close: []; changed: [settings: PublicSettings] }>();
const owner = inject(modelSettingsLifetimeKey);
if (!owner) throw new Error('settings_owner_missing'); // Production MUST use the original App owner, never a dialog-local flight.
const lifetime = owner, session = lifetime.attach();
const dialog = ref<HTMLDialogElement | null>(null);
const profileId = ref("");
const busy = ref(false);
const blocked = computed(() => busy.value || lifetime.state.busy || lifetime.state.closing || lifetime.state.uncertain || lifetime.state.deferred);
const replyBlocked = computed(() => busy.value || lifetime.state.busy || lifetime.state.closing);
type ProviderSection = 'saved' | 'cloud' | 'custom' | 'local';
const section = ref<ProviderSection>('saved');
const search = ref('');
const selectedProvider = shallowRef<ProviderRecipe>();
const catalog = computed(() => searchProviders(search.value, section.value === 'local' ? 'local' : 'cloud'));
const testStatus = ref<'untested' | 'passed' | 'failed'>('untested');
const visibleKey = ref(false);
const message = ref("");
const savedProfile = shallowRef<ModelProfile | null>(null);
const pendingSwitch = shallowRef<(() => void) | null>(null);
const baseline = ref('');
const savedTariff = ref<BillingTariff | null>(null);
const form = reactive({ name: "", preset: "openai", model: "", base_url: "", region: '', plan: '', input_price: 0, output_price: 0, api_key: "",
  billing_tariff_edit: emptyTariffEdit() });

watch(() => [form.preset, form.model, form.base_url, form.api_key], () => { testStatus.value = 'untested'; }, { flush: 'sync' });

function navigate(next: ProviderSection): void { if (!blocked.value) { section.value = next; search.value = ''; } }
function switchEditor(action: () => void): void {
  if (blocked.value) return;
  if (baseline.value && JSON.stringify(form) !== baseline.value) { pendingSwitch.value = action; return; }
  action();
}
function discardSwitch(): void { const action = pendingSwitch.value; pendingSwitch.value = null; form.api_key = ''; action?.(); }
function selectProfile(id: string): void { switchEditor(() => fill(id)); }
function addProvider(provider: ProviderRecipe): void { switchEditor(() => addProviderNow(provider)); }
function addProviderNow(provider: ProviderRecipe): void {
  if (blocked.value) return;
  fill('__new__');
  selectedProvider.value = provider;
  Object.assign(form, { name: provider.name, preset: provider.preset, base_url: provider.baseUrl, model: '' });
  baseline.value = JSON.stringify(form);
}
function newCustom(): void { switchEditor(() => { fill('__new__'); form.preset = 'custom'; baseline.value = JSON.stringify(form); }); }

// Filter keys BEFORE reading values: Object.entries also reads `confirmed`,
// making its own checkbox trigger this synchronous consent-reset watcher.
watch(() => [form.preset, form.model, form.base_url, ...Object.keys(form.billing_tariff_edit)
  .filter(key => key !== 'confirmed').map(key => form.billing_tariff_edit[key as keyof typeof form.billing_tariff_edit])], () => {
  form.billing_tariff_edit.confirmed = false;
}, { flush: 'sync' }); // Price/model identity edits cannot reuse prior consent.

function fill(id: string, settings: PublicSettings = props.settings): void {
  pendingSwitch.value = null;
  form.api_key = "";
  visibleKey.value = false;
  profileId.value = id;
  const profile = settings.profiles.find((item) => item.id === id);
  savedProfile.value = profile ?? null;
  selectedProvider.value = profile ? providerForEndpoint(profile.base_url) : undefined;
  testStatus.value = 'untested';
  try { savedTariff.value = canonicalTariff(profile?.billing_tariff); }
  catch { savedTariff.value = null; }
  form.billing_tariff_edit = tariffEditFromSaved(profile?.billing_tariff);
  Object.assign(form, profile ? { name: profile.name, preset: profile.preset, model: profile.model, base_url: profile.base_url,
    input_price: profile.input_price, output_price: profile.output_price, region: profile.region || '', plan: profile.plan || '' } : {
    name: "新模型", preset: "openai", model: "", base_url: "", region: '', plan: '', input_price: 0, output_price: 0 });
  message.value = ""; baseline.value = JSON.stringify(form);
}

function close(): void {
  if (busy.value || lifetime.state.busy) { message.value = '设置请求仍在进行，不能提前丢弃原请求。'; return; }
  form.api_key = ""; emit("close");
}

async function applyReply(reply: ModelSettingsReply, adopted = false): Promise<void> {
  form.api_key = ''; visibleKey.value = false; form.billing_tariff_edit.confirmed = false;
  if (reply.kind === 'probe') { testStatus.value = adopted ? 'untested' : reply.value.ok ? 'passed' : 'failed'; message.value = `${adopted ? `原 ${reply.profileId} 的迟到测试回复：` : ''}${reply.value.reply}`; return; }
  emit('changed', reply.value);
  if (reply.kind === 'forget' && !adopted && profileId.value === reply.profileId) {
    savedProfile.value = reply.value.profiles.find(item => item.id === reply.profileId) ?? null;
  } else fill(reply.value.saved_profile_id || (reply.profileId !== '__new__' && reply.value.profiles.some(p => p.id === reply.profileId) ? reply.profileId : reply.value.active_profile_id), reply.value);
  message.value = reply.kind === 'read_settings' ? '只读查看当前设置，不重发保存或测试；旧请求结果仍不据此证明，本窗口未知门禁不解除。'
    : reply.kind === 'forget' ? 'Key 已移除。' : reply.kind === 'delete' ? '模型档案已删除。' : reply.kind === 'save_default' ? '已保存并设为新对话默认；已有对话不变。' : '设置已保存；默认和已有对话不变，保存不代表测试通过。';
  if (reply.kind === 'save' && !adopted && props.applyProfile) await applySelected(true);
}
async function receiveReply(explicit = false): Promise<void> {
  if (!lifetime.isActive(session)) return;
  const reply = lifetime.takeReply(session, explicit);
  if (reply) await applyReply(reply, explicit);
  else message.value = lifetime.state.error || '原回复已由 App 保留；需明确采用，不会自动更新设置。';
}
async function applySelected(afterSave = false): Promise<void> {
  if (!lifetime.isActive(session) || !props.applyProfile) return;
  if (props.applyDisabled) { message.value = `${afterSave ? '已保存；' : ''}当前对话忙碌或只读，尚未应用。`; return; }
  const source = profileId.value;
  form.api_key = ''; visibleKey.value = false;
  try {
    await props.applyProfile(source);
    if (lifetime.isActive(session)) message.value = `${afterSave ? '已保存并应用到当前选择；' : '已应用已存档案（未保存草稿）；'}不发送模型请求，不代表测试通过。`;
  } catch {
    if (lifetime.isActive(session)) message.value = `${afterSave ? '已保存；' : ''}应用未确认，可能失败或结果未知；不自动重试，请核对当前对话的模型。`;
  }
}
async function useSaved(): Promise<void> {
  if (blocked.value || !lifetime.canMutate(session) || profileId.value === '__new__') return;
  busy.value = true;
  try { await applySelected(); } finally { busy.value = false; }
}

async function readCurrent(): Promise<void> {
  if (busy.value || !lifetime.canRead(session)) return;
  busy.value = true;
  try { await lifetime.perform(session, { kind: 'read_settings', profileId: profileId.value, invoke: () => workspaceApi.settings() }); await receiveReply(); }
  finally { form.api_key = ''; visibleKey.value = false; busy.value = false; }
}

async function save(forget = false, intent: 'save' | 'save_only' | 'save_default' = 'save'): Promise<void> {
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
    await lifetime.perform(session, { kind: forget ? 'forget' : intent, profileId: source,
      invoke: () => workspaceApi.saveProfile(source, config, forget, !forget && intent === 'save_default') });
    await receiveReply();
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
    await lifetime.perform(session, { kind: 'probe', profileId: source, invoke: () => workspaceApi.probe(body) }); await receiveReply();
  } catch (error) { message.value = tariffErrorMessage(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

async function remove(): Promise<void> {
  if (busy.value || !lifetime.canMutate(session) || profileId.value === "__new__" || !window.confirm("删除这个模型档案？至少保留一个档案。")) return;
  busy.value = true;
  try {
    const source = profileId.value;
    await lifetime.perform(session, { kind: 'delete', profileId: source, invoke: () => workspaceApi.deleteProfile(source) }); await receiveReply();
  } catch (error) { message.value = tariffErrorMessage(error); }
  finally { form.api_key = ""; visibleKey.value = false; busy.value = false; }
}

onMounted(() => { fill(props.initialProfileId || props.settings.active_profile_id); if (profileId.value === '__new__') section.value = 'cloud'; dialog.value?.showModal(); });
onBeforeUnmount(() => { lifetime.detach(session); form.api_key = ""; form.billing_tariff_edit = emptyTariffEdit(); savedTariff.value = null; savedProfile.value = null; dialog.value?.close(); });
</script>

<template>
  <dialog ref="dialog" class="workspace-dialog provider-center" aria-labelledby="model-settings-title" @cancel.prevent="close">
    <form class="workspace-dialog-content provider-center-content" autocomplete="off" @submit.prevent="save()">
      <header class="section-heading"><div><p class="eyebrow">PROVIDERS</p><h2 id="model-settings-title">提供商中心</h2></div><button type="button" class="icon-button" :disabled="busy || lifetime.state.busy" aria-label="关闭模型设置" @click="close">×</button></header>
      <details class="provider-security"><summary>密钥保护：{{ props.settings.key_protection }} · 仅显式操作联网 · 了解详情</summary><p class="helper">密钥保护：{{ props.settings.key_protection }}。密钥只用于本次保存/测试请求，不写浏览器存储。连接测试可能产生模型费用，不会自动运行。建议先保存，再测试；测试后未保存的 Key 会清空。</p></details>
      <p v-if="lifetime.state.busy" role="status">原 {{ lifetime.state.lastAction }} 请求仍由 App 保留；隐藏／卸载弹窗不撤销请求，关闭或切项目等待同一原请求。</p>
      <p v-if="lifetime.state.uncertain" role="alert">原 {{ lifetime.state.uncertainAction }} / {{ lifetime.state.uncertainProfileId }} 请求结果未知；本窗口不重试保存／删除／测试，只读查看不证明旧请求成功或未执行。实际主机资源排空和持久未知恢复仍需独立确认。</p>
      <p v-if="lifetime.state.deferred" role="status">原 {{ lifetime.state.lastAction }} / {{ lifetime.state.sourceProfileId }} 的迟到回复已保留；不自动更新设置、不重发。明确采用设置回复会替换当前本地草稿并清空 Key。</p>
      <div class="button-row"><button v-if="lifetime.state.deferred" type="button" :disabled="replyBlocked" @click="receiveReply(true)">明确显示原请求的迟到回复（不重发）</button>
        <button v-if="lifetime.state.uncertain" type="button" :disabled="replyBlocked || lifetime.state.deferred" @click="readCurrent">明确只读查看当前设置（不解除未知门禁）</button></div>
      <nav class="provider-sections" aria-label="提供商分组">
        <button v-for="item in [{id:'saved',name:'已配置'}, {id:'cloud',name:'API 密钥'}, {id:'custom',name:'自定义端点'}, {id:'local',name:'本地服务'}]" :key="item.id" type="button" :disabled="blocked" :aria-pressed="section === item.id" @click="navigate(item.id as ProviderSection)">{{ item.name }}</button>
      </nav>
      <div v-if="pendingSwitch" class="provider-unsaved" role="alert">有未保存修改。<button type="button" :disabled="blocked" @click="pendingSwitch = null">保留当前编辑</button><button type="button" :disabled="blocked" @click="discardSwitch">放弃并切换</button></div>
      <fieldset :disabled="blocked" class="provider-layout">
        <aside class="provider-sidebar" aria-label="提供商列表">
          <template v-if="section === 'saved' || section === 'custom'">
            <div class="provider-list-heading"><strong>{{ section === 'saved' ? '模型档案' : '自定义连接' }}</strong><button type="button" @click="newCustom">新建</button></div>
            <p v-if="section === 'custom'" class="helper">OpenAI-compatible Chat Completions。现有档案可直接编辑，不套用目录预设。</p>
            <button v-for="profile in props.settings.profiles" :key="profile.id" type="button" class="provider-list-item" :aria-pressed="profileId === profile.id" @click="selectProfile(profile.id)"><strong>{{ profile.name }}</strong><small>{{ profile.model || profile.preset }}</small><small>{{ profile.id === props.settings.active_profile_id ? '默认 · ' : '' }}已保存 · 未测试</small></button>
          </template>
          <template v-else-if="section === 'cloud' || section === 'local'">
            <label for="provider-search" class="field-label">搜索提供商</label><input id="provider-search" v-model="search" type="search" placeholder="名称或模型 ID" />
            <p class="helper">{{ section === 'local' ? '连接已运行的本地服务；不下载或安装模型。' : '官方预设，未经账户实测。选择后填 Key 和模型。' }}</p>
            <div class="provider-directory">
              <button v-for="provider in catalog" :key="provider.id" type="button" class="provider-list-item" :aria-pressed="selectedProvider?.id === provider.id && profileId === '__new__'" @click="addProvider(provider)"><strong>添加 {{ provider.name }}</strong><small>{{ provider.group === 'local' ? '本地服务' : 'API Key' }}</small></button>
              <p v-if="!catalog.length" class="helper">没有匹配的提供商，可使用自定义端点。</p>
            </div>
          </template>
          <p v-else class="helper">本版只支持 API Key／兼容端点，不提供 OAuth 登录，也不复用聊天产品订阅。</p>
        </aside>
        <ProviderConnectionEditor :form="form" :blocked="blocked" :settings="props.settings" :profile-id="profileId" :selected-provider="selectedProvider" :saved-profile="savedProfile" :saved-tariff="savedTariff" :test-status="testStatus" v-model:visible-key="visibleKey" @remove="remove" @forget="save(true)" />

      </fieldset>
      <details class="provider-more"><summary>更多连接方式</summary><p>账号授权未支持；不复用聊天产品订阅、Token 或 Cookie。本地仅连接已启动服务。</p></details>
      <p class="provider-boundary">仅 Chat Completions 兼容；预设／保存不代表账户实测或工具调用验证。</p>
      <p role="status" aria-live="polite">{{ message }}</p>
      <footer class="button-row provider-footer"><button type="button" :disabled="blocked" @click="probe">测试连接 · 可能计费</button><button type="submit" class="primary-button" :disabled="blocked">{{ busy ? '请求中…' : '保存并使用' }}</button><details><summary>更多操作</summary><button v-if="props.applyProfile" type="button" :disabled="blocked || props.applyDisabled || profileId === '__new__'" @click="useSaved">使用已存档案</button><button type="button" :disabled="blocked" @click="save(false, 'save_only')">仅保存</button><button type="button" :disabled="blocked" @click="save(false, 'save_default')">设为新对话默认</button></details></footer>
    </form>
  </dialog>
</template>
