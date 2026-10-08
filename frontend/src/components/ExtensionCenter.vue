<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ExtensionController } from "../extensions";
const props = defineProps<{ active: boolean; blocked: boolean }>();
const controller = new ExtensionController(), state = controller.state;
const disabled = computed(() => !props.active || props.blocked || controller.selectionLocked);
const receiptDisabled = computed(() => !props.active || props.blocked || state.closing || state.busy);
const selected = computed(() => state.servers.find(server => server.name === state.selectedServer));
const selectedServer = computed({ get: () => state.selectedServer ?? "", set: value => { controller.selectServer(value); } });
const selectedAction = computed({ get: () => state.action, set: (value: "probe" | "refresh") => { controller.chooseAction(value); } });
const discoveryConsent = computed({ get: () => state.confirmed, set: value => { controller.confirmDiscovery(value); } });
const reloadConsent = computed({ get: () => state.reloadConfirmed, set: value => { controller.confirmSkillReload(value); } });
const exitConsent = ref(false);
const actionLabel = (action: string) => action === "probe" ? "探测协议" : action === "refresh" ? "刷新工具目录" : "重新加载 Skills";
watch(() => [props.active, props.blocked] as const, () => {
  exitConsent.value = false; controller.activate(props.active && !props.blocked);
}, { immediate: true, flush: "sync" });
watch(() => [state.busy, state.intent, state.uncertain], () => { exitConsent.value = false; }, { flush: "sync" });
function acknowledgeExit() {
  if (receiptDisabled.value || !exitConsent.value) return;
  controller.acknowledgeExit(true); exitConsent.value = false;
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (controller.unloadBlocked) { event.preventDefault(); event.returnValue = ""; }
}
onMounted(() => { window.addEventListener("beforeunload", beforeUnload); });
onBeforeUnmount(() => { window.removeEventListener("beforeunload", beforeUnload); controller.dispose(); });
function finishClose() { controller.finishClose(); controller.activate(props.active && !props.blocked); }
defineExpose({ prepareClose: () => controller.prepareClose(), finishClose });
</script>

<template>
  <section class="extensions-workspace" aria-labelledby="extensions-heading" :inert="blocked || undefined">
    <header class="extensions-heading">
      <div><p class="extensions-eyebrow">LOCAL EXTENSIONS</p><h1 id="extensions-heading">扩展中心</h1></div>
      <details class="page-explanation"><summary aria-label="扩展中心说明" title="扩展中心说明">说明</summary><p>进入页面不自动连接、读取目录或重试。这里只展示能力，不安装扩展、执行工具或替运行授权。</p></details>
    </header>
    <p class="extension-boundary">目录文本敏感且不可信、未全局脱敏；仅显示纯文本，不作为实时健康、安全或工具效果证明。</p>
    <p v-if="state.busy" role="status">正在等待原扩展请求；关闭／项目切换必须等待该请求，不能把中止页面当作传输排空。</p>
    <p v-if="state.error" class="extension-warning" role="alert">{{ state.error }}</p>
    <aside v-if="state.intent" class="extension-receipt" aria-label="保留的原扩展请求">
      <strong>原请求：{{ actionLabel(state.intent.action) }}</strong>
      <p>服务器：{{ state.intent.server ?? '本项目 Skills 目录' }} · 传输：{{ state.intent.transport ?? '本地目录验证' }}</p>
      <p>保留当前页面的原服务器、动作与确认请求。缓存、重新进入页面和退出均不能证明该请求没有建立连接或加载目录。</p>
    </aside>
    <div v-if="state.deferredAcknowledgement" class="extension-receipt" role="status">
      <p>原操作已收到有效回执，但在页面隐藏／冻结期间没有展示。不会自动重新请求。</p>
      <button type="button" :disabled="receiptDisabled" @click="controller.showOriginalReceipt()">显示原操作回执（不发送请求）</button>
    </div>
    <fieldset v-if="state.uncertain" class="extension-receipt" :disabled="receiptDisabled">
      <legend>未知回复：只允许明确知悉后退出</legend>
      <label><input v-model="exitConsent" type="checkbox" />我知悉原操作回复仍未知，退出不代表未连接／已排空，不重跑原请求。</label>
      <button type="button" :disabled="!exitConsent" @click="acknowledgeExit">仅允许退出（保留原请求与未知状态）</button>
      <p v-if="state.exitAcknowledged">已明确知悉；所有新操作仍锁定。实际传输／进程清理由原 RunService／主机负责。</p>
    </fieldset>

    <div class="extensions-grid">
      <section class="extension-card" aria-labelledby="mcp-heading">
        <h2 id="mcp-heading">MCP · 配置与目录</h2>
        <fieldset :disabled="disabled">
          <legend>本项目已配置服务器（不显示 URL／argv／env／认证）</legend>
          <button type="button" @click="controller.readServers()">读取配置列表（不连接）</button>
          <label>服务器<select v-model="selectedServer" aria-label="MCP 服务器" :disabled="!state.servers.length">
            <option value="" disabled>显式选择一个服务器</option>
            <option v-for="server in state.servers" :key="server.name" :value="server.name">{{ server.name }} · {{ server.transport }}</option>
          </select></label>
          <p v-if="selected">{{ selected.name }} · {{ selected.transport }} · 原配置并发上限 {{ selected.max_concurrency }}</p>
          <p v-else>未选择服务器；不会默认采用第一项或自动读取工具。</p>
          <button type="button" :disabled="!selected" @click="controller.readCached()">只读当前缓存（不连接）</button>
          <label>本次动作<select v-model="selectedAction" aria-label="MCP 明确动作">
            <option value="probe">探测协议（可能建立连接）</option>
            <option value="refresh">刷新工具目录（可能建立连接）</option>
          </select></label>
          <p>本次选择：{{ selected?.name ?? '未选择' }} · {{ selected?.transport ?? '无传输' }} · {{ actionLabel(state.action) }}</p>
          <label class="extension-consent"><input v-model="discoveryConsent" type="checkbox" :disabled="!selected" />我确认该服务器与本次动作，允许原服务可能启动 stdio／访问网络；这不是工具执行权限。</label>
          <button type="button" :disabled="!selected || !state.confirmed" @click="controller.discover()">执行本次明确动作</button>
        </fieldset>
        <p class="extension-boundary">探测完成仅表示协议探测返回，不证明工具执行、服务器当前健康或安全；刷新目录不执行工具。每次动作重新确认。</p>
        <div v-if="state.lastAction && state.lastAction.action !== 'reload_skills'" class="extension-receipt">
          <strong>记录的回执：{{ actionLabel(state.lastAction.action) }} · {{ state.lastAction.server ?? '本项目 Skills' }}</strong>
          <p>历史回执不是实时健康指标；不继承运行权限。</p>
        </div>
        <p v-if="state.probe">已记录 {{ state.probe.server }} 的探测返回，协议 {{ state.probe.protocol_version }}；未验证工具执行。</p>
        <div v-if="state.catalog" class="extension-catalog" aria-live="polite">
          <h3>{{ state.catalog.server }} · 工具缓存</h3>
          <p v-if="state.catalog.cache_state === 'missing'">尚无该服务的工具目录缓存；工具总数未知，不是空目录或连接失败证明。</p>
          <p v-else-if="state.catalog.cache_state === 'stale_generation'">缓存代际已变化；工具总数未知。不会自动重新连接或刷新。</p>
          <template v-else>
            <p>展示 {{ state.catalog.tools.length }} / {{ state.catalog.total }} 项 · 展示上限 {{ state.catalog.limit }}<span v-if="state.catalog.truncated"> · 其余项未展示</span></p>
            <article v-for="(tool, index) in state.catalog.tools" :key="index" class="extension-item">
              <h4>{{ tool.title }}</h4><p>{{ tool.description }}</p>
              <p>展示名称：<code>{{ tool.logical_name }}</code> · 远端名称：{{ tool.remote_name }}</p>
              <small>Schema 摘要：{{ tool.schema_hash }}（不是权限或工具效果回执）</small>
              <p v-if="tool.text_truncated">文本已截短；展示名称不能用作执行标识。</p>
            </article>
          </template>
          <p class="extension-boundary">远端文本敏感且不可信，仅作为转义后的纯文本展示；未全局脱敏，不能直接作为公开报告。</p>
        </div>
      </section>

      <section class="extension-card" aria-labelledby="skills-heading">
        <h2 id="skills-heading">Skills · 渐进披露</h2>
        <fieldset :disabled="disabled">
          <legend>标题目录与显式重新加载</legend>
          <button type="button" @click="controller.readSkills()">读取 Skills 标题目录（不执行）</button>
          <p>仅显示名称、描述与验证警告，不返回正文或支持文件。注册器可能读取 SKILL.md 正文用于本地校验；不会加载到模型或执行。</p>
          <label class="extension-consent"><input v-model="reloadConsent" type="checkbox" />我确认重新读取本项目 Skills 文件并验证整个候选目录；失败保留旧目录，不执行指令。</label>
          <button type="button" :disabled="!state.reloadConfirmed" @click="controller.reloadSkills()">重新加载 Skills（需要新的确认）</button>
        </fieldset>
        <div v-if="state.skills" class="extension-catalog">
          <h3>记录的 Skills 标题目录</h3>
          <p v-if="state.lastAction?.action === 'reload_skills'">已记录本项目的重新加载回执；未执行指令或授予权限。</p>
          <p>展示 {{ state.skills.skills.length }} / {{ state.skills.total }} 项 · 上限 {{ state.skills.limit }}<span v-if="state.skills.truncated"> · 其余项未展示</span></p>
          <article v-for="skill in state.skills.skills" :key="skill.name" class="extension-item">
            <h4>{{ skill.name }}</h4><p>{{ skill.description }}</p><small v-if="skill.text_truncated">描述已截短</small>
          </article>
          <p>验证警告：展示 {{ state.skills.warnings.length }} / {{ state.skills.warning_total }} 项<span v-if="state.skills.warning_truncated"> · 其余警告未展示</span></p>
          <small>警告文本每项最多 256 UTF-8 字节，可能截短；这里不是完整诊断或正文阅读器。</small>
          <ul><li v-for="(warning, index) in state.skills.warnings" :key="index">{{ warning }}</li></ul>
          <p class="extension-boundary">目录文本敏感且不可信、未全局脱敏。标题不等于已采用某个 Skill，采用由原运行／Policy 路径决定。</p>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.extensions-workspace { max-width: 1440px; margin: 0 auto; padding: 24px; color: var(--text); }
.extensions-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 20px; }
.extensions-heading h1 { margin: 0; font-size: 26px; }.extensions-heading > p { max-width: 680px; color: var(--muted); line-height: 1.65; }
.extensions-eyebrow { margin: 0 0 8px; color: var(--cyan); font-size: 11px; letter-spacing: .15em; }
.extensions-grid { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 20px; }
.extension-card { min-width: 0; padding: 20px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.extension-card h2 { margin-top: 0; }.extension-card fieldset { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--border-soft); }
.extension-card label { display: grid; gap: 8px; }.extension-card .extension-consent, .extension-receipt label { display: flex; align-items: flex-start; gap: 8px; line-height: 1.65; }
.extension-card button, .extension-receipt button { min-height: 44px; padding: 10px 14px; color: var(--text); background: var(--surface-raised); border: 1px solid var(--border); border-radius: 8px; }
.extension-receipt { margin: 16px 0; padding: 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-soft); }
.extension-item { margin: 12px 0; padding: 14px; border-top: 1px solid var(--border-soft); }.extension-item h4 { margin: 0; }
.extension-item p, .extension-catalog li { white-space: pre-wrap; }.extension-card p, .extension-card small, .extension-receipt p { overflow-wrap: anywhere; line-height: 1.65; }
.extension-boundary { color: var(--muted); font-size: 12px; }.extension-warning { padding: 12px; border: 1px solid var(--warn); color: var(--warn); }
@media (max-width: 960px) { .extensions-grid { grid-template-columns: 1fr; }.extensions-heading { flex-direction: column; gap: 8px; } }
@media (max-width: 560px) { .extensions-workspace { padding: 12px; }.extension-card { padding: 12px; } }
</style>
