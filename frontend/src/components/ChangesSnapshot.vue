<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { ConflictStage, DiffPlane, GitDiff, GitStatus } from "../changesTypes";
const props = defineProps<{ status: GitStatus | null; diff: GitDiff | null; stale: boolean; disabled: boolean; reading: boolean }>();
const emit = defineEmits<{ refresh: []; select: [path: string, plane: DiffPlane, stage: ConflictStage] }>();
const path = ref(""), plane = ref<DiffPlane>("worktree"), stage = ref<ConflictStage>(null);
const rows = computed(() => props.status?.available ? props.status.rows : []);
const row = computed(() => rows.value.find(item => item.path === path.value));
const stageRequired = computed(() => row.value?.staged === "unmerged" && plane.value !== "combined");
watch(() => props.status?.available ? props.status.fingerprint : "", () => { path.value = ""; stage.value = null; });
function choose(value: string) { path.value = value; stage.value = null; }
function select() {
  if (!props.disabled && !props.stale && row.value && (!stageRequired.value || stage.value !== null)) emit("select", path.value, plane.value, stage.value);
}
// Backend unavailable categories are already fixed; bounded rendering is text, never HTML/link/argv.
function reason(value: string): string { return /^[a-z0-9_]{1,96}$/.test(value) ? value : "git_inspection_unavailable"; }
</script>
<template>
  <section class="changes-snapshot" aria-labelledby="changes-git-title">
    <header class="section-heading"><h2 id="changes-git-title">Git 快照与选中 Diff</h2><button type="button" :disabled="disabled" @click="emit('refresh')">显式刷新 Git 快照</button></header>
    <p class="helper">按需读取，不轮询；会运行固定只读 Git plumbing，不执行项目测试／任意命令，不 stage／commit／reset。</p>
    <p v-if="reading" role="status">正在读取当前请求的证据…</p>
    <p v-if="stale" class="order-warning" role="status">快照可能过期；离开页面／授权变化／指纹不匹配后须显式刷新，不自动重放。</p>
    <p v-if="!status" class="helper">尚未读取 Git 快照；不可达不代表仓库无变更。</p>
    <p v-else-if="!status.available" class="order-warning" role="alert">Git 读取不可用：{{ reason(status.reason) }}。没有 clean 或空仓库结论。</p>
    <template v-else>
      <dl class="changes-metrics">
        <div><dt>实际 HEAD ref</dt><dd>{{ status.head_ref ?? '没有观测到符号 ref（不虚构分支名）' }}</dd></div>
        <div><dt>实际 HEAD 对象</dt><dd><code>{{ status.head_id ?? '尚未观测到 HEAD 对象' }}</code></dd></div>
        <div><dt>格式／引用存储</dt><dd>{{ status.object_format ?? '对象格式未观测' }} / {{ status.ref_storage }} · {{ status.linked_worktree ? 'linked worktree' : '普通工作区' }}</dd></div>
        <div><dt>捕获时间</dt><dd>{{ status.captured_at }}</dd></div>
        <div><dt>排除记录／遍历项</dt><dd>{{ status.excluded_count }}（不等于敏感文件去重计数）</dd></div>
        <div><dt>未知／走访／可见记录</dt><dd>{{ status.unknown_count }} / {{ status.walked_entries }} / {{ status.rows.length }}</dd></div>
      </dl>
      <p class="helper">动态 ignore／拒绝目录／链接及配额可能限制覆盖；这是原始字节比较，不应用 filters／EOL 转换；Windows 不比较执行位。不是整个仓库 clean 证明，也不推断 Agent 作者。</p>
      <p v-if="status.excluded_count || status.unknown_count || status.coverage_reasons.length" class="order-warning">本次仅为有限覆盖／部分可见快照，有排除、未知或覆盖限制；不能缩写成全仓库检查完成。</p>
      <p class="helper">覆盖原因：{{ status.coverage_reasons.map(reason).join('、') || '未报告额外覆盖原因；仍不是全仓库 clean 证明' }}</p>
      <details><summary>本次快照指纹与策略 hash（不是长期权限）</summary><dl class="changes-metrics"><div><dt>fingerprint</dt><dd><code>{{ status.fingerprint }}</code></dd></div><div><dt>policy SHA-256</dt><dd><code>{{ status.policy_sha256 }}</code></dd></div><div><dt>index SHA-256</dt><dd><code>{{ status.index_sha256 ?? '未观测' }}</code></dd></div><div><dt>refs SHA-256</dt><dd><code>{{ status.refs_sha256 }}</code></dd></div></dl></details>
      <div class="changes-table-wrap"><table class="changes-table"><caption>可见记录（含未改变项；不是全仓库清单）</caption><thead><tr><th scope="col">路径</th><th scope="col">HEAD → index</th><th scope="col">index → 工作区</th><th scope="col">限制</th></tr></thead><tbody>
        <tr v-for="item in rows" :key="item.path"><td><button type="button" :disabled="disabled || stale" :aria-pressed="path === item.path" @click="choose(item.path)">{{ item.path }}</button></td><td>{{ item.staged }}</td><td>{{ item.worktree }}</td><td>{{ item.reason ? reason(item.reason) : '—' }}</td></tr>
      </tbody></table></div>
      <p v-if="!rows.length" class="helper">本次快照没有可见记录；不代表被排除内容无变化。</p>
      <fieldset class="changes-selection" :disabled="disabled || stale || !row"><legend>选中 Diff：{{ path || '先选择一条实际记录' }}</legend>
        <label>比较平面<select v-model="plane"><option value="staged">staged · HEAD → index</option><option value="worktree">worktree · index → 工作区</option><option value="combined">combined · HEAD → 工作区</option></select></label>
        <label v-if="row?.stages.length">冲突 index stage<select v-model="stage"><option :value="null">{{ stageRequired ? '必须选择实际 stage' : '不使用 stage（combined 仍是 HEAD → 工作区）' }}</option><option v-for="item in row.stages" :key="item.stage" :value="item.stage">stage {{ item.stage }} · {{ item.object_id }}</option></select></label>
        <button type="button" :disabled="!row || disabled || stale || (stageRequired && stage === null)" @click="select">读取选中 Diff（绑定本次指纹）</button>
      </fieldset>
    </template>
    <section v-if="diff" class="changes-diff" aria-label="指纹化 Diff 结果">
      <p v-if="!diff.available" class="order-warning" role="alert">Diff 不可用：{{ reason(diff.reason) }}。不自动缩减、改 stage 或重试。</p>
      <template v-else><h3>{{ diff.path }} · {{ diff.plane }}<span v-if="diff.conflict_stage !== null"> · stage {{ diff.conflict_stage }}</span></h3>
        <p class="helper">敏感本地文本，尚非 S8 全局脱敏；仅内存展示，不写入浏览器存储。{{ diff.text ? '' : '文本差异为空；仍须查看存在性／mode，不等于整个仓库 clean。' }}</p>
        <dl class="changes-metrics"><div><dt>before / after 存在</dt><dd>{{ diff.before_exists }} / {{ diff.after_exists }}</dd></div><div><dt>before / after mode</dt><dd>{{ diff.before_mode ?? 'missing' }} / {{ diff.after_mode ?? 'missing' }}</dd></div><div><dt>before SHA-256</dt><dd><code>{{ diff.before_sha256 ?? 'missing' }}</code></dd></div><div><dt>after SHA-256</dt><dd><code>{{ diff.after_sha256 ?? 'missing' }}</code></dd></div></dl>
        <pre tabindex="0" aria-label="本次选中 Diff 文本">{{ diff.text }}</pre>
      </template>
    </section>
  </section>
</template>
