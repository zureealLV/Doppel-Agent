<script setup lang="ts">
import { NotebookPen } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ContextController, type FileSpec, type NotePayload, type NoteSource, type ProjectNote } from "../context";
const props = defineProps<{ workOrderId: string | null; blocked: boolean }>();
const controller = new ContextController(), state = controller.state;
const attachment = ref(""), acceptConfirmed = ref(false), noteConfirmed = ref(false), runSource = ref("");
const deleting = ref<ProjectNote | null>(null), deleteConfirmed = ref(false);
const disabled = computed(() => props.blocked || !state.ready || state.loading || state.busy || state.preparing || state.closing || state.uncertain || !!state.restoreError);
const kindNames = { fact: "事实", constraint: "约束", decision: "决策" };
const sourceNames = { current: "hash 与本次检查一致", stale: "文件已变，旧行段不可当当前上下文", unavailable: "当前不可读取" };
watch(() => JSON.stringify([state.files, state.budget]), () => { acceptConfirmed.value = false; });
watch(() => JSON.stringify(state.editor), () => { noteConfirmed.value = false; });
watch(() => state.scope, () => { deleting.value = null; deleteConfirmed.value = false; });
function input(event: Event): string { return (event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value; }
function updateFile(index: number, field: keyof FileSpec, event: Event) {
  const files = state.files.map(file => ({ ...file })), value = input(event);
  const item = files[index]!;
  if (field === "path") item.path = value;
  else if (field === "start_line") item.start_line = Number(value);
  else if (field === "end_line") item.end_line = value ? Number(value) : null;
  controller.updateFiles(files);
}
function updateNote(field: "title" | "body" | "kind", event: Event) {
  const value = input(event);
  controller.updateNote({ ...state.editor, [field]: value } as NotePayload);
}
function updateSources(sources: NoteSource[]) { controller.updateNote({ ...state.editor, sources }); }
function addSource(source: NoteSource) {
  if (state.editor.sources.length >= 8) { state.error = "笔记最多 8 项来源。"; return; }
  updateSources([...state.editor.sources, source]);
}
function userLabel(index: number, event: Event) { updateSources(state.editor.sources.map((source, i) => i === index ? { kind: "user", label: input(event) } : source)); }
function addRunSource() {
  if (!/^[0-9a-f]{32}$/.test(runSource.value.trim())) { state.error = "来源需要真实 run 的 32 位 ID；只有终结且已排空的运行可保存。"; return; }
  addSource({ kind: "run", run_id: runSource.value.trim() }); runSource.value = "";
}
function addFileSource(index: number) { if (state.manifest) addSource({ kind: "file", manifest_id: state.manifest.manifest_id, index }); }
function confirmDelete(note: ProjectNote) { deleting.value = note; deleteConfirmed.value = false; }
async function deleteNote() {
  if (!deleting.value) return;
  await controller.deleteNote(deleting.value, deleteConfirmed.value);
  if (!state.uncertain && !state.error) deleting.value = null;
}
async function prepareClose() { await controller.prepareClose(); }
function finishClose() { controller.finishClose(); }
async function prepareInput(scope: string | null) {
  if (props.blocked) throw new Error("工作区正在关闭/切换，不能绑定上下文。");
  return controller.prepareInput(scope);
}
defineExpose({ prepareClose, finishClose, prepareInput });
function beforeUnload(event: BeforeUnloadEvent) {
  if (state.fileDirty || state.noteDirty || state.uncertain || state.selectionPending || state.loading || state.busy || state.preparing || state.restoreError) { event.preventDefault(); event.returnValue = ""; }
}
onMounted(() => { void controller.initialize(); window.addEventListener("beforeunload", beforeUnload); });
onBeforeUnmount(() => { controller.dispose(); window.removeEventListener("beforeunload", beforeUnload); });
</script>
<template>
  <details class="context-drawer">
    <summary title="上下文与项目笔记" aria-label="上下文与项目笔记"><NotebookPen :size="19" aria-hidden="true" /><span class="sr-only">上下文与项目笔记 · {{ state.scope ? `工作单 ${state.scope.slice(0, 8)}` : '项目' }}</span><span v-if="state.fileDirty || state.noteDirty || state.uncertain || state.selectionPending" class="context-unsaved-dot" role="status" title="待保存/确认"><span class="sr-only">待保存/确认</span></span></summary>
    <section class="context-panel" aria-label="显式上下文与确认式项目笔记" :inert="props.blocked || undefined">
      <p class="helper">这是当前面板选择，不是已提交输入。保存/选择不会请求模型或批准工具；原生会话/独立运行仅在本次明确勾选后绑定项目上下文，工作单需明确激活绑定或仅未来节点重绑。先手动切到目标作用域；不会静默代选。工作单与运行详情分别显示已存绑定和固定输入。所有新路径尚待统一验证。</p>
      <p v-if="state.preparing" role="status">正在等待已选内容保存并准备固定引用；暂时不能改选或切换作用域。</p>
      <div class="button-row"><button type="button" :disabled="disabled" @click="controller.initialize(null)">项目上下文</button><button type="button" :disabled="disabled || !workOrderId" @click="controller.initialize(workOrderId)">切到当前工作单上下文</button><button type="button" :disabled="disabled" @click="controller.checkSources()">检查所选来源状态</button></div>
      <p class="helper">作用域不会跟随页面/工作单选择偷偷变化；切换先保存选择，并拒绝未处理编辑。</p>
      <p v-if="state.loading" role="status">正在从项目库恢复…</p>
      <div v-if="state.restoreError" class="order-warning" role="alert">{{ state.restoreError }}<button type="button" :disabled="state.loading || props.blocked || state.closing" @click="controller.initialize(state.scope)">重试只读恢复</button></div>
      <div v-if="state.error" class="order-warning" role="alert">{{ state.error }}</div>
      <div v-if="state.uncertain" class="order-warning" role="alert">保存回复未知，编辑被冻结；不要换 payload/key。<button type="button" :disabled="state.busy || state.loading || props.blocked || state.closing" @click="controller.retryMutation()">重试原请求确认</button></div>
      <p v-if="state.selectionPending && !state.selectionError" role="status">上下文选择保存中，关闭/切换会等待确认。</p>
      <div v-if="state.selectionError" class="order-warning" role="alert">选择尚未保存：{{ state.selectionError }}<button type="button" :disabled="disabled" @click="controller.retrySelection()">重试选择保存</button></div>
      <div v-if="state.checkError" class="order-warning" role="alert">检查失败，之前的状态可能已旧：{{ state.checkError }}</div>
      <div class="context-columns">
        <section aria-labelledby="file-context-heading">
          <h2 id="file-context-heading">显式文件 / 行段</h2>
          <form class="inline-form" @submit.prevent="controller.addAttachment(attachment); attachment = ''"><label>添加附件 <input v-model="attachment" :disabled="disabled" maxlength="2048" placeholder="@src/main.py#L1-L20" /></label><button type="submit" :disabled="disabled">加入待预览清单</button></form>
          <label>包含内容预算（字节，256–65536）<input :value="state.budget" :disabled="disabled" type="number" min="256" max="65536" @input="controller.updateFiles(state.files, Number(input($event)))" /></label>
          <ol class="context-files"><li v-for="(file, index) in state.files" :key="index"><label>相对路径 <input :value="file.path" :disabled="disabled" maxlength="2048" @input="updateFile(index, 'path', $event)" /></label><div class="field-grid"><label>起始行 <input :value="file.start_line" :disabled="disabled" type="number" min="1" @input="updateFile(index, 'start_line', $event)" /></label><label>结束行（空为文件末尾）<input :value="file.end_line ?? ''" :disabled="disabled" type="number" min="1" @input="updateFile(index, 'end_line', $event)" /></label></div><button type="button" :disabled="disabled" @click="controller.updateFiles(state.files.filter((_, i) => i !== index))">移除此待接受行段</button></li></ol>
          <div class="button-row"><button type="button" :disabled="disabled || !state.files.length" @click="controller.previewFiles()">只读预览</button><button type="button" :disabled="disabled" @click="controller.discardFiles(); acceptConfirmed = false">放弃附件编辑</button><button type="button" :disabled="disabled || state.fileDirty || !state.manifest" @click="controller.clearManifest()">清空已选附件（不删除历史快照）</button></div>
          <div v-if="state.preview" class="context-preview"><h3>将接受的快照</h3><p>{{ state.preview.total_bytes }} 字节 · 估算 {{ state.preview.estimated_tokens }} tokens（UTF-8 / 4，不是实际用量）</p><details v-for="entry in state.preview.entries" :key="entry.index"><summary>{{ entry.path }} : {{ entry.start_line }}–{{ entry.end_line }} · {{ entry.bytes }} 字节</summary><p class="context-hash">完整文件 SHA-256 {{ entry.file_sha256 }}<br />包含文本 SHA-256 {{ entry.content_sha256 }}</p><p v-if="entry.truncation_reasons.length" class="order-warning">截断：{{ entry.truncation_reasons.join('、') }}；非完整文件或完整请求范围。</p><pre>{{ entry.text }}</pre></details><label class="context-confirm"><input v-model="acceptConfirmed" type="checkbox" :disabled="disabled" />我确认保存以上范围与截断后的实际内容</label><button type="button" :disabled="disabled || !acceptConfirmed" @click="controller.acceptFiles(acceptConfirmed)">确认接受快照并选择</button></div>
          <section v-if="state.manifest" class="context-selected"><h3>已接受附件 {{ state.manifest.manifest_id.slice(0, 8) }}</h3><p>{{ state.manifest.total_bytes }} 字节 · 估算 {{ state.manifest.estimated_tokens }} tokens；provider actual 尚无本次调用</p><ul><li v-for="entry in state.manifest.entries" :key="entry.index"><strong>{{ entry.path }} : {{ entry.start_line }}–{{ entry.end_line }}</strong><p v-if="state.checks.some(check => check.index === entry.index)">{{ sourceNames[state.checks.find(check => check.index === entry.index)!.status] }}（最近一次显式检查，不保证当前仍未变化）</p><p v-else>尚未检查，当前性未知。</p><small class="context-hash">文件 {{ entry.file_sha256 }}<br />文本 {{ entry.content_sha256 }}</small><p v-if="entry.truncation_reasons.length" class="helper">接受时截断：{{ entry.truncation_reasons.join('、') }}</p></li></ul></section>
        </section>
        <section aria-labelledby="note-context-heading">
          <h2 id="note-context-heading">确认式项目笔记</h2>
          <p class="helper">事实/约束/决策由你确认；来源是接受的快照、已排空真实 run 或明确用户声明，不是模型猜测。保存不自动加入所选上下文。</p>
          <ul class="context-note-picks"><li v-for="note in state.selectedNotes" :key="note.note_id"><strong>已选 r{{ note.revision }} · {{ note.title }}</strong><p v-if="note.current_deleted || note.current_revision !== note.revision" class="order-warning">笔记已删除或更新；旧选择保留供审查，请移除或明确改选新版本。</p><small>来源状态来自最近一次读取，不是本次模型使用证明。</small><button type="button" :disabled="disabled" @click="controller.removeNote(note.note_id)">从所选上下文移除</button></li></ul>
          <button type="button" :disabled="disabled" @click="controller.listNotes()">刷新当前作用域笔记</button>
          <ul class="context-note-list"><li v-for="note in state.notes" :key="note.note_id"><label><input type="checkbox" :disabled="disabled" :checked="state.selectedRefs.some(ref => ref.note_id === note.note_id && ref.revision === note.revision)" @change="controller.pickNote(note, ($event.target as HTMLInputElement).checked)" />{{ kindNames[note.kind] }} · {{ note.title }} · r{{ note.revision }}</label><small>{{ note.scope === 'project' ? '项目' : '工作单' }} · {{ note.body_bytes }} 字节 · 估算 {{ note.estimated_tokens }} tokens</small><div class="button-row"><button type="button" :disabled="disabled" @click="controller.editNote(note.note_id)">读取并编辑</button><button type="button" :disabled="disabled || state.noteDirty" @click="confirmDelete(note)">删除…</button></div></li></ul>
          <button v-if="state.more" type="button" :disabled="disabled" @click="controller.listNotes(true)">读取更早笔记</button><p v-if="state.capped" class="helper">可见列表上限 250；旧笔记仍在库中，已选版本独立载入。</p>
          <div v-if="deleting" class="order-warning"><p>删除 {{ deleting.title }} 的当前版本；保留审计历史，不删除源文件或 run。</p><label class="context-confirm"><input v-model="deleteConfirmed" type="checkbox" :disabled="disabled" />我确认删除这条笔记</label><div class="button-row"><button type="button" :disabled="disabled || !deleteConfirmed" @click="deleteNote">确认删除</button><button type="button" :disabled="disabled" @click="deleting = null">保留笔记</button></div></div>
          <h3>{{ state.editing ? `编辑 r${state.editing.revision}` : '新笔记' }} · {{ state.editor.scope_work_order_id ? '工作单作用域' : '项目作用域' }}</h3>
          <label>类型 <select :value="state.editor.kind" :disabled="disabled" @change="updateNote('kind', $event)"><option value="fact">事实</option><option value="constraint">约束</option><option value="decision">决策</option></select></label><label>标题 <input :value="state.editor.title" :disabled="disabled" maxlength="200" @input="updateNote('title', $event)" /></label><label>正文 <textarea :value="state.editor.body" :disabled="disabled" maxlength="16000" rows="6" @input="updateNote('body', $event)" /></label>
          <ul class="context-note-sources"><li v-for="(source, index) in state.editor.sources" :key="index"><label v-if="source.kind === 'user'">用户声明来源 <input :value="source.label" :disabled="disabled" maxlength="200" @input="userLabel(index, $event)" /></label><span v-else-if="source.kind === 'file'">接受快照 {{ source.manifest_id.slice(0, 8) }} · entry {{ source.index }}</span><span v-else>真实 run {{ source.run_id }}</span><button type="button" :disabled="disabled" @click="updateSources(state.editor.sources.filter((_, i) => i !== index))">移除此笔记来源</button></li></ul>
          <div class="button-row"><button type="button" :disabled="disabled" @click="addSource({ kind: 'user', label: '用户明确确认' })">增加用户声明来源</button><button v-for="entry in state.manifest?.entries || []" :key="entry.index" type="button" :disabled="disabled" @click="addFileSource(entry.index)">引用 {{ entry.path }} : {{ entry.start_line }}–{{ entry.end_line }}</button></div>
          <form class="inline-form" @submit.prevent="addRunSource"><label>来源 run ID <input v-model="runSource" :disabled="disabled" maxlength="32" /></label><button type="submit" :disabled="disabled">增加运行来源</button></form>
          <label class="context-confirm"><input v-model="noteConfirmed" :disabled="disabled" type="checkbox" />我确认内容与来源；这不是未核实模型推测</label><div class="button-row"><button type="button" :disabled="disabled || !noteConfirmed" @click="controller.saveNote(noteConfirmed)">确认保存笔记</button><button type="button" :disabled="disabled" @click="controller.discardNote(); noteConfirmed = false">放弃笔记编辑 / 新笔记</button></div>
        </section>
      </div>
    </section>
  </details>
</template>
