// Actual compiled App/RunInspector/SubagentPanel/RunReportPanel + original host controllers.
// Other workspaces/host UI are fixture stubs. UNRUN; no browser/native/OS drain proof.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChildReviewController } from "./childReview";
import { subagentReviewApi } from "./subagentReviewApi";
import { RunReportController } from "./runReport";
import { runReportApi } from "./runReportApi";
import { reportFixture } from "./testSupport/runReportFixture";
import { childAdmission, childId, childParent, childSnapshot, otherParent } from "./testSupport/childReviewFixture";
import { button, checkLabel, click, descendants, flushVue, mountVirtualProps, type VirtualNode } from "./testSupport/virtualHost";
import type { RunRecord } from "./types";
import App from "./App.vue";
import type { ModelSettingsLifetime } from './modelSettingsLifetime';
import type { PublicSettings } from './workspaceTypes';

const host = vi.hoisted(() => ({
  child: null as ChildReviewController | null,
  report: null as RunReportController | null,
  desktop: null as import('./desktopBridge').DesktopController | null,
  project: null as import('./projectController').ProjectController | null,
  settings: null as ModelSettingsLifetime | null,
  inspectorControls: {} as Record<string, { show(value: boolean): void; select(value: string): void }>,
  steps: [] as string[], windowAction: vi.fn(), projectSwitch: vi.fn(), navigate: vi.fn(),
}));
vi.mock('./components/ConversationWorkspace.vue', async () => {
  const { defineComponent, h, ref, inject } = await import('vue'); const { default: Inspector } = await import('./components/RunInspector.vue');
  const { modelSettingsLifetimeKey } = await import('./modelSettingsLifetime');
  return { default: defineComponent({ props: ['active'], setup(_props, { expose }) {
    host.settings = inject(modelSettingsLifetimeKey) ?? null;
    const showing = ref(true), run = ref('c'.repeat(32));
    host.inspectorControls.conversation = { show: value => { showing.value = value; }, select: value => { run.value = value; } };
    expose({ prepareClose: async () => { host.steps.push('conversation'); }, finishClose() {}, openSearch() {} });
    return () => h('div', { 'data-fixture': 'conversation' }, showing.value ? [h(Inspector, { runId: run.value, conversationId: 'f'.repeat(32) })] : []);
  } }) };
});
vi.mock('./components/WorkOrdersWorkspace.vue', async () => {
  const { defineComponent, h, ref } = await import('vue'); const { default: Inspector } = await import('./components/RunInspector.vue');
  return { default: defineComponent({ props: ['active'], setup(_props, { expose }) {
    const showing = ref(true), run = ref('d'.repeat(32));
    host.inspectorControls.order = { show: value => { showing.value = value; }, select: value => { run.value = value; } };
    expose({ prepareClose: async () => { host.steps.push('order'); }, finishClose() {} });
    return () => h('div', { 'data-fixture': 'order' }, showing.value ? [h(Inspector, { runId: run.value })] : []);
  } }) };
});
vi.mock('./components/ContextPanel.vue', async () => {
  const { defineComponent, h } = await import('vue'); return { default: defineComponent({ setup(_p, { expose }) {
    expose({ prepareClose: async () => { host.steps.push('context'); }, finishClose() {} }); return () => h('div');
  } }) };
});
vi.mock('./components/ChangesWorkspace.vue', async () => {
  const { defineComponent, h } = await import('vue'); return { default: defineComponent({ setup(_p, { expose }) {
    expose({ prepareClose: async () => { host.steps.push('changes'); }, finishClose() {}, canChangeSource: () => true }); return () => h('div');
  } }) };
});
vi.mock('./components/ExtensionCenter.vue', async () => {
  const { defineComponent, h } = await import('vue'); return { default: defineComponent({ setup(_p, { expose }) {
    expose({ prepareClose: async () => { host.steps.push('extensions'); }, finishClose() {} }); return () => h('div');
  } }) };
});
vi.mock('./components/LegacyConversationWorkspace.vue', async () => {
  const { defineComponent, h } = await import('vue'); return { default: defineComponent({ setup(_p, { expose }) {
    expose({ prepareClose: async () => { host.steps.push('legacy'); }, prepareProjectSwitch: async () => { host.steps.push('legacy'); } }); return () => h('div');
  } }) };
});
vi.mock('./components/RunComposer.vue', async () => {
  const { defineComponent, h } = await import('vue'); return { default: defineComponent({ setup: () => () => h('div') }) };
});
vi.mock('./components/DesktopControls.vue', async () => {
  const { defineComponent, h } = await import('vue'); const { DesktopController } = await import('./desktopBridge');
  return { default: defineComponent({ props: ['beforeClose', 'afterClose'], setup(props) {
    const controller = new DesktopController({ beforeClose: () => props.beforeClose(), afterClose: result => props.afterClose(result) });
    controller.attach({ window_action: host.windowAction }); host.desktop = controller;
    return () => h('button', { onClick: () => controller.act('close') }, 'fixture-close');
  } }) };
});
vi.mock('./components/ProjectHome.vue', async () => {
  const { defineComponent, h } = await import('vue'); const { ProjectController } = await import('./projectController');
  return { default: defineComponent({ props: ['beforeSwitch', 'afterSwitch', 'blocked'], setup(props) {
    const controller = new ProjectController({ beforeSwitch: () => props.beforeSwitch(), afterSwitch: value => props.afterSwitch(value), navigate: host.navigate });
    controller.attach({ project_list: async () => ({ ok: true, projects: [] }), project_choose: async () => ({ ok: false }), project_switch: host.projectSwitch });
    controller.state.projects = [{ project_id: 'e'.repeat(32), name: 'fixture', path: 'fixture-only', opened_at_ns: 1, selected_at_ns: 1 }]; host.project = controller;
    return () => h('button', { disabled: props.blocked, onClick: () => controller.switchTo('e'.repeat(32), true) }, 'fixture-switch');
  } }) };
});

function row(id: string): RunRecord {
  return { run_id: id, conversation_id: 'f'.repeat(32), thread_id: id, status: 'completed', mode: 'graph',
    request: { prompt: 'fixture parent', mode: 'graph', effort: 'balanced', deadline_seconds: 600,
      permissions: { workspace_write: false, command_execute: false, mcp_execute: false, delegate: true } },
    answer: 'fixture parent answer', error: '', metadata: {}, created_at: '', updated_at: '', started_at: null, finished_at: null, cancel_requested: false };
}
function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const mounts: Array<{ unmount(): void }> = [];
let unload: EventListener | undefined;
function ancestor(node: VirtualNode, fixture: string): boolean {
  for (let current: VirtualNode | null = node; current; current = current.parent) if (current.props['data-fixture'] === fixture) return true;
  return false;
}
async function open(root: VirtualNode, fixture: 'conversation' | 'order' | 'standalone') {
  const node = descendants(root).find(node => node.tag === 'button' && node.textContent.includes('明确打开此父任务')
    && (fixture === 'standalone' ? !ancestor(node, 'conversation') && !ancestor(node, 'order') : ancestor(node, fixture)));
  if (!node) throw new Error('compiled root inspector entry missing'); click(node); await flushVue(); return host.child!;
}
async function openReport(root: VirtualNode, fixture: 'conversation' | 'order' | 'standalone') {
  const node = descendants(root).find(node => node.tag === 'button' && node.textContent.includes('明确打开此原生任务的脱敏报告')
    && (fixture === 'standalone' ? !ancestor(node, 'conversation') && !ancestor(node, 'order') : ancestor(node, fixture)));
  if (!node) throw new Error('compiled root report entry missing'); click(node); await flushVue(); return host.report!;
}
async function mount() { const m = mountVirtualProps(App, {}); mounts.push(m); await flushVue(); return m; }
async function reviewed(root: VirtualNode) {
  await click(button(root, '明确读取该父任务目录')); await flushVue();
  const c = host.child!; c.selectChild(childId); c.chooseAction('follow_up'); c.setDraft('follow_up', ' next '); c.reviewAction(); c.confirmReview(true); await flushVue();
}
function callUnload() { const event = { preventDefault: vi.fn(), returnValue: 'unchanged' }; unload?.(event as unknown as Event); return event; }
beforeEach(() => {
  vi.useFakeTimers(); host.child = null; host.report = null; host.settings = null; host.steps = []; host.inspectorControls = {};
  host.windowAction.mockReset().mockResolvedValue(false); host.projectSwitch.mockReset().mockResolvedValue({ ok: false }); host.navigate.mockReset();
  vi.stubGlobal('Document', class Document {}); vi.stubGlobal('ShadowRoot', class ShadowRoot {});
  const windowFixture = { location: { hash: '#runtime' }, setInterval: vi.fn(() => 1), clearInterval: vi.fn(),
    localStorage: { getItem: (key: string) => key === 'doppel.runtime.lastRun' ? childParent : null, setItem: vi.fn() },
    addEventListener: vi.fn((kind: string, callback: EventListener) => { if (kind === 'beforeunload') unload = callback; }), removeEventListener: vi.fn() };
  vi.stubGlobal('window', windowFixture); vi.stubGlobal('document', { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    let value: unknown;
    if (path.endsWith('/health')) value = { status: 'ok' };
    else if (path.endsWith('/projects/current')) value = { name: 'fixture', path: 'fixture-only', switching_available: false };
    else if (path.includes('/events?')) value = [];
    else { const match = path.match(/\/runs\/([a-f0-9]{32})$/); if (!match) throw new Error('unexpected fixture request'); value = row(match[1]!); }
    return { ok: true, json: async () => value };
  }));
  vi.spyOn(subagentReviewApi, 'snapshot').mockImplementation(async parent => {
    const value = childSnapshot(); value.parent_run_id = parent; value.items[0]!.parent_run_id = parent; return value;
  });
  vi.spyOn(subagentReviewApi, 'followUp').mockResolvedValue(childAdmission());
  vi.spyOn(subagentReviewApi, 'spawn').mockRejectedValue(new Error('unexpected spawn'));
  vi.spyOn(subagentReviewApi, 'cancel').mockRejectedValue(new Error('unexpected cancel'));
  vi.spyOn(runReportApi, 'read').mockImplementation(async run => reportFixture(run));
  vi.spyOn(runReportApi, 'download').mockImplementation(async run => reportFixture(run));
  vi.spyOn(runReportApi, 'save').mockResolvedValue(); // no DOM/browser/download IO
  const reportSelect = RunReportController.prototype.selectSource;
  vi.spyOn(RunReportController.prototype, 'selectSource').mockImplementation(function (this: RunReportController, run) {
    if (host.report && host.report !== this) throw new Error('multiple root report owners'); host.report = this; return reportSelect.call(this, run);
  });
  const select = ChildReviewController.prototype.selectSource;
  vi.spyOn(ChildReviewController.prototype, 'selectSource').mockImplementation(function (this: ChildReviewController, parent) {
    if (host.child && host.child !== this) throw new Error('multiple root owners'); host.child = this; return select.call(this, parent);
  });
});

describe('standalone runtime helper cleanup in the actual compiled App', () => {
  it('removes the always-visible history note outside the composer', async () => {
    const m = await mount();
    expect(descendants(m.root).some(node => node.props.class === 'standalone-workspace')).toBe(true);
    expect(m.root.textContent).not.toContain('独立运行生成自己的原生会话，不导入已有对话或 Legacy 历史。');
  });
});

describe('C1 actual compiled App settings/probe lifetime with synthetic host', () => {
  it.each(['close', 'switch'] as const)('root %s joins an entered original settings/probe request even after dialog detach', async action => {
    await mount(); const c = host.settings; if (!c) throw new Error('root settings owner missing');
    const token = c.attach(), flight = deferred<PublicSettings>();
    const invoke = vi.fn(() => flight.promise), work = c.perform(token, { kind: 'save', profileId: 'fixture', invoke }); await flushVue(); c.detach(token);
    expect(callUnload().preventDefault).toHaveBeenCalledOnce();
    const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    flight.resolve({ active_profile_id: 'fixture', profiles: [], key_protection: 'fixture' }); await work; await closing; await flushVue();
    expect(c.state.deferred).toBe(true); expect(invoke).toHaveBeenCalledOnce();
    expect(action === 'close' ? host.windowAction : host.projectSwitch).toHaveBeenCalledOnce(); expect(host.navigate).not.toHaveBeenCalled();
  });
  it.each(['close', 'switch'] as const)('timeout recovery for settings %s keeps original promise and prevents the old late host transition', async action => {
    await mount(); const c = host.settings; if (!c) throw new Error('root settings owner missing');
    const token = c.attach(), flight = deferred<{ ok: boolean; reply: string }>();
    const invoke = vi.fn(() => flight.promise), work = c.perform(token, { kind: 'probe', profileId: 'fixture', invoke }); await flushVue();
    const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); await vi.advanceTimersByTimeAsync(5100); await closing; await flushVue();
    expect(c.state.busy).toBe(true); expect(c.state.closing).toBe(false);
    flight.resolve({ ok: true, reply: 'offline-only' }); await work; await flushVue();
    expect(c.takeReply(token)).toBeNull(); expect(c.state.deferred).toBe(true);
    expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    expect(invoke).toHaveBeenCalledOnce(); expect(host.navigate).not.toHaveBeenCalled();
  });
});

describe('actual compiled root report ownership with synthetic host fixtures', () => {
  it('inspector labels flat usage as model callbacks without provider layer duplication or report IO', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      let value: unknown;
      if (path.endsWith('/health')) value = { status: 'ok' };
      else if (path.endsWith('/projects/current')) value = { name: 'fixture', path: 'fixture-only', switching_available: false };
      else {
        const match = path.match(/\/runs\/([a-f0-9]{32})(?:\/events\?.*)?$/);
        if (!match) throw new Error('unexpected fixture request');
        const run = match[1]!, call = '8e23f935b2ae4017a37799e324ab3038';
        value = path.includes('/events?') ? [
          { seq: 9, type: 'provider.request_finished', payload: { call_id: call, usage: { input_tokens: 100, output_tokens: 10 } } },
          { seq: 10, type: 'provider.call_finished', payload: { call_id: call, usage: { input_tokens: 100, output_tokens: 10 } } },
          { seq: 11, type: 'graph.model_finished', payload: { provider_call_id: call, usage: { prompt_tokens: 100, completion_tokens: 10 } } },
        ].map(e => ({ ...e, run_id: run, thread_id: run, timestamp: '2026-10-06T20:37:42+00:00' })) : row(run);
      }
      return { ok: true, json: async () => value };
    }));
    const m = await mount();
    expect(m.root.textContent).toContain('模型完成回调 Token 100 输入 / 10 输出（1 条有效回调）');
    expect(m.root.textContent).toContain('不是去重后的 provider 主计量或完整账单');
    expect(m.root.textContent).not.toContain('300 输入 / 30 输出');
    expect(runReportApi.read).not.toHaveBeenCalled();
    expect(runReportApi.download).not.toHaveBeenCalled();
  });
  it('three actual inspector entries share one root; page/hide/reentry do not read or download', async () => {
    const m = await mount(); const c = await openReport(m.root, 'conversation'); expect(c.state.runId).toBe(otherParent);
    await openReport(m.root, 'order'); expect(host.report).toBe(c); expect(c.state.runId).toBe('d'.repeat(32));
    await openReport(m.root, 'standalone'); expect(c.state.runId).toBe(childParent);
    host.inspectorControls.conversation!.show(false); host.inspectorControls.order!.show(false);
    await click(button(m.root, '计划与任务')); await flushVue();
    await click(button(m.root, '隐藏运行报告')); await flushVue(); await click(button(m.root, '打开已固定的运行报告')); await flushVue();
    expect(runReportApi.read).not.toHaveBeenCalled(); expect(runReportApi.download).not.toHaveBeenCalled(); expect(runReportApi.save).not.toHaveBeenCalled();
  });
  it.each(['close', 'switch'] as const)('original report read joins root %s and retains late data without auto adoption', async action => {
    const m = await mount(), c = await openReport(m.root, 'standalone'), flight = deferred<ReturnType<typeof reportFixture>>();
    vi.mocked(runReportApi.read).mockReturnValue(flight.promise); const work = c.readReport();
    expect(callUnload().preventDefault).toHaveBeenCalledOnce();
    const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    flight.resolve(reportFixture()); await work; await closing; await flushVue();
    expect(c.state.report).toBeNull(); expect(c.state.deferred).toBe(true);
    expect(action === 'close' ? host.windowAction : host.projectSwitch).toHaveBeenCalledOnce();
    await click(button(m.root, '明确显示原请求的迟到快照')); await flushVue(); expect(c.state.report?.run_id).toBe(childParent);
    expect(runReportApi.read).toHaveBeenCalledOnce(); expect(runReportApi.save).not.toHaveBeenCalled();
  });
  it.each(['close', 'switch'] as const)('root %s recovery fences original report continuation without forgetting its promise', async action => {
    const m = await mount(), c = await openReport(m.root, 'standalone'), flight = deferred<ReturnType<typeof reportFixture>>();
    vi.mocked(runReportApi.download).mockReturnValue(flight.promise); const work = c.prepareExport();
    const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); await vi.advanceTimersByTimeAsync(5100); await closing; await flushVue();
    expect(c.state.closing).toBe(false); expect(c.state.busy).toBe(true); expect(callUnload().preventDefault).toHaveBeenCalledOnce();
    flight.resolve(reportFixture()); await work; await flushVue();
    expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    expect(c.state.exportSnapshot).toBeNull(); expect(c.state.deferred).toBe(true); expect(runReportApi.save).not.toHaveBeenCalled();
  });
  it.each(['close', 'switch'] as const)('root %s joins a started download-request promise, not an inferred file-completion receipt', async action => {
    const m = await mount(), c = await openReport(m.root, 'standalone'); await c.prepareExport(); c.confirmExport(true);
    const flight = deferred<void>(); vi.mocked(runReportApi.save).mockReturnValue(flight.promise);
    const work = c.exportPrepared(); await flushVue();
    const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled();
    flight.resolve(); await work; await closing; await flushVue();
    expect(c.state.exportState).toBe('requested'); expect(m.root.textContent).toContain('不证明文件写入完成');
    expect(action === 'close' ? host.windowAction : host.projectSwitch).toHaveBeenCalledOnce(); expect(runReportApi.save).toHaveBeenCalledOnce();
  });
});
afterEach(() => { for (const m of mounts.splice(0)) m.unmount(); unload = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('actual compiled root child ownership with synthetic host fixtures', () => {
  it('all three inspector sources use one root owner, with no automatic child read or disposal', async () => {
    const m = await mount(); expect(subagentReviewApi.snapshot).not.toHaveBeenCalled();
    const c = await open(m.root, 'conversation'); expect(c.state.parentRunId).toBe(otherParent);
    await open(m.root, 'order'); expect(host.child).toBe(c); expect(c.state.parentRunId).toBe('d'.repeat(32));
    await open(m.root, 'standalone'); expect(host.child).toBe(c); expect(c.state.parentRunId).toBe(childParent);
    c.setDraft('spawn', 'retained'); host.inspectorControls.conversation!.show(false); host.inspectorControls.order!.show(false);
    await click(button(m.root, '计划与任务')); await flushVue(); // standalone inspector unmounts
    expect(c.state.spawnDraft).toBe('retained'); expect(c.state.active).toBe(true); expect(subagentReviewApi.snapshot).not.toHaveBeenCalled();
  });
  it.each(['close', 'switch'] as const)('original pending read joins root %s without a late metadata adoption', async action => {
    const m = await mount(); const c = await open(m.root, 'standalone'); const flight = deferred<ReturnType<typeof childSnapshot>>();
    vi.mocked(subagentReviewApi.snapshot).mockReturnValue(flight.promise); const reading = c.readSnapshot();
    const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    flight.resolve(childSnapshot()); await reading; await closing; await flushVue();
    expect(c.state.snapshot).toBeNull(); expect(c.state.uncertain).toBe(false);
    expect(action === 'close' ? host.windowAction : host.projectSwitch).toHaveBeenCalledOnce();
  });
  it.each(['close', 'switch'] as const)('unknown + drafts require both narrow acknowledgments before root %s', async action => {
    const m = await mount(); const c = await open(m.root, 'standalone'); await reviewed(m.root);
    vi.mocked(subagentReviewApi.followUp).mockRejectedValue(new Error('PRIVATE_HTTP_409')); await c.dispatch(); await flushVue();
    const close = () => action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await close(); await flushVue(); expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled();
    expect(action === 'close' ? host.desktop!.state.error : host.project!.state.error).toContain('子任务');
    expect(action === 'close' ? host.desktop!.state.error : host.project!.state.error).not.toContain('PRIVATE');
    await checkLabel(m.root, '我知悉原请求仍未知'); await close(); await flushVue();
    expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled();
    await checkLabel(m.root, '我知悉本页全部子任务草稿'); await close(); await flushVue();
    expect(action === 'close' ? host.windowAction : host.projectSwitch).toHaveBeenCalledOnce(); expect(host.navigate).not.toHaveBeenCalled();
    expect(c.state.uncertain).toBe(true); expect(c.selectSource(otherParent)).toBe(false); expect(c.state.followDraft).toBe(' next ');
    expect(subagentReviewApi.followUp).toHaveBeenCalledOnce();
  });
  it.each(['close', 'switch'] as const)('root %s joins the original reviewed mutation before invoking the host once', async action => {
    const m = await mount(); const c = await open(m.root, 'standalone'); await reviewed(m.root); c.acknowledgeDraftExit(true);
    const flight = deferred<ReturnType<typeof childAdmission>>(); vi.mocked(subagentReviewApi.followUp).mockReturnValue(flight.promise);
    const work = c.dispatch(); const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    flight.resolve(childAdmission()); await work; await closing; await flushVue();
    expect(action === 'close' ? host.windowAction : host.projectSwitch).toHaveBeenCalledOnce();
    expect(c.state.deferredAcknowledgement).toBe(true); expect(c.state.lastReceipt).toBeNull(); expect(c.state.followDraft).toBe(' next ');
    expect(subagentReviewApi.followUp).toHaveBeenCalledOnce(); expect(host.navigate).not.toHaveBeenCalled();
  });
  it.each(['close', 'switch'] as const)('timeout recovery fences original late ACK/root %s and keeps original promise', async action => {
    const m = await mount(); const c = await open(m.root, 'standalone'); await reviewed(m.root); c.acknowledgeDraftExit(true);
    const flight = deferred<ReturnType<typeof childAdmission>>(); vi.mocked(subagentReviewApi.followUp).mockReturnValue(flight.promise);
    const work = c.dispatch(); const closing = action === 'close' ? host.desktop!.act('close') : host.project!.switchTo('e'.repeat(32), true);
    await flushVue(); await vi.advanceTimersByTimeAsync(5100); await closing; await flushVue();
    expect(c.state.busy).toBe(true); expect(c.state.closing).toBe(false); expect(c.selectSource(otherParent)).toBe(false);
    flight.resolve(childAdmission()); await work; await flushVue();
    expect(c.state.deferredAcknowledgement).toBe(true); expect(c.state.lastReceipt).toBeNull();
    expect(host.windowAction).not.toHaveBeenCalled(); expect(host.projectSwitch).not.toHaveBeenCalled(); expect(host.steps).toEqual([]);
    expect(host.navigate).not.toHaveBeenCalled(); expect(subagentReviewApi.followUp).toHaveBeenCalledOnce();
  });
  it('same-tick hide/unmount pins a late original ACK and beforeunload covers hidden drafts/unknowns', async () => {
    const m = await mount(); const c = await open(m.root, 'standalone'); await reviewed(m.root);
    expect(callUnload().preventDefault).toHaveBeenCalledOnce();
    const flight = deferred<ReturnType<typeof childAdmission>>(); vi.mocked(subagentReviewApi.followUp).mockReturnValue(flight.promise);
    const work = c.dispatch(); click(button(m.root, '隐藏子任务审查')); expect(c.state.active).toBe(false);
    await click(button(m.root, '计划与任务')); await flushVue(); expect(c.state.busy).toBe(true);
    flight.resolve(childAdmission()); await work; await flushVue(); expect(c.state.deferredAcknowledgement).toBe(true);
    click(button(m.root, '打开已固定的子任务审查')); await flushVue();
    await click(button(m.root, '明确展示迟到的原回执')); await flushVue(); expect(c.state.followDraft).toBe(' next ');
    await checkLabel(m.root, '我知悉本页全部子任务草稿'); expect(callUnload().preventDefault).not.toHaveBeenCalled();
  });
});
