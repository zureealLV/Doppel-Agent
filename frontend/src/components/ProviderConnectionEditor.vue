<script setup lang="ts">
import { ref, watch } from 'vue';
import type { ProviderRecipe } from '../providerCatalog';
import type { ModelProfile, ProfileForm, PublicSettings } from '../workspaceTypes';
import type { BillingTariff, TariffEdit } from '../billingTariff';
const props = defineProps<{ form: ProfileForm & { billing_tariff_edit: TariffEdit }; settings: PublicSettings; profileId: string;
  selectedProvider?: ProviderRecipe; blocked: boolean; savedProfile: ModelProfile | null; savedTariff: BillingTariff | null; testStatus: 'untested' | 'passed' | 'failed' }>();
const visibleKey = defineModel<boolean>('visibleKey', { default: false });
const emit = defineEmits<{ remove: []; forget: [] }>();
const editAddress = ref(false);
watch(() => [props.selectedProvider?.id, props.profileId], () => { editAddress.value = false; });
</script>
<template>
        <section class="provider-editor" aria-label="连接配置">
          <div class="provider-editor-heading"><div><p class="eyebrow">{{ profileId === '__new__' ? 'NEW CONNECTION' : 'SAVED PROFILE' }}</p><h3>{{ selectedProvider?.name || '自定义 / 离线连接' }}</h3></div><span class="provider-state">{{ profileId === '__new__' ? '尚未保存' : '已保存档案' }} · {{ testStatus === 'passed' ? form.preset === 'mock' ? '离线 Mock 测试通过（非外部连接）' : '本次测试通过' : testStatus === 'failed' ? '本次测试未通过' : '当前配置未测试' }}</span></div>
          <template v-if="selectedProvider">
            <p class="helper">{{ selectedProvider.note }}</p>
            <p class="provider-source helper"><a :href="selectedProvider.sourceUrl" target="_blank" rel="noopener noreferrer">{{ selectedProvider.name }} 文档</a> · 核验 {{ selectedProvider.verifiedOn }}</p>
          </template>
        <label class="field-label" for="profile-key">API Key · {{ settings.profiles.find(item => item.id === profileId)?.api_key_saved ? '已安全保存，留空保留原 Key' : '尚未保存' }}</label>
        <div class="inline-form"><input id="profile-key" v-model="form.api_key" :type="visibleKey ? 'text' : 'password'" autocomplete="off" spellcheck="false" /><button type="button" :aria-pressed="visibleKey" @click="visibleKey = !visibleKey">{{ visibleKey ? '隐藏' : '显示' }}</button></div>
        <p v-if="selectedProvider?.authentication === 'optional-key'" class="helper">本地服务未启用认证时可留空；启用认证后需填写对应 Key。</p>
        <p v-if="selectedProvider" class="provider-service">服务：{{ selectedProvider.name }} <button type="button" :aria-expanded="editAddress" @click="editAddress = !editAddress">{{ editAddress ? '收起地址' : '编辑地址' }}</button></p>
        <div class="field-grid settings-fields">
          <label><span class="field-label">模型名称</span><input id="profile-model" v-model="form.model" :required="form.preset !== 'mock'" /></label>
          <label v-if="!selectedProvider || editAddress"><span class="field-label">API Base URL</span><input id="profile-base-url" v-model="form.base_url" type="url" :required="form.preset !== 'mock'" /></label>
        </div>
        <div v-if="selectedProvider?.models.length" class="provider-model-hints"><span class="field-label">建议模型（非实时清单，可手填）</span><button v-for="model in selectedProvider.models" :key="model" type="button" @click="form.model = model">{{ model }}</button></div>
        <details class="provider-advanced"><summary>高级 · 档案 / 协议 / 计费声明</summary>
          <label><span class="field-label">档案名称</span><input id="profile-name" v-model="form.name" maxlength="60" required /></label>
          <label><span class="field-label">服务预设</span><select id="profile-preset" v-model="form.preset"><option value="deepseek">DeepSeek</option><option value="openai">OpenAI-compatible</option><option value="custom">自定义提供商（OpenAI-compatible）</option><option value="local">本地服务</option><option value="mock">离线 Mock</option></select></label>
        <p class="helper">仅使用 OpenAI-compatible Chat Completions（及离线 Mock），不原生支持 Responses 或 Anthropic Messages。模型是否支持工具调用由实际服务决定。</p>
        <div class="field-grid settings-fields">          <label><span class="field-label">旧兼容输入数值（不是计费声明）</span><input v-model.number="form.input_price" type="number" min="0" step="any" /></label>
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
        </details>
        <details class="provider-management"><summary>管理凭据与档案</summary><button type="button" :disabled="blocked || profileId === '__new__'" @click="emit('forget')">移除 Key</button><button type="button" class="danger-button" :disabled="blocked || settings.profiles.length <= 1 || profileId === '__new__'" @click="emit('remove')">删除档案</button></details>
        </section>

</template>
