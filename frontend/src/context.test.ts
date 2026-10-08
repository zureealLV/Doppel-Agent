// Definitions only; controller/SSR is not native restart or model-input proof.
import { describe, expect, it, vi } from "vitest";
import { createSSRApp } from "vue";
import { renderToString } from "vue/server-renderer";
import ContextPanel from "./components/ContextPanel.vue";
import { ContextController, ContextHttpError, parseAttachment, type ContextApi, type ContextSelection, type Manifest, type ProjectNote, type NotePayload, type FileSpec, type NoteRef } from "./context";

const mid = "a".repeat(32), nid = "b".repeat(32), oid = "c".repeat(32);
function manifest(): Manifest {
  return { manifest_id: mid, request_key: "fixture-manifest-key", created_at: "fixture", budget_bytes: 65536, total_bytes: 8, estimated_tokens: 2, estimate_method: "utf8_bytes_div4", actual_tokens: null,
    entries: [{ index: 0, path: "source.txt", start_line: 1, requested_end_line: null, end_line: 1, total_lines: 1,
      file_sha256: "a".repeat(64), content_sha256: "b".repeat(64), captured_at: "fixture", text: "fixture\n", bytes: 8, truncation_reasons: [] }] };
}
function note(revision = 1): ProjectNote {
  return { note_id: nid, revision, current_revision: revision, current_deleted: false, deleted: false, request_key: "fixture-note-key", kind: "constraint", title: "fixture", body: "Preserve files.", sources: [{ kind: "user", label: "confirmed human" }], scope: "project", scope_work_order_id: null, body_bytes: 15, estimated_tokens: 4, actual_tokens: null };
}
function api(): ContextApi {
  const picks = new Map<string, ContextSelection>();
  return {
    preview: vi.fn(async () => manifest()), accept: vi.fn(async (_files: FileSpec[], _budget: number, key: string) => ({ ...manifest(), request_key: key })),
    manifest: vi.fn(async () => manifest()), check: vi.fn(async () => ({ manifest_id: mid, entries: [{ index: 0, status: "current" as const, reason: "" }] })),
    selection: vi.fn(async (scope: string | null) => picks.get(scope || "project") || { saved: false, scope_work_order_id: scope, manifest_id: null, notes: [], revision: 0, request_key: null }),
    saveSelection: vi.fn(async (scope: string | null, manifest_id: string | null, notes: NoteRef[], revision: number, key: string) => {
      const previous = picks.get(scope || "project");
      if (previous?.request_key === key) return previous;
      if ((previous?.revision || 0) !== revision) throw new ContextHttpError(409, "context_selection_revision_changed");
      const saved = { saved: true, scope_work_order_id: scope, manifest_id, notes, revision: revision + 1, request_key: key };
      picks.set(scope || "project", saved); return saved;
    }),
    notes: vi.fn(async () => [note()]), note: vi.fn(async (_id: string, revision?: number) => note(revision)),
    createNote: vi.fn(async (payload: NotePayload, key: string) => ({ ...note(), ...payload, request_key: key })),
    updateNote: vi.fn(async (_id: string, payload: NotePayload, revision: number, key: string) => ({ ...note(revision + 1), ...payload, request_key: key })),
    deleteNote: vi.fn(async (_id: string, revision: number, key: string) => ({ ...note(revision + 1), deleted: true, current_deleted: true, request_key: key })),
  };
}
function deferred<T>() { let resolve!: (value: T) => void; return { promise: new Promise<T>(r => { resolve = r; }), resolve }; }
const payload = (): NotePayload => ({ kind: "constraint", title: "explicit", body: "Preserve files.", scope_work_order_id: null, sources: [{ kind: "user", label: "human" }] });

describe("context attachment/parser and confirmation boundaries", () => {
  it("parses explicit @path/line syntax, rejecting host/traversal/bad line formats", () => {
    expect(parseAttachment("@src/main.py#L2-L9")).toEqual({ path: "src/main.py", start_line: 2, end_line: 9 });
    expect(parseAttachment("@README.md")).toEqual({ path: "README.md", start_line: 1, end_line: null });
    for (const value of ["C:/secret", "../secret", "/outside", "a\\b", "@x#L9-L2", "@x#L0"]) expect(parseAttachment(value)).toBeNull();
  });
  it("preview does not accept and confirmation uses preview hashes, not a raw unreviewed payload", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    controller.addAttachment("@source.txt"); await controller.previewFiles();
    expect(backend.accept).not.toHaveBeenCalled(); expect(controller.state.fileDirty).toBe(true);
    await controller.acceptFiles(false); expect(backend.accept).not.toHaveBeenCalled();
    await controller.acceptFiles(true); await controller.flushSelection();
    expect(vi.mocked(backend.accept).mock.calls[0]![0][0]!.expected_file_sha256).toBe("a".repeat(64));
    expect(controller.state.manifest?.manifest_id).toBe(mid); expect(controller.state.fileDirty).toBe(false);
    expect((await backend.selection(null)).manifest_id).toBe(mid);
  });
  it("editing path/budget invalidates preview and blocks close until accepted or explicitly discarded", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    controller.addAttachment("@source.txt"); await controller.previewFiles(); controller.updateFiles(controller.state.files, 256);
    expect(controller.state.preview).toBeNull(); await controller.acceptFiles(true); expect(backend.accept).not.toHaveBeenCalled();
    await expect(controller.prepareClose()).rejects.toThrow("未接受"); controller.finishClose();
    controller.discardFiles(); await controller.prepareClose();
  });
  it("unknown accept retains its original key/hash/payload, freezes editing and allows exact retry", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    vi.mocked(backend.accept).mockRejectedValueOnce(new Error("lost accept reply"));
    controller.addAttachment("@source.txt"); await controller.previewFiles(); await controller.acceptFiles(true);
    const first = vi.mocked(backend.accept).mock.calls[0]!;
    controller.updateFiles([{ path: "other.txt", start_line: 1, end_line: null }]); expect(controller.state.files[0]!.path).toBe("source.txt");
    await expect(controller.prepareClose()).rejects.toThrow("未确认"); controller.finishClose();
    await controller.retryMutation(); expect(vi.mocked(backend.accept).mock.calls[1]).toEqual(first);
    expect(controller.state.uncertain).toBe(false);
  });
});

describe("context/note durable scope and lifecycle definitions", () => {
  it("restores historical selected note and accepted file independently of the current first page, without POST", async () => {
    const backend = api(), controller = new ContextController(backend);
    vi.mocked(backend.selection).mockResolvedValueOnce({ saved: true, scope_work_order_id: null, manifest_id: mid, notes: [{ note_id: nid, revision: 1 }], revision: 5, request_key: "saved-choice-key" });
    vi.mocked(backend.note).mockResolvedValueOnce({ ...note(), current_revision: 2 }); vi.mocked(backend.notes).mockResolvedValue([]);
    await controller.initialize();
    expect(controller.state.manifest?.manifest_id).toBe(mid); expect(controller.state.selectedNotes[0]!.revision).toBe(1);
    expect(controller.state.selectedNotes[0]!.current_revision).toBe(2); expect(controller.state.notes).toEqual([]);
    expect(backend.saveSelection).not.toHaveBeenCalled(); expect(backend.accept).not.toHaveBeenCalled(); expect(backend.createNote).not.toHaveBeenCalled();
  });
  it("unknown restore blocks edits/close and manual retry reads only instead of wiping stored choice", async () => {
    const backend = api(), controller = new ContextController(backend);
    vi.mocked(backend.selection).mockRejectedValueOnce(new Error("restore offline")); await controller.initialize();
    controller.addAttachment("@source.txt"); controller.updateNote(payload()); await controller.saveNote(true);
    expect(controller.state.files).toEqual([]); expect(backend.createNote).not.toHaveBeenCalled();
    await expect(controller.prepareClose()).rejects.toThrow("恢复"); controller.finishClose(); await controller.initialize();
    expect(controller.state.ready).toBe(true); expect(backend.saveSelection).not.toHaveBeenCalled();
  });
  it("note writes require confirmation, do not automatically select newly saved notes, and keep scope reads separate", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize(); controller.updateNote(payload());
    await controller.saveNote(false); expect(backend.createNote).not.toHaveBeenCalled();
    await controller.saveNote(true); expect(controller.state.selectedRefs).toEqual([]);
    expect(backend.saveSelection).not.toHaveBeenCalled();
    controller.pickNote(note(), true); await controller.flushSelection();
    await controller.initialize(oid); expect(controller.state.selectedRefs).toEqual([]);
    expect((await backend.selection(null)).notes).toEqual([{ note_id: nid, revision: 1 }]);
    expect((await backend.selection(oid)).saved).toBe(false);
  });
  it("a known note CAS conflict keeps dirty editing but permits explicit discard; unknown edits keep the same key", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize(); await controller.editNote(nid);
    controller.updateNote(payload()); vi.mocked(backend.updateNote).mockRejectedValueOnce(new ContextHttpError(409, "note_revision_changed"));
    await controller.saveNote(true); expect(controller.state.noteDirty).toBe(true); expect(controller.state.uncertain).toBe(false);
    controller.discardNote(); await controller.editNote(nid); controller.updateNote(payload());
    vi.mocked(backend.updateNote).mockRejectedValueOnce(new Error("lost edit reply")); await controller.saveNote(true);
    const first = vi.mocked(backend.updateNote).mock.calls.at(-1)!; controller.discardNote(); expect(controller.state.noteDirty).toBe(true);
    await controller.retryMutation(); expect(vi.mocked(backend.updateNote).mock.calls.at(-1)).toEqual(first);
  });
  it("a newer note response never silently changes a previously selected revision", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    controller.pickNote(note(), true); await controller.flushSelection(); await controller.editNote(nid); controller.updateNote(payload()); await controller.saveNote(true);
    expect(controller.state.selectedRefs).toEqual([{ note_id: nid, revision: 1 }]);
    expect(controller.state.selectedNotes[0]!.current_revision).toBe(2);
    controller.pickNote(note(2), true); await controller.flushSelection(); expect(controller.state.selectedRefs[0]!.revision).toBe(2);
  });
  it("close cannot drop pending preview/read; dispose ignores late restore without writing selection", async () => {
    const backend = api(), controller = new ContextController(backend), gate = deferred<ContextSelection>();
    vi.mocked(backend.selection).mockReturnValueOnce(gate.promise); const loading = controller.initialize();
    await expect(controller.prepareClose()).rejects.toThrow("恢复"); controller.finishClose(); controller.dispose();
    gate.resolve({ saved: false, scope_work_order_id: null, manifest_id: null, notes: [], revision: 0, request_key: null }); await loading;
    expect(controller.state.ready).toBe(false); expect(backend.saveSelection).not.toHaveBeenCalled();
  });
  it("failed selection flush blocks close, then retries unchanged key/CAS without poisoning later choices", async () => {
    const backend = api(), controller = new ContextController(backend), original = vi.mocked(backend.saveSelection).getMockImplementation()!;
    await controller.initialize(); vi.mocked(backend.saveSelection).mockRejectedValue(new Error("metadata offline"));
    controller.pickNote(note(), true); await expect(controller.prepareClose()).rejects.toThrow("metadata offline"); controller.finishClose();
    const first = vi.mocked(backend.saveSelection).mock.calls[0]!; expect(controller.state.selectionPending).toBe(true);
    vi.mocked(backend.saveSelection).mockImplementation(original); await controller.retrySelection();
    expect(vi.mocked(backend.saveSelection).mock.calls.at(-1)).toEqual(first); expect(controller.state.selectionPending).toBe(false);
    controller.removeNote(nid); await controller.flushSelection(); expect((await backend.selection(null)).notes).toEqual([]);
  });
  it("lost committed selection replies confirm canonical pair while no-current checks remain unknown", async () => {
    const backend = api(), controller = new ContextController(backend), original = vi.mocked(backend.saveSelection).getMockImplementation()!;
    await controller.initialize(); vi.mocked(backend.saveSelection).mockImplementationOnce(async (...args) => { await original(...args); throw new Error("lost choice ACK"); });
    controller.pickNote(note(), true); await controller.flushSelection(); expect(backend.saveSelection).toHaveBeenCalledTimes(1);
    expect(controller.state.selectionPending).toBe(false); expect(controller.state.checks).toEqual([]);
  });
  it("native close waits for an already admitted accept and its durable selection, not merely the POST launch", async () => {
    const backend = api(), controller = new ContextController(backend), gate = deferred<Manifest>();
    await controller.initialize(); controller.addAttachment("@source.txt"); await controller.previewFiles();
    vi.mocked(backend.accept).mockReturnValueOnce(gate.promise);
    const accepting = controller.acceptFiles(true), closed = vi.fn();
    const closing = controller.prepareClose().then(closed);
    expect(closed).not.toHaveBeenCalled();
    const key = vi.mocked(backend.accept).mock.calls[0]![2];
    gate.resolve({ ...manifest(), request_key: key }); await accepting; await closing;
    expect(closed).toHaveBeenCalledOnce(); expect((await backend.selection(null)).manifest_id).toBe(mid);
    expect(controller.state.selectionPending).toBe(false);
  });
  it("a late older metadata ACK cannot clear a newer pending selection and close drains the last tail", async () => {
    const backend = api(), controller = new ContextController(backend), gate = deferred<ContextSelection>();
    const original = vi.mocked(backend.saveSelection).getMockImplementation()!;
    await controller.initialize();
    vi.mocked(backend.saveSelection).mockImplementationOnce(async (...args) => {
      const saved = await original(...args); await gate.promise; return saved;
    });
    controller.pickNote(note(), true);
    await vi.waitFor(() => expect(backend.saveSelection).toHaveBeenCalledTimes(1));
    controller.removeNote(nid); const closed = vi.fn(), closing = controller.prepareClose().then(closed);
    expect(controller.state.selectionPending).toBe(true); expect(closed).not.toHaveBeenCalled();
    gate.resolve(await backend.selection(null)); await closing;
    expect(backend.saveSelection).toHaveBeenCalledTimes(2); expect((await backend.selection(null)).notes).toEqual([]);
    expect(controller.state.selectionPending).toBe(false);
  });
  it("unknown canonical recovery reads retain selection and block close without fabricating success", async () => {
    const backend = api(), controller = new ContextController(backend);
    await controller.initialize(); vi.mocked(backend.selection).mockRejectedValue(new Error("canonical read offline"));
    controller.pickNote(note(), true); await expect(controller.prepareClose()).rejects.toThrow("canonical read offline");
    expect(controller.state.selectionPending).toBe(true); expect(backend.saveSelection).not.toHaveBeenCalled();
  });
  it("foreign-scope note responses cannot populate the list or editor or be selected/deleted", async () => {
    const backend = api(), controller = new ContextController(backend), foreign = { ...note(), scope_work_order_id: oid };
    vi.mocked(backend.notes).mockResolvedValue([foreign]); await controller.initialize();
    expect(controller.state.notes).toEqual([]); expect(controller.state.error).toContain("作用域");
    controller.pickNote(foreign, true); await controller.deleteNote(foreign, true);
    expect(controller.state.selectedRefs).toEqual([]); expect(backend.deleteNote).not.toHaveBeenCalled();
    vi.mocked(backend.note).mockResolvedValue(foreign); await controller.editNote(nid);
    expect(controller.state.editing).toBeNull();
  });
  it("only confirmed deletion removes selected refs, while failed source checks preserve the last receipt as old", async () => {
    const backend = api(), controller = new ContextController(backend);
    await controller.initialize(); controller.pickNote(note(), true); await controller.flushSelection();
    await controller.deleteNote(note(), false); expect(backend.deleteNote).not.toHaveBeenCalled();
    await controller.deleteNote(note(), true); await controller.flushSelection();
    expect((await backend.selection(null)).notes).toEqual([]);
    controller.addAttachment("@source.txt"); await controller.previewFiles(); await controller.acceptFiles(true); await controller.checkSources();
    const receipt = [...controller.state.checks]; vi.mocked(backend.check).mockRejectedValueOnce(new Error("source read offline"));
    await controller.checkSources(); expect(controller.state.checkError).toBe("source read offline");
    expect(controller.state.checks).toEqual(receipt); expect(controller.state.manifest?.manifest_id).toBe(mid);
  });
  it("initial SSR describes explicit confirmation and no injected/actual model proof", async () => {
    const html = await renderToString(createSSRApp(ContextPanel, { workOrderId: oid, blocked: false }));
    expect(html).toContain("不会请求模型"); expect(html).toContain("当前面板选择，不是已提交输入"); expect(html).toContain("确认保存笔记");
    expect(html).not.toContain("验证通过"); expect(html).not.toContain("hash 与本次检查一致");
  });
});

describe("reviewed selection to explicit fixed input descriptor definitions", () => {
  it("a later 4xx cannot release an earlier unknown confirmed mutation or replace its payload/key", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    vi.mocked(backend.accept).mockRejectedValueOnce(new Error("lost confirmed accept"))
      .mockRejectedValueOnce(new ContextHttpError(409, "later source failure"));
    controller.addAttachment("@source.txt"); await controller.previewFiles(); await controller.acceptFiles(true);
    await controller.retryMutation(); expect(controller.state.uncertain).toBe(true);
    await expect(controller.prepareInput(null)).rejects.toThrow("等待确认");
    await controller.retryMutation(); const calls = vi.mocked(backend.accept).mock.calls;
    expect(calls[1]).toEqual(calls[0]); expect(calls[2]).toEqual(calls[0]); expect(controller.state.uncertain).toBe(false);
  });
  it("waits for the final ordered selection save, locks mutation/scope/close, and returns a detached descriptor", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    const save = vi.mocked(backend.saveSelection).getMockImplementation()!, gate = deferred<void>();
    vi.mocked(backend.saveSelection).mockImplementationOnce(async (...args) => { await gate.promise; return save(...args); });
    controller.pickNote(note(), true);
    const pending = controller.prepareInput(null);
    expect(controller.state.preparing).toBe(true); controller.removeNote(nid); await controller.initialize(oid);
    expect(controller.state.scope).toBeNull(); expect(controller.state.selectedRefs).toHaveLength(1);
    await expect(controller.prepareClose()).rejects.toThrow("准备输入"); controller.finishClose();
    gate.resolve(undefined); const descriptor = await pending;
    expect(descriptor).toEqual({ manifest_id: null, notes: [{ note_id: nid, revision: 1 }] });
    descriptor.notes[0]!.revision = 88; expect(controller.state.selectedRefs[0]!.revision).toBe(1);
    expect(controller.state.selectionPending).toBe(false); expect(controller.state.preparing).toBe(false);
  });
  it("fails closed on dirty files/notes, wrong namespace, stale picked versions and unknown selection ACK", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    await expect(controller.prepareInput(oid)).rejects.toThrow("作用域不匹配");
    controller.addAttachment("@source.txt"); await expect(controller.prepareInput(null)).rejects.toThrow("接受附件"); controller.discardFiles();
    controller.updateNote(payload()); await expect(controller.prepareInput(null)).rejects.toThrow("保存笔记"); controller.discardNote();
    controller.pickNote(note(), true); await controller.flushSelection(); controller.state.selectedNotes[0]!.current_revision = 2;
    await expect(controller.prepareInput(null)).rejects.toThrow("已变/删除"); controller.removeNote(nid); await controller.flushSelection();
    vi.mocked(backend.saveSelection).mockRejectedValue(new Error("unknown metadata save")); controller.pickNote(note(), true);
    await expect(controller.prepareInput(null)).rejects.toThrow("unknown metadata save"); expect(controller.state.selectionPending).toBe(true); expect(controller.state.preparing).toBe(false);
  });
  it("explicit empty descriptor does not silently select a listed note, create metadata defaults or check files", async () => {
    const backend = api(), controller = new ContextController(backend); await controller.initialize();
    await expect(controller.prepareInput(null)).resolves.toEqual({ manifest_id: null, notes: [] });
    expect(backend.saveSelection).not.toHaveBeenCalled(); expect(backend.check).not.toHaveBeenCalled(); expect(backend.accept).not.toHaveBeenCalled();
  });
});
