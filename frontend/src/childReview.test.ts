// Construction definitions, UNRUN; injected transport, no DOM/native/provider.
import { describe, expect, it, vi } from "vitest";
import { ChildReviewController } from "./childReview";
import { childAdmission, childId, childParent, childSnapshot, otherParent } from "./testSupport/childReviewFixture";
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function ticks() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
function fixture() {
  const api = { snapshot: vi.fn(async () => childSnapshot()), history: vi.fn(), spawn: vi.fn(async () => childAdmission(1, "new")),
    followUp: vi.fn(async () => childAdmission()), cancel: vi.fn() };
  return { api, c: new ChildReviewController(api) };
}
async function selected(c: ChildReviewController) { c.activate(true); expect(c.selectSource(childParent)).toBe(true); await c.readSnapshot(); c.selectChild(childId); }
function reviewed(c: ChildReviewController) { c.chooseAction("follow_up"); c.setDraft("follow_up", " next "); expect(c.reviewAction()).toBe(true); c.confirmReview(true); }

describe("root-owned reviewed child lifecycle", () => {
  it('startup quarantine refuses spawn/follow-up but permits only freshly reviewed original-generation cancel, not failed-close execution', async () => {
    const { c, api } = fixture(), snapshot = childSnapshot(); snapshot.service.execution_admission = 'quarantined';
    snapshot.items[0]!.status = 'running'; snapshot.counts.durable_active_for_parent = 1;
    api.snapshot.mockResolvedValue(snapshot); await selected(c);
    c.chooseAction('spawn'); c.setDraft('spawn', 'new'); expect(c.reviewAction()).toBe(false);
    c.chooseAction('follow_up'); c.setDraft('follow_up', 'next'); expect(c.reviewAction()).toBe(false);
    c.chooseAction('cancel'); expect(c.reviewAction()).toBe(true); c.confirmReview(true);
    api.cancel.mockResolvedValue({ parent_run_id: childParent, subagent_id: childId,
      reviewed_generation: 1, cancel_requested: true, physical_drain_verified: false });
    await c.dispatch(); expect(api.cancel).toHaveBeenCalledOnce();
    expect(api.cancel).toHaveBeenCalledWith(childParent, childId, { confirmed: true, expected_generation: 1 });
    expect(api.spawn).not.toHaveBeenCalled(); expect(api.followUp).not.toHaveBeenCalled();
    snapshot.service.failed_close_diagnostic = true; api.snapshot.mockResolvedValue(snapshot);
    await c.readSnapshot(); c.selectChild(childId); c.chooseAction('cancel'); expect(c.reviewAction()).toBe(false);
  });
  it("does not consume draft-retention capacity merely by visiting empty sources", () => {
    const { c } = fixture(); c.activate(true);
    for (let i = 1; i <= 64; i++) expect(c.selectSource(i.toString(16).padStart(32, '0'))).toBe(true);
    expect(c.hasDrafts).toBe(false); expect(c.unloadBlocked).toBe(false);
  });
  it("bounds nonempty draft sources without blocking read-only source selection or evicting old drafts", () => {
    const { c } = fixture(); c.activate(true);
    for (let i = 1; i <= 32; i++) { c.selectSource(i.toString(16).padStart(32, '0')); c.setDraft('spawn', `draft ${i}`); }
    expect(c.selectSource('e'.repeat(32))).toBe(true); c.setDraft('spawn', 'not accepted');
    expect(c.state.spawnDraft).toBe(''); expect(c.state.sourceNotice).toContain('32');
    c.selectSource('1'.padStart(32, '0')); expect(c.state.spawnDraft).toBe('draft 1');
    c.setDraft('spawn', ''); c.selectSource('e'.repeat(32)); c.setDraft('spawn', 'accepted after explicit clear');
    expect(c.state.spawnDraft).toBe('accepted after explicit clear');
  });
  it("rejects malformed injected spawn receipt while preserving exact unknown intent", async () => {
    const { api, c } = fixture(); await selected(c); c.chooseAction('spawn'); c.setDraft('spawn', 'new'); c.reviewAction(); c.confirmReview(true);
    api.spawn.mockResolvedValue({ ...childAdmission(1, 'new'), completion_verified: true } as unknown as ReturnType<typeof childAdmission>);
    await c.dispatch(); expect(c.state.uncertain).toBe(true); expect(c.state.intent?.subagent_id).toBeNull();
    expect(c.state.spawnDraft).toBe('new'); await c.readSnapshot(); expect(api.snapshot).toHaveBeenCalledOnce();
  });
  it("joins passive original metadata reads on close without publishing stale late data", async () => {
    const { api, c } = fixture(); c.activate(true); c.selectSource(childParent);
    const flight = deferred<ReturnType<typeof childSnapshot>>(); api.snapshot.mockImplementation(() => flight.promise);
    const work = c.readSnapshot(); let closed = false; const closing = c.prepareClose().then(() => { closed = true; });
    await ticks(); expect(closed).toBe(false); expect(c.selectSource(otherParent)).toBe(false);
    flight.resolve(childSnapshot()); await work; await closing;
    expect(c.state.snapshot).toBeNull(); expect(c.state.uncertain).toBe(false); expect(closed).toBe(true);
  });
  it("rejects malformed injected cancel ACK without inferring physical drain or replacing its reviewed generation", async () => {
    const { api, c } = fixture(); const value = childSnapshot(); value.items[0]!.status = 'running'; value.counts.durable_active_for_parent = 1;
    api.snapshot.mockResolvedValue(value); await selected(c); c.chooseAction('cancel'); c.reviewAction(); c.confirmReview(true);
    api.cancel.mockResolvedValue({ parent_run_id: childParent, subagent_id: childId, reviewed_generation: 2, cancel_requested: true, physical_drain_verified: true });
    await c.dispatch(); expect(c.state.uncertain).toBe(true); expect(c.state.lastReceipt).toBeNull();
    expect(c.state.intent?.body).toEqual({ confirmed: true, expected_generation: 1 });
    c.activate(false); c.activate(true); await c.dispatch(); expect(api.cancel).toHaveBeenCalledOnce();
  });
  it("construction/selection/visibility/reentry do not request, and drafts survive parent switches", async () => {
    const { api, c } = fixture(); c.activate(true); c.selectSource(childParent); c.setDraft("spawn", " original draft ");
    c.selectSource(otherParent); c.setDraft("spawn", "other draft"); c.selectSource(childParent); c.activate(false); c.activate(true); await ticks();
    expect(c.state.spawnDraft).toBe(" original draft ");
    for (const call of Object.values(api)) expect(call).not.toHaveBeenCalled();
    expect(c.hasDrafts).toBe(true); expect(c.unloadBlocked).toBe(true);
  });
  it("requires an exact snapshot and fresh action/source/generation/draft review", async () => {
    const { api, c } = fixture(); await selected(c); reviewed(c);
    c.setDraft("follow_up", "changed"); await c.dispatch(); expect(api.followUp).not.toHaveBeenCalled();
    c.setDraft("follow_up", "next"); c.reviewAction(); c.confirmReview(true); c.activate(false); c.activate(true);
    await c.dispatch(); expect(api.followUp).not.toHaveBeenCalled();
    c.reviewAction(); c.confirmReview(true); await c.readSnapshot(); await c.dispatch(); expect(api.followUp).not.toHaveBeenCalled();
    c.reviewAction(); c.confirmReview(true); await c.dispatch(); expect(api.followUp).toHaveBeenCalledOnce();
    expect(api.followUp).toHaveBeenCalledWith(childParent, childId, { confirmed: true, prompt: "next", expected_generation: 1 });
    expect(c.state.followDraft).toBe("next"); expect(c.state.snapshot).toBeNull(); // explicit fresh metadata before another action
    expect(c.state.lastReceipt?.intent.expected_generation).toBe(1);
  });
  it("pins original intent/draft through live and failed POSTs, including apparent 4xx", async () => {
    const { api, c } = fixture(); await selected(c); reviewed(c);
    const flight = deferred<never>(); api.followUp.mockImplementation(() => flight.promise);
    const work = c.dispatch(); await ticks();
    expect(c.selectSource(otherParent)).toBe(false); c.setDraft("follow_up", "replacement"); c.chooseAction("spawn");
    expect(c.state.followDraft).toBe(" next "); expect(c.state.intent?.action).toBe("follow_up");
    flight.reject(new Error("HTTP 409 PRIVATE")); await work;
    expect(c.state.uncertain).toBe(true); expect(c.state.intent?.body).toEqual({ confirmed: true, prompt: "next", expected_generation: 1 });
    c.activate(false); c.activate(true); await c.readSnapshot(); await c.dispatch();
    expect(api.snapshot).toHaveBeenCalledOnce(); expect(api.followUp).toHaveBeenCalledOnce();
    expect(c.state.error).not.toContain("PRIVATE");
    await expect(c.prepareClose()).rejects.toThrow("未知"); c.finishClose(); c.activate(true);
    c.acknowledgeExit(true); c.acknowledgeDraftExit(true); await c.prepareClose(); c.finishClose(); c.activate(true);
    expect(c.selectSource(otherParent)).toBe(false); expect(c.state.uncertain).toBe(true); await c.dispatch();
    expect(api.followUp).toHaveBeenCalledOnce();
  });
  it("retains a late valid original ACK until explicit local adoption, without clearing the draft", async () => {
    const { api, c } = fixture(); await selected(c); reviewed(c);
    const flight = deferred<ReturnType<typeof childAdmission>>(); api.followUp.mockImplementation(() => flight.promise);
    const work = c.dispatch(); c.activate(false); flight.resolve(childAdmission()); await work; c.activate(true);
    expect(c.state.deferredAcknowledgement).toBe(true); expect(c.state.lastReceipt).toBeNull();
    expect(c.selectSource(otherParent)).toBe(false); c.showOriginalReceipt();
    expect(c.state.intent).toBeNull(); expect(c.state.lastReceipt?.receipt).toEqual(childAdmission());
    expect(c.state.followDraft).toBe(" next "); expect(api.followUp).toHaveBeenCalledOnce();
  });
  it("joins the original flight on close and rejects superseded preparation after recovery", async () => {
    const { api, c } = fixture(); await selected(c); reviewed(c); c.acknowledgeDraftExit(true);
    const flight = deferred<ReturnType<typeof childAdmission>>(); api.followUp.mockImplementation(() => flight.promise);
    const work = c.dispatch(); let closed = false;
    const close = c.prepareClose().then(() => { closed = true; }); const check = expect(close).rejects.toThrow("替代");
    await ticks(); expect(closed).toBe(false); c.finishClose(); c.activate(true);
    expect(c.state.busy).toBe(true); expect(c.selectSource(otherParent)).toBe(false);
    flight.resolve(childAdmission()); await work; await check;
    expect(c.state.deferredAcknowledgement).toBe(true); expect(closed).toBe(false);
  });
  it("never silently rebinds an older follow-up draft to a newer generation", async () => {
    const { api, c } = fixture(); await selected(c); c.chooseAction("follow_up"); c.setDraft("follow_up", "old draft");
    api.snapshot.mockResolvedValue(childSnapshot(2)); await c.readSnapshot();
    expect(c.state.draftGeneration).toBe(1); expect(c.reviewAction()).toBe(false);
    c.adoptChildGeneration(); expect(c.state.draftGeneration).toBe(2); expect(c.state.followDraft).toBe("old draft");
    expect(c.reviewAction()).toBe(true); expect(c.state.review?.expected_generation).toBe(2);
  });
  it("uses full generation-pinned older pages and validates injected replies again", async () => {
    const { api, c } = fixture(); api.snapshot.mockResolvedValue(childSnapshot(20)); await selected(c);
    const snapshot = childSnapshot(20), source = snapshot.items[0]!;
    api.history.mockResolvedValue({ ...source, history_offset: 0, history: Array.from({ length: 16 }, (_, i) => ({ prompt: `p${i}`, answer: `a${i}` })),
      service: snapshot.service, output: snapshot.output, physical_drain_verified: false });
    await c.readHistory(0); expect(api.history).toHaveBeenCalledWith(childParent, childId, 20, 0, 16);
    expect(c.state.page?.history[0]?.prompt).toBe("p0");
    api.history.mockResolvedValue({ ...c.state.page, generation: 21 }); await c.readHistory(0);
    expect(c.state.error).toContain("读取"); expect(c.state.page).toBeNull();
  });
  it("cancels only the explicitly reviewed queued/running generation and does not claim drain", async () => {
    const { api, c } = fixture(); const value = childSnapshot(); value.items[0]!.status = "running"; value.counts.durable_active_for_parent = 1;
    api.snapshot.mockResolvedValue(value); await selected(c); c.chooseAction("cancel"); c.reviewAction(); c.confirmReview(true);
    api.cancel.mockResolvedValue({ parent_run_id: childParent, subagent_id: childId, reviewed_generation: 1, cancel_requested: true, physical_drain_verified: false });
    await c.dispatch(); expect(api.cancel).toHaveBeenCalledWith(childParent, childId, { confirmed: true, expected_generation: 1 });
    expect(c.state.lastReceipt?.receipt.physical_drain_verified).toBe(false); expect(c.state.snapshot).toBeNull();
  });
  it("does not spawn beyond the lifetime budget or follow a noncompleted child", async () => {
    const { api, c } = fixture(); const value = childSnapshot(); value.limits.lifetime_per_parent = 1;
    value.items[0]!.status = "running"; value.counts.durable_active_for_parent = 1; api.snapshot.mockResolvedValue(value);
    await selected(c); c.setDraft("spawn", "new"); expect(c.reviewAction()).toBe(false);
    c.chooseAction("follow_up"); c.setDraft("follow_up", "next"); expect(c.reviewAction()).toBe(false);
    await c.dispatch(); expect(api.spawn).not.toHaveBeenCalled(); expect(api.followUp).not.toHaveBeenCalled();
  });
  it("draft exit acknowledgment never deletes drafts or unlocks unknown effects", async () => {
    const { c } = fixture(); c.activate(true); c.selectSource(childParent); c.setDraft("spawn", "keep me");
    await expect(c.prepareClose()).rejects.toThrow("草稿"); c.finishClose(); c.activate(true);
    c.acknowledgeDraftExit(true); await c.prepareClose(); expect(c.state.spawnDraft).toBe("keep me");
    c.finishClose(); c.activate(true); c.setDraft("spawn", "edited"); expect(c.unloadBlocked).toBe(true);
  });
});
