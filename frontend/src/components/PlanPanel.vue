<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import ContextBindingReceipt from "./ContextBindingReceipt.vue";
import type { ModelProfile } from "../workspaceTypes";
import { clonePlan, defaultExecution, validatePlan, type PlanTask, type WorkOrder, type WorkOrderPlan, type ExecutionSettings } from "../workOrders";

const props = defineProps<{ plan: WorkOrderPlan; order: WorkOrder | null; dirty: boolean; disabled: boolean; uncertain: boolean; profiles: ModelProfile[];
  rebindContext?: boolean; uncertainOperation?: "save" | "activate" | null; conflict?: boolean }>();
const emit = defineEmits<{ update: [plan: WorkOrderPlan]; save: []; discard: []; activate: [settings: ExecutionSettings, includeContext: boolean]; reconcile: [];
  rebind: [selected: boolean]; retry: []; adopt: [] }>();
const execution = reactive(defaultExecution());
const includeContext = ref(false);
watch(() => props.order?.work_order_id, () => { Object.assign(execution, defaultExecution()); includeContext.value = false; });
const validation = computed(() => validatePlan(props.plan));
const started = computed(() => new Set(props.order?.attempts.map(a => a.task_id) || []));
const terminal = computed(() => !!props.order && ["succeeded", "cancelled"].includes(props.order.status));
const futureRebind = computed(() => !!props.order && props.order.status !== "draft" && !terminal.value && props.plan.tasks.some(task => !started.value.has(task.id)));
const frozen = (id: string) => props.disabled || props.uncertain || terminal.value || started.value.has(id);
const value = (event: Event) => (event.target as HTMLInputElement).value;
function updateTitle(title: string) { const plan = clonePlan(props.plan); plan.title = title; emit("update", plan); }
function updateTask(index: number, changes: Partial<PlanTask>) { const plan = clonePlan(props.plan); Object.assign(plan.tasks[index]!, changes); emit("update", plan); }
function add() {
  const plan = clonePlan(props.plan); let index = 1;
  while (plan.tasks.some(t => t.id === `task-${index}`)) index++;
  plan.tasks.push({ id: `task-${index}`, title: "新节点", prompt: "", dependencies: [], access: "read", mode: "graph", profile_id: null }); emit("update", plan);
}
function remove(index: number) { const plan = clonePlan(props.plan); plan.tasks.splice(index, 1); emit("update", plan); }
function dependency(index: number, id: string, checked: boolean) {
  const old = props.plan.tasks[index]!.dependencies;
  updateTask(index, { dependencies: checked ? [...old, id] : old.filter(value => value !== id) });
}
function activate() { if (!props.disabled && !props.uncertain && !props.dirty) emit("activate", JSON.parse(JSON.stringify(execution)) as ExecutionSettings, includeContext.value); }
</script>

<template>
  <section class="plan-panel" aria-labelledby="plan-title">
    <div class="section-heading"><div><p class="eyebrow">PLAN / IMMUTABLE REVISIONS</p><h2 id="plan-title">任务计划</h2></div><span v-if="order" class="revision-label">当前 r{{ order.active_revision }}<template v-if="dirty"> · 未保存</template></span></div>
    <p class="helper">先保存计划，再显式批准执行。已开始节点固定定义；修改未来节点会暂停工作单，不取消当前运行。</p>
    <label><span class="field-label">工作单标题</span><input :value="plan.title" maxlength="200" :disabled="disabled || uncertain || terminal" @input="updateTitle(value($event))" /></label>
    <ol class="plan-nodes">
      <li v-for="(task, index) in plan.tasks" :key="index" class="plan-node">
        <div class="section-heading"><h3>节点 {{ index + 1 }} <span v-if="started.has(task.id)" class="helper">· 定义已固定</span></h3><button type="button" :disabled="frozen(task.id) || plan.tasks.length === 1 || plan.tasks.some(t => t.dependencies.includes(task.id))" @click="remove(index)">删除节点</button></div>
        <div class="field-grid">
          <label><span class="field-label">节点 ID</span><input :value="task.id" maxlength="64" :disabled="frozen(task.id) || plan.tasks.some(t => t.dependencies.includes(task.id))" @input="updateTask(index, { id: value($event) })" /></label>
          <label><span class="field-label">标题</span><input :value="task.title" maxlength="200" :disabled="frozen(task.id)" @input="updateTask(index, { title: value($event) })" /></label>
        </div>
        <label><span class="field-label">任务说明</span><textarea :value="task.prompt" rows="4" maxlength="100000" :disabled="frozen(task.id)" @input="updateTask(index, { prompt: value($event) })" /></label>
        <div class="field-grid">
          <label><span class="field-label">访问分类（不是授权）</span><select :value="task.access" :disabled="frozen(task.id)" @change="updateTask(index, { access: value($event) as PlanTask['access'] })"><option value="read">只读 · 不获副作用工具</option><option value="write">写访问 · 仍需显式 grants / 审批</option></select></label>
          <label><span class="field-label">执行器</span><select :value="task.mode" :disabled="frozen(task.id)" @change="updateTask(index, { mode: value($event) as PlanTask['mode'] })"><option value="graph">Graph</option><option value="deep">Deep</option><option value="legacy">Legacy</option></select></label>
        </div>
        <label><span class="field-label">节点模型（留空沿用批准时选择）</span><input :value="task.profile_id || ''" maxlength="128" :disabled="frozen(task.id)" list="order-profile-ids" @input="updateTask(index, { profile_id: value($event) || null })" /></label>
        <fieldset class="node-dependencies" :disabled="frozen(task.id)"><legend>前置依赖</legend><label v-for="other in plan.tasks.filter(t => t.id !== task.id)" :key="other.id"><input type="checkbox" :checked="task.dependencies.includes(other.id)" @change="dependency(index, other.id, ($event.target as HTMLInputElement).checked)" />{{ other.id }} · {{ other.title }}</label><span v-if="plan.tasks.length === 1" class="helper">无其他节点</span></fieldset>
      </li>
    </ol>
    <datalist id="order-profile-ids"><option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }} · {{ profile.model }}</option></datalist>
    <label v-if="order && order.status !== 'draft' && !terminal" class="context-confirm"><input type="checkbox" :checked="!!rebindContext" :disabled="disabled || uncertain || !futureRebind" @change="emit('rebind', ($event.target as HTMLInputElement).checked)" />保存本次新版本时，显式重绑目标工作单面板所选上下文，仅用于尚未开始的节点</label>
    <p v-if="order && order.status !== 'draft' && !terminal" class="helper">不勾选就沿用旧绑定；勾选允许只改上下文而保存新版本，也会暂停派发。请先手动切到此工作单上下文并接受/确认内容；空选择表示明确移除未来节点的文件/笔记基底。已开始或重试节点不会跟随改选；以服务接受时已开始的节点为准。</p>
    <p v-if="order && order.status !== 'draft' && !terminal && !futureRebind" class="helper">当前没有尚未开始的节点；不会用重绑替换已经执行的输入。</p>
    <div class="button-row"><button type="button" :disabled="disabled || uncertain || terminal || plan.tasks.length >= 64" @click="add">添加节点</button><button type="button" :disabled="disabled || uncertain || terminal || !!validation || (!!order && !dirty && !rebindContext)" class="primary-button" @click="emit('save')">{{ order ? '保存新计划版本' : '保存草稿（不执行）' }}</button><button v-if="dirty || rebindContext" type="button" :disabled="disabled || uncertain" @click="emit('discard')">放弃未保存编辑 / 重绑意图</button></div>
    <p v-if="validation" class="field-error" role="status">{{ validation }}</p>
    <div v-if="uncertain" class="order-warning" role="alert"><p>{{ uncertainOperation === 'activate' ? '激活' : '保存' }}结果尚未确认。编辑已冻结；重试沿用原计划版本、执行配置和上下文引用，不会读取面板新选择或自动换创建 key。</p><div class="button-row"><button type="button" :disabled="disabled" @click="emit('retry')">重试原请求（不重新选择上下文）</button><button type="button" :disabled="disabled" @click="emit('reconcile')">只读刷新核对版本与绑定</button><button v-if="conflict" type="button" :disabled="disabled" @click="emit('adopt')">明确停止本地旧请求重试，采用服务端版本</button></div><p v-if="conflict" class="helper">采用只读版本不再次激活/重绑，不判定旧请求成功或未执行；会放弃当前本地编辑和原重试意图。</p></div>
    <form v-if="order?.status === 'draft'" class="execution-scope" @submit.prevent="activate">
      <h3>显式批准执行</h3><p class="helper">激活将固定模型路由及权限。只读节点不会继承以下副作用权限；每次工具审批仍独立处理。</p>
      <fieldset :disabled="disabled || dirty || uncertain"><legend>执行配置</legend>
        <label><span class="field-label">默认模型档案</span><select v-if="profiles.length" v-model="execution.profile_id"><option :value="null">批准时解析服务默认模型</option><option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }} · {{ profile.model }}</option></select><input v-else :value="execution.profile_id || ''" placeholder="留空使用服务默认模型" @input="execution.profile_id = value($event) || null" /></label>
        <div class="field-grid"><label><span class="field-label">力度</span><select v-model="execution.effort"><option>quick</option><option>balanced</option><option>deep</option></select></label><label><span class="field-label">每节点最长秒数</span><input v-model.number="execution.deadline_seconds" type="number" min="1" max="3600" step="1" required /></label></div>
        <div class="permissions"><label><input v-model="execution.permissions.workspace_write" type="checkbox" />允许补丁提案</label><label><input v-model="execution.permissions.command_execute" type="checkbox" />允许命令执行</label><label><input v-model="execution.permissions.mcp_execute" type="checkbox" />允许 MCP 工具</label><label><input v-model="execution.permissions.delegate" type="checkbox" />允许子任务</label></div>
        <label class="context-confirm"><input v-model="includeContext" type="checkbox" />本次批准时绑定目标工作单作用域的已接受上下文 / 确认笔记版本</label>
        <p class="helper">默认不绑定，绝不跟随面板改选。勾选后等待选择保存，服务在激活时检查并固定输入；不增加上方权限。草稿保存不等于接受上下文。</p>
        <button type="submit" class="primary-button" :disabled="dirty || !!validation || !Number.isInteger(execution.deadline_seconds)">批准此版本并进入队列</button>
      </fieldset>
    </form>
    <section v-else-if="order?.execution.profiles" class="execution-scope"><h3>已固定执行配置</h3><p>{{ order.execution.effort }} · 每节点 {{ order.execution.deadline_seconds }} 秒</p><ul><li v-for="profile in Object.values(order.execution.profiles)" :key="profile.id"><code>{{ profile.id }}</code> · {{ profile.provider }} / {{ profile.model }}</li></ul><p class="helper">模型路由快照不代表成本价格或实际用量已测得。</p></section>
    <ContextBindingReceipt v-if="order" :bindings="order.context_bindings" :active-revision="order.active_revision" />
  </section>
</template>
