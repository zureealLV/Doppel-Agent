<script setup lang="ts">
import { canRetry, latestAttempt, taskReason, orderStatus, dispatchErrors, type WorkOrder, type QueueSnapshot } from "../workOrders";
defineProps<{ order: WorkOrder | null; queue: QueueSnapshot | null; queueError: string; disabled: boolean; viewDisabled?: boolean }>();
const emit = defineEmits<{ control: [action: "pause" | "resume" | "cancel"]; retry: [taskId: string]; openRun: [runId: string] }>();
</script>
<template>
  <section class="task-queue" aria-labelledby="queue-title">
    <p class="eyebrow">DURABLE STATE / NOT MODEL CLAIMS</p><h2 id="queue-title">任务与运行队列</h2>
    <p v-if="queueError" class="field-error" role="alert">{{ queueError }}</p>
    <template v-if="queue"><p class="queue-capacity">原生调度器占用 {{ queue.scheduler.active }}/{{ queue.scheduler.max_active }} · 内存排队 {{ queue.scheduler.queued }}/{{ queue.scheduler.queue_capacity }}</p><p class="helper">持久状态与内存调度器分别取样；审批等待或终态排空仍在下面列表中。不含 Legacy 线程池和独立子任务队列。</p>
      <details class="global-run-queue"><summary>全项目原生运行（{{ queue.total }}）</summary><ul><li v-for="item in queue.items" :key="item.run_id"><button type="button" :disabled="viewDisabled ?? disabled" @click="emit('openRun', item.run_id)">{{ item.title || item.run_id }} · {{ orderStatus[item.status] || item.status }}</button><small>{{ item.mode }} · {{ item.task_id ? `工作单节点 ${item.task_id}` : '未确认工作单关联' }} · {{ item.lease_active ? 'lease 仍占用' : 'lease 已释放' }}</small></li></ul><p v-if="queue.total > queue.items.length" class="helper">仅显示最近 {{ queue.limit }} 项；总数不是当前可见项数量。</p><p v-if="!queue.items.length" class="helper">当前无原生活跃/审批等待/排空运行。</p></details>
    </template><p v-else class="helper">尚无队列快照，不能把未知占用当作零。</p>
    <template v-if="order">
      <div class="section-heading"><h3>{{ order.title }}</h3><span :data-order-state="order.status">{{ orderStatus[order.status] || order.status }}</span></div>
      <p v-if="order.dispatch_error" class="order-warning" role="alert">{{ dispatchErrors[order.dispatch_error] || '派发状态需检查' }}；查看真实运行后再显式恢复/重试。</p>
      <div class="button-row"><button v-if="['queued', 'running'].includes(order.status)" type="button" :disabled="disabled" @click="emit('control', 'pause')">暂停新派发</button><button v-if="order.status === 'paused'" type="button" :disabled="disabled" @click="emit('control', 'resume')">恢复派发</button><button v-if="!['succeeded', 'cancelled'].includes(order.status) || order.dispatch_error === 'runtime_cancel_failed'" type="button" :disabled="disabled" class="danger-button" @click="emit('control', 'cancel')">{{ order.dispatch_error === 'runtime_cancel_failed' ? '重试取消' : '取消工作单与活跃运行' }}</button></div>
      <p class="helper">暂停不取消当前 run；取消请求不等于资源已经排空。重试后仍需显式恢复工作单。</p>
      <ol class="queue-nodes"><li v-for="task in order.tasks" :key="task.task_id"><header><strong>{{ task.task_id }} · {{ task.title }}</strong><span :data-order-state="task.status">{{ orderStatus[task.status] || task.status }}</span></header><p class="helper">定义 r{{ task.execution_revision }} · {{ task.mode }} · {{ task.access }}<template v-if="task.dependencies.length"> · 依赖 {{ task.dependencies.join('、') }}</template></p><p v-if="taskReason(order, task)" class="task-reason">{{ taskReason(order, task) }}</p><div class="button-row"><button v-if="latestAttempt(order, task)?.run_id" type="button" :disabled="viewDisabled ?? disabled" @click="emit('openRun', latestAttempt(order, task)!.run_id!)">打开真实运行/审批</button><button v-if="canRetry(order, task)" type="button" :disabled="disabled" @click="emit('retry', task.task_id)">申请重试失败节点</button></div>
        <details v-if="order.attempts.some(a => a.task_id === task.task_id)"><summary>尝试记录</summary><ul class="attempt-list"><li v-for="attempt in order.attempts.filter(a => a.task_id === task.task_id)" :key="attempt.attempt_id">r{{ attempt.revision }} / #{{ attempt.attempt_number }} · {{ orderStatus[attempt.status] || attempt.status }}<button v-if="attempt.run_id" type="button" :disabled="viewDisabled ?? disabled" @click="emit('openRun', attempt.run_id)"><code>{{ attempt.run_id }}</code></button><span v-else> · 尚无已确认的 run</span></li></ul></details>
      </li></ol><p class="helper">执行完成只表示 runtime 终态与 lease 对账；没有测试结果时仍为未验证，不提供虚构百分比。</p>
    </template>
  </section>
</template>
