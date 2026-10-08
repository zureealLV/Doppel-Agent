// Compiled setup/watch/model handlers in synthetic host, UNRUN. Not DOM/native/layout QA.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SubagentPanel from "./components/SubagentPanel.vue";
import { ChildReviewController } from "./childReview";
import { childAdmission, childId, childParent, childSnapshot, otherParent } from "./testSupport/childReviewFixture";
import { button, checkLabel, click, descendants, flushVue, mountVirtualProps, type VirtualNode } from "./testSupport/virtualHost";
const mounts: Array<{ unmount(): void }> = [];
function fixture() {
  const api = { snapshot: vi.fn(async () => childSnapshot()), history: vi.fn(), spawn: vi.fn(),
    followUp: vi.fn(async () => childAdmission()), cancel: vi.fn() };
  const c = new ChildReviewController(api); c.activate(true); c.selectSource(childParent);
  const mounted = mountVirtualProps(SubagentPanel, { controller: c, active: true, blocked: false }); mounts.push(mounted);
  return { api, c, mounted };
}
async function model(root: VirtualNode, tag: string, match: string, value: unknown) {
  const node = descendants(root).find(node => node.tag === tag && (node.props['aria-label'] === match
    || node.parent?.textContent.includes(match)));
  if (!node) throw new Error(`synthetic model missing ${match}`);
  const update = node.props['onUpdate:modelValue']; if (typeof update !== 'function') throw new Error('compiled model missing');
  update(value); await flushVue();
}
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function select(root: VirtualNode) { await click(button(root, '明确读取该父任务目录')); await flushVue(); await model(root, 'select', '原子任务', childId); }
async function review(root: VirtualNode) {
  await model(root, 'select', '子任务动作', 'follow_up'); await model(root, 'textarea', '追问原草稿', ' next ');
  await click(button(root, '准备并展示本次确认请求')); await flushVue();
  await checkLabel(root, '我确认该父任务');
}
beforeEach(() => { vi.stubGlobal('Document', class Document {}); vi.stubGlobal('ShadowRoot', class ShadowRoot {}); });
afterEach(() => { for (const mounted of mounts.splice(0)) mounted.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('root-retained child panel model definitions', () => {
  it('mount/visibility/source selection do not read or mutate', async () => {
    const { api, c, mounted: m } = fixture(); await flushVue();
    m.props.active = false; await flushVue(); m.props.active = true; await flushVue(); c.selectSource(otherParent); await flushVue();
    for (const call of Object.values(api)) expect(call).not.toHaveBeenCalled();
    expect(m.root.textContent).toContain(otherParent); expect(m.root.textContent).toContain('只读');
  });
  it('edits and blocked/reentry consume consent; dispatch keeps raw drafts', async () => {
    const { api, c, mounted: m } = fixture(); await select(m.root); await review(m.root);
    m.props.blocked = true; await flushVue(); m.props.blocked = false; await flushVue();
    await c.dispatch(); expect(api.followUp).not.toHaveBeenCalled();
    await click(button(m.root, '准备并展示本次确认请求')); await flushVue(); await checkLabel(m.root, '我确认该父任务');
    await click(button(m.root, '执行本次确认动作')); await flushVue();
    expect(api.followUp).toHaveBeenCalledWith(childParent, childId, { confirmed: true, prompt: 'next', expected_generation: 1 });
    expect(c.state.followDraft).toBe(' next '); expect(m.root.textContent).toContain('不是当前状态');
    expect(c.state.snapshot).toBeNull();
  });
  it('panel unmount never disposes original root promise; remount shows only deferred ACK', async () => {
    const { api, c, mounted: m } = fixture(); await select(m.root); await review(m.root);
    const flight = deferred<ReturnType<typeof childAdmission>>(); api.followUp.mockImplementation(() => flight.promise);
    const work = click(button(m.root, '执行本次确认动作')); await flushVue(); m.unmount();
    expect(c.state.busy).toBe(true); expect(c.selectSource(otherParent)).toBe(false);
    flight.resolve(childAdmission()); await work;
    const next = mountVirtualProps(SubagentPanel, { controller: c, active: true, blocked: false }); mounts.push(next); await flushVue();
    expect(c.state.lastReceipt).toBeNull(); expect(c.state.deferredAcknowledgement).toBe(true);
    await click(button(next.root, '明确展示迟到的原回执')); await flushVue();
    expect(c.state.followDraft).toBe(' next '); expect(api.followUp).toHaveBeenCalledOnce();
  });
  it('unknown requires distinct exit/draft acknowledgments and never unlocks child source', async () => {
    const { api, c, mounted: m } = fixture(); await select(m.root); await review(m.root);
    api.followUp.mockRejectedValue(new Error('PRIVATE_409')); await click(button(m.root, '执行本次确认动作')); await flushVue();
    expect(c.state.uncertain).toBe(true); expect(m.root.textContent).not.toContain('PRIVATE_409');
    await checkLabel(m.root, '我知悉原请求仍未知');
    await expect(c.prepareClose()).rejects.toThrow('草稿'); c.finishClose(); c.activate(true); await flushVue();
    await checkLabel(m.root, '我知悉本页全部子任务草稿'); await c.prepareClose(); c.finishClose(); c.activate(true);
    expect(c.selectSource(otherParent)).toBe(false); await c.readSnapshot(); await c.dispatch();
    expect(api.snapshot).toHaveBeenCalledOnce(); expect(api.followUp).toHaveBeenCalledOnce();
  });
  it('newer metadata keeps old draft binding until explicit compiled adoption control', async () => {
    const { api, c, mounted: m } = fixture(); await select(m.root); await review(m.root);
    api.snapshot.mockResolvedValue(childSnapshot(2)); await click(button(m.root, '明确读取该父任务目录')); await flushVue();
    expect(c.state.draftGeneration).toBe(1); expect(c.reviewAction()).toBe(false);
    await click(button(m.root, '明确将保留草稿绑定')); await flushVue();
    expect(c.state.draftGeneration).toBe(2); expect(c.state.followDraft).toBe(' next ');
  });
});
