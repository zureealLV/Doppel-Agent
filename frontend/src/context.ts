import { reactive } from "vue";
import type { ContextDescriptor } from "./types";

export interface FileSpec { path: string; start_line: number; end_line: number | null; expected_file_sha256?: string | null }
export interface FileEntry {
  index: number; path: string; start_line: number; requested_end_line: number | null; end_line: number; total_lines: number;
  file_sha256: string; content_sha256: string; captured_at: string; text: string; bytes: number; truncation_reasons: string[];
}
export interface ContextPreview { entries: FileEntry[]; budget_bytes: number; total_bytes: number; estimated_tokens: number; estimate_method: string; actual_tokens: null }
export interface Manifest extends ContextPreview { manifest_id: string; request_key: string; created_at: string }
export type NoteSource = { kind: "user"; label: string } | { kind: "file"; manifest_id: string; index: number } | { kind: "run"; run_id: string };
export interface NotePayload { kind: "fact" | "constraint" | "decision"; title: string; body: string; scope_work_order_id: string | null; sources: NoteSource[] }
export interface ProjectNote extends NotePayload {
  note_id: string; revision: number; current_revision: number; current_deleted: boolean; deleted: boolean; request_key: string;
  scope: "project" | "work_order"; body_bytes: number; estimated_tokens: number; actual_tokens: null;
}
export interface NoteRef { note_id: string; revision: number }
export interface ContextSelection { saved: boolean; scope_work_order_id: string | null; manifest_id: string | null; notes: NoteRef[]; revision: number; request_key: string | null }
export interface SourceCheck { index: number; status: "current" | "stale" | "unavailable"; reason: string }
export class ContextHttpError extends Error { constructor(readonly status: number, message: string) { super(message); } }
const base = "/api/v1/context", idPath = (id: string) => encodeURIComponent(id);
async function request<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(url, { method, ...(body === undefined ? {} : {
    body: JSON.stringify(body), headers: { "Content-Type": "application/json", "X-Doppel-UI": "1" },
  }) });
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new ContextHttpError(response.status, typeof error.detail === "string" ? error.detail : `上下文请求未完成（HTTP ${response.status}）。`);
  }
  return response.json() as Promise<T>;
}
export const contextApi = {
  preview: (files: FileSpec[], budget: number) => request<ContextPreview>(`${base}/preview`, "POST", { files, budget_bytes: budget }),
  accept: (files: FileSpec[], budget: number, key: string) => request<Manifest>(`${base}/manifests`, "POST", { files, budget_bytes: budget, confirmed: true, idempotency_key: key }),
  manifest: (id: string) => request<Manifest>(`${base}/manifests/${idPath(id)}`),
  check: (id: string) => request<{ manifest_id: string; entries: SourceCheck[] }>(`${base}/manifests/${idPath(id)}/check`, "POST"),
  selection: (scope: string | null) => request<ContextSelection>(`${base}/selection${scope ? `?scope_work_order_id=${idPath(scope)}` : ""}`),
  saveSelection: (scope: string | null, manifest: string | null, notes: NoteRef[], revision: number, key: string) => request<ContextSelection>(`${base}/selection`, "POST", {
    scope_work_order_id: scope, manifest_id: manifest, notes, expected_revision: revision, idempotency_key: key,
  }),
  notes: (scope: string | null, before?: string) => request<ProjectNote[]>(`${base}/notes?limit=50${scope ? `&scope_work_order_id=${idPath(scope)}` : ""}${before ? `&before_id=${idPath(before)}` : ""}`),
  note: (id: string, revision?: number) => request<ProjectNote>(`${base}/notes/${idPath(id)}${revision ? `?revision=${revision}` : ""}`),
  createNote: (note: NotePayload, key: string) => request<ProjectNote>(`${base}/notes`, "POST", { note, confirmed: true, idempotency_key: key }),
  updateNote: (id: string, note: NotePayload, revision: number, key: string) => request<ProjectNote>(`${base}/notes/${idPath(id)}`, "PUT", { note, expected_revision: revision, confirmed: true, idempotency_key: key }),
  deleteNote: (id: string, revision: number, key: string) => request<ProjectNote>(`${base}/notes/${idPath(id)}/delete`, "POST", { expected_revision: revision, confirmed: true, idempotency_key: key }),
};
export type ContextApi = typeof contextApi;
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const draftFiles = (manifest: Manifest | null): FileSpec[] => manifest?.entries.map(e => ({ path: e.path, start_line: e.start_line, end_line: e.requested_end_line })) || [];
export const emptyNote = (scope: string | null): NotePayload => ({ kind: "constraint", title: "", body: "", scope_work_order_id: scope, sources: [{ kind: "user", label: "用户明确确认" }] });
const editableSource = (source: NoteSource): NoteSource => source.kind === "user" ? { kind: "user", label: source.label }
  : source.kind === "file" ? { kind: "file", manifest_id: source.manifest_id, index: source.index } : { kind: "run", run_id: source.run_id };
export function parseAttachment(text: string): FileSpec | null {
  const match = /^@?([^#]+)(?:#L([1-9]\d*)(?:-L?([1-9]\d*))?)?$/.exec(text.trim());
  if (!match) return null;
  const path = match[1]!.trim(), start = Number(match[2] || 1), end = match[3] ? Number(match[3]) : match[2] ? start : null;
  if (!path || path.startsWith("/") || /[\\:\x00-\x1f]/.test(path) || path.split("/").some(p => !p || [".", ".."].includes(p))
    || !Number.isSafeInteger(start) || end !== null && (!Number.isSafeInteger(end) || end < start)) return null;
  return { path, start_line: start, end_line: end };
}
type Mutation = { kind: "accept"; files: FileSpec[]; budget: number; key: string }
  | { kind: "create" | "update"; note: NotePayload; id: string | null; revision: number; key: string }
  | { kind: "delete"; id: string; revision: number; key: string };
type Pick = { scope: string | null; manifest: string | null; notes: NoteRef[]; key: string; revision?: number };
export class ContextController {
  readonly state = reactive({ scope: null as string | null, ready: false, loading: false, busy: false, closing: false,
    error: "", restoreError: "", files: [] as FileSpec[], budget: 65536, fileDirty: false, preview: null as ContextPreview | null,
    manifest: null as Manifest | null, checks: [] as SourceCheck[], checkError: "", notes: [] as ProjectNote[], more: false, capped: false,
    selectedRefs: [] as NoteRef[], selectedNotes: [] as ProjectNote[], editor: emptyNote(null), editing: null as ProjectNote | null,
    noteDirty: false, uncertain: false, preparing: false, selectionPending: false, selectionError: "" });
  private epoch = 0;
  private disposed = false;
  private reads = 0;
  private flight: Promise<void> | null = null;
  private pending: Mutation | null = null;
  private picked: Pick | null = null;
  private writes: Promise<void> = Promise.resolve();
  constructor(private readonly api: ContextApi = contextApi) {}
  private blocked(): boolean { return this.disposed || !this.state.ready || this.state.loading || this.state.busy || this.state.preparing || this.state.closing || this.state.uncertain || !!this.state.restoreError; }
  private report(error: unknown): string { return error instanceof Error ? error.message : "上下文操作未完成，请重试。"; }
  async initialize(scope: string | null = null): Promise<void> {
    if (this.disposed || this.state.loading || this.state.busy || this.state.preparing || this.state.closing) return;
    if (this.state.fileDirty || this.state.noteDirty || this.state.uncertain) { this.state.error = "请先接受/保存或明确放弃当前编辑，再切换上下文作用域。"; return; }
    this.state.loading = true;
    try { await this.flushSelection(); }
    catch (error) { this.state.error = this.report(error); this.state.loading = false; return; }
    if (this.disposed) { this.state.loading = false; return; }
    const epoch = ++this.epoch; ++this.reads;
    this.state.scope = scope; this.state.ready = false; this.state.restoreError = ""; this.state.error = "";
    this.state.manifest = null; this.state.selectedRefs = []; this.state.selectedNotes = []; this.state.files = []; this.state.preview = null;
    this.state.notes = []; this.state.checks = []; this.state.checkError = ""; this.state.editor = emptyNote(scope); this.state.editing = null;
    try {
      const saved = await this.api.selection(scope);
      if (saved.scope_work_order_id !== scope) throw new Error("上下文选择作用域回复不匹配。");
      const manifest = saved.manifest_id ? await this.api.manifest(saved.manifest_id) : null;
      if (manifest && manifest.manifest_id !== saved.manifest_id) throw new Error("附件快照 ID 回复不匹配。");
      const selected: ProjectNote[] = [];
      for (const ref of saved.notes) {
        const note = await this.api.note(ref.note_id, ref.revision);
        if (note.note_id !== ref.note_id || note.revision !== ref.revision || ![null, scope].includes(note.scope_work_order_id)) throw new Error("所选笔记身份/版本/作用域不匹配。");
        selected.push(note);
      }
      if (this.disposed || epoch !== this.epoch) return;
      this.state.manifest = manifest; this.state.files = draftFiles(manifest); this.state.budget = manifest?.budget_bytes || 65536;
      this.state.selectedRefs = clone(saved.notes); this.state.selectedNotes = selected; this.state.ready = true;
    } catch (error) { if (!this.disposed && epoch === this.epoch) this.state.restoreError = this.report(error); }
    finally { --this.reads; if (epoch === this.epoch) this.state.loading = false; }
    if (this.state.ready && !this.disposed) await this.listNotes();
  }
  updateFiles(files: FileSpec[], budget = this.state.budget): void {
    if (this.blocked()) return;
    this.state.files = clone(files); this.state.budget = budget; this.state.fileDirty = true; this.state.preview = null;
  }
  addAttachment(text: string): void {
    const entry = parseAttachment(text);
    if (!entry || this.state.files.length >= 24) { this.state.error = "请用 @相对路径#L1-L20；最多 24 项。不接受 host path 或越界路径。"; return; }
    this.updateFiles([...this.state.files, entry]);
  }
  discardFiles(): void {
    if (this.blocked()) return;
    this.state.files = draftFiles(this.state.manifest); this.state.budget = this.state.manifest?.budget_bytes || 65536;
    this.state.fileDirty = false; this.state.preview = null; this.state.error = "";
  }
  async previewFiles(): Promise<void> {
    if (this.blocked()) return;
    const epoch = this.epoch; ++this.reads; this.state.busy = true; this.state.error = "";
    try { const preview = await this.api.preview(clone(this.state.files), this.state.budget); if (!this.disposed && epoch === this.epoch) this.state.preview = preview; }
    catch (error) { this.state.error = this.report(error); }
    finally { --this.reads; this.state.busy = false; }
  }
  async acceptFiles(confirmed: boolean): Promise<void> {
    if (this.blocked()) return;
    if (!confirmed || !this.state.preview) { this.state.error = "先预览，再明确确认保存这些附件快照。"; return; }
    const preview = this.state.preview;
    if (preview.entries.length !== this.state.files.length || preview.budget_bytes !== this.state.budget
      || preview.entries.some((entry, i) => entry.path !== this.state.files[i]!.path || entry.start_line !== this.state.files[i]!.start_line || entry.requested_end_line !== this.state.files[i]!.end_line)) { this.state.error = "预览已失效，请重新预览。"; return; }
    this.pending = { kind: "accept", files: this.state.files.map((spec, i) => ({ ...spec, expected_file_sha256: preview.entries[i]!.file_sha256 })), budget: this.state.budget, key: crypto.randomUUID() };
    await this.mutate();
  }
  clearManifest(): void {
    if (this.blocked()) return;
    if (this.state.fileDirty) { this.state.error = "请先放弃未接受的附件编辑。"; return; }
    this.state.manifest = null; this.state.files = []; this.state.preview = null; this.state.checks = []; this.remember();
  }
  async checkSources(): Promise<void> {
    if (this.blocked()) return;
    const epoch = this.epoch; ++this.reads; this.state.busy = true;
    try {
      const manifest = this.state.manifest;
      const checks = manifest ? await this.api.check(manifest.manifest_id) : null;
      if (checks && checks.manifest_id !== manifest?.manifest_id) throw new Error("附件状态 ID 不匹配。");
      const selected: ProjectNote[] = [];
      for (const ref of this.state.selectedRefs) {
        const note = await this.api.note(ref.note_id, ref.revision);
        if (note.note_id !== ref.note_id || note.revision !== ref.revision || ![null, this.state.scope].includes(note.scope_work_order_id)) throw new Error("所选笔记身份/版本/作用域不匹配。");
        selected.push(note);
      }
      if (!this.disposed && epoch === this.epoch) { this.state.checks = checks?.entries || []; this.state.selectedNotes = selected; this.state.checkError = ""; }
    } catch (error) { this.state.checkError = this.report(error); }
    finally { --this.reads; this.state.busy = false; }
  }
  async listNotes(more = false): Promise<void> {
    if (this.blocked()) return;
    const epoch = this.epoch; ++this.reads; this.state.busy = true;
    try {
      const rows = await this.api.notes(this.state.scope, more ? this.state.notes.at(-1)?.note_id : undefined);
      if (this.disposed || epoch !== this.epoch) return;
      if (rows.some(note => ![null, this.state.scope].includes(note.scope_work_order_id))) throw new Error("笔记列表回复含其他作用域。");
      const combined = more ? [...this.state.notes, ...rows.filter(n => !this.state.notes.some(old => old.note_id === n.note_id))] : rows;
      this.state.notes = combined.slice(0, 250); this.state.capped = combined.length >= 250; this.state.more = rows.length === 50 && !this.state.capped;
    } catch (error) { this.state.error = this.report(error); }
    finally { --this.reads; this.state.busy = false; }
  }
  pickNote(note: ProjectNote, selected: boolean): void {
    if (this.blocked() || note.deleted || note.current_deleted || note.revision !== note.current_revision || ![null, this.state.scope].includes(note.scope_work_order_id)) return;
    const refs = this.state.selectedRefs.filter(ref => ref.note_id !== note.note_id);
    if (selected && refs.length >= 16) { this.state.error = "最多显式选择 16 条笔记。"; return; }
    this.state.selectedRefs = selected ? [...refs, { note_id: note.note_id, revision: note.revision }] : refs;
    this.state.selectedNotes = this.state.selectedNotes.filter(old => old.note_id !== note.note_id);
    if (selected) this.state.selectedNotes.push(note);
    this.remember();
  }
  removeNote(id: string): void {
    if (this.blocked()) return;
    this.state.selectedRefs = this.state.selectedRefs.filter(ref => ref.note_id !== id);
    this.state.selectedNotes = this.state.selectedNotes.filter(note => note.note_id !== id); this.remember();
  }
  updateNote(note: NotePayload): void { if (!this.blocked()) { this.state.editor = clone(note); this.state.noteDirty = true; } }
  discardNote(): void { if (!this.blocked()) { this.state.editor = emptyNote(this.state.scope); this.state.editing = null; this.state.noteDirty = false; this.state.error = ""; } }
  async editNote(id: string): Promise<void> {
    if (this.blocked()) return;
    if (this.state.noteDirty) { this.state.error = "先保存或放弃当前笔记编辑。"; return; }
    const epoch = this.epoch; ++this.reads; this.state.busy = true;
    try {
      const note = await this.api.note(id);
      if (this.disposed || epoch !== this.epoch) return;
      if (note.note_id !== id || ![null, this.state.scope].includes(note.scope_work_order_id)) throw new Error("笔记回复不属于当前作用域。");
      this.state.editing = note; this.state.editor = { kind: note.kind, title: note.title, body: note.body, scope_work_order_id: note.scope_work_order_id, sources: note.sources.map(editableSource) };
    } catch (error) { this.state.error = this.report(error); }
    finally { --this.reads; this.state.busy = false; }
  }
  async saveNote(confirmed: boolean): Promise<void> {
    if (this.blocked()) return;
    if (!confirmed) { this.state.error = "笔记保存需要明确确认，不会把未确认的内容当事实。"; return; }
    const note = this.state.editor;
    if (!note.title.trim() || !note.body.trim() || note.title.length > 200 || note.body.length > 16000 || !note.sources.length || note.sources.length > 8) { this.state.error = "笔记需要标题、正文和 1–8 项来源；正文最多 16000 字符。"; return; }
    this.pending = { kind: this.state.editing ? "update" : "create", note: clone(note), id: this.state.editing?.note_id || null, revision: this.state.editing?.revision || 0, key: crypto.randomUUID() };
    await this.mutate();
  }
  async deleteNote(note: ProjectNote, confirmed: boolean): Promise<void> {
    if (this.blocked()) return;
    if (note.current_deleted || ![null, this.state.scope].includes(note.scope_work_order_id)) { this.state.error = "只能明确删除当前作用域的未删除笔记。"; return; }
    if (!confirmed || this.state.noteDirty) { this.state.error = "先处理未保存笔记，并明确确认删除。"; return; }
    this.pending = { kind: "delete", id: note.note_id, revision: note.current_revision, key: crypto.randomUUID() }; await this.mutate();
  }
  private async mutate(): Promise<void> {
    const intent = this.pending;
    if (!intent || this.disposed || this.state.busy || this.state.closing) return;
    this.state.busy = true; this.state.error = "";
    const recovering = this.state.uncertain;
    const flight = (async () => {
      try {
        if (intent.kind === "accept") {
          const manifest = await this.api.accept(intent.files, intent.budget, intent.key);
          if (this.disposed) return;
          if (manifest.request_key !== intent.key) throw new Error("附件接受回复 key 不匹配。");
          this.state.manifest = manifest; this.state.files = draftFiles(manifest); this.state.fileDirty = false; this.state.preview = null; this.state.checks = []; this.remember();
        } else {
          const note = intent.kind === "delete" ? await this.api.deleteNote(intent.id, intent.revision, intent.key)
            : intent.id ? await this.api.updateNote(intent.id, intent.note, intent.revision, intent.key) : await this.api.createNote(intent.note, intent.key);
          if (this.disposed) return;
          if (note.request_key !== intent.key || intent.id && note.note_id !== intent.id) throw new Error("笔记保存回复身份/key 不匹配。");
          const expectedRevision = intent.kind === "create" ? 1 : intent.revision + 1;
          const expectedScope = intent.kind === "delete" ? this.state.scope : intent.note.scope_work_order_id;
          if (note.revision !== expectedRevision || note.current_revision < note.revision
            || intent.kind !== "delete" && note.scope_work_order_id !== expectedScope
            || intent.kind === "delete" && (!note.deleted || ![null, expectedScope].includes(note.scope_work_order_id))) throw new Error("笔记保存回复版本/作用域不匹配。");
          this.state.selectedNotes = this.state.selectedNotes.map(old => old.note_id === note.note_id ? { ...old, current_revision: note.current_revision, current_deleted: note.current_deleted } : old);
          if (intent.kind === "delete") {
            this.state.selectedRefs = this.state.selectedRefs.filter(ref => ref.note_id !== intent.id);
            this.state.selectedNotes = this.state.selectedNotes.filter(old => old.note_id !== intent.id); this.remember();
            if (this.state.editing?.note_id === intent.id) { this.state.editing = null; this.state.editor = emptyNote(this.state.scope); }
          } else if (note.current_deleted || note.revision !== note.current_revision) {
            this.state.editing = note; this.state.noteDirty = true; this.state.error = "该请求已保存历史版本，但当前笔记已变；编辑仍保留，请明确放弃或载入新版本。";
          } else { this.state.editing = note; this.state.noteDirty = false; }
        }
        this.state.uncertain = false; this.pending = null;
      } catch (error) {
        this.state.error = this.report(error);
        this.state.uncertain = recovering || !(error instanceof ContextHttpError && [400, 404, 409, 422].includes(error.status));
        if (!this.state.uncertain) this.pending = null;
      } finally { this.state.busy = false; }
    })();
    this.flight = flight; await flight; if (this.flight === flight) this.flight = null;
    if (!this.state.uncertain && !this.state.closing && !this.disposed) await this.listNotes();
  }
  async retryMutation(): Promise<void> { if (this.state.uncertain && !this.state.loading && !this.state.restoreError) await this.mutate(); }
  private remember(): void {
    const intent: Pick = { scope: this.state.scope, manifest: this.state.manifest?.manifest_id || null, notes: clone(this.state.selectedRefs), key: crypto.randomUUID() };
    this.picked = intent; this.state.selectionPending = true; this.state.selectionError = "";
    this.writes = this.writes.then(() => this.writePick(intent));
  }
  private confirmPick(intent: Pick): void { if (!this.disposed && this.picked === intent) { this.picked = null; this.state.selectionPending = false; this.state.selectionError = ""; } }
  private async writePick(intent: Pick, recover = false): Promise<void> {
    try {
      if (intent.revision === undefined || recover) {
        const current = await this.api.selection(intent.scope);
        if (current.scope_work_order_id !== intent.scope) throw new Error("上下文作用域回复不匹配。");
        if (intent.revision !== undefined && current.revision < intent.revision) throw new Error("上下文选择版本倒退，保存仍未确认。");
        if (recover && intent.revision !== undefined && current.revision > intent.revision && current.manifest_id === intent.manifest && JSON.stringify(current.notes) === JSON.stringify(intent.notes)) { this.confirmPick(intent); return; }
        if (intent.revision !== undefined && intent.revision !== current.revision) intent.key = crypto.randomUUID();
        intent.revision = current.revision;
      }
      const reply = await this.api.saveSelection(intent.scope, intent.manifest, intent.notes, intent.revision, intent.key);
      if (reply.scope_work_order_id !== intent.scope || reply.manifest_id !== intent.manifest || JSON.stringify(reply.notes) !== JSON.stringify(intent.notes)
        || reply.revision !== intent.revision + 1 || reply.request_key !== intent.key) throw new Error("上下文选择保存回复不匹配。");
      this.confirmPick(intent);
    } catch (error) { if (this.picked === intent && !this.disposed) this.state.selectionError = this.report(error); }
  }
  async flushSelection(): Promise<void> {
    let tail: Promise<void>;
    do { tail = this.writes; await tail; } while (tail !== this.writes);
    const intent = this.picked;
    if (intent) {
      this.writes = this.writes.then(() => this.writePick(intent, true));
      do { tail = this.writes; await tail; } while (tail !== this.writes);
    }
    if (this.picked) throw new Error(this.state.selectionError || "上下文选择未确认，不能关闭或切换作用域。");
  }
  async retrySelection(): Promise<void> { try { await this.flushSelection(); } catch { /* Keep visible pending/error for a later retry. */ } }
  async prepareInput(scope: string | null): Promise<ContextDescriptor> {
    if (this.blocked() || this.reads) throw new Error("上下文仍在读取/恢复或等待确认；不能准备运行输入。");
    if (scope !== this.state.scope) throw new Error("上下文作用域不匹配；请手动切到项目/目标工作单作用域再提交，不会静默代选。");
    if (this.state.fileDirty || this.state.noteDirty) throw new Error("请先接受附件、保存笔记或明确放弃编辑，再绑定运行输入。");
    const epoch = this.epoch;
    this.state.preparing = true;
    try {
      await this.flushSelection();
      if (this.disposed || epoch !== this.epoch || this.state.closing) throw new Error("上下文准备被工作区关闭/切换打断；没有提交运行。");
      if (this.state.selectedNotes.length !== this.state.selectedRefs.length || this.state.selectedRefs.some(ref => {
        const note = this.state.selectedNotes.find(n => n.note_id === ref.note_id && n.revision === ref.revision);
        return !note || note.deleted || note.current_deleted || note.current_revision !== ref.revision || ![null, scope].includes(note.scope_work_order_id);
      })) throw new Error("所选笔记已变/删除或作用域不匹配；请明确重选版本。服务仍会检查文件和来源。");
      return clone({ manifest_id: this.state.manifest?.manifest_id || null, notes: this.state.selectedRefs });
    } finally { this.state.preparing = false; }
  }
  async prepareClose(): Promise<void> {
    this.state.closing = true;
    if (this.state.preparing || this.state.loading || this.reads || this.state.restoreError || !this.state.ready) throw new Error("上下文选择仍在读取/恢复/准备输入；请等待或重试后关闭。");
    if (this.flight) await this.flight;
    if (this.state.fileDirty || this.state.noteDirty || this.state.uncertain) throw new Error("上下文/笔记还有未接受、未保存或未确认的编辑，请保存或明确放弃后关闭。");
    await this.flushSelection();
  }
  finishClose(): void { this.state.closing = false; }
  dispose(): void { this.disposed = true; ++this.epoch; }
}
