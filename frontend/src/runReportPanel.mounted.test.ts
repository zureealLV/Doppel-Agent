// Compiled SFC + synthetic renderer definitions, ALL UNRUN. Not native download,
// real DOM dispatch, layout, accessibility/focus or physical cleanup evidence.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RunReportPanel from "./components/RunReportPanel.vue";
import { RunReportController } from "./runReport";
import { reportFixture, reportRun } from "./testSupport/runReportFixture";
import { reportV2Fixture } from './testSupport/reportEvidenceFixture';
import { reportV3Fixture } from './testSupport/providerReportFixture';
import { costSubset, reportV4ChildrenFixture, reportV4Fixture, reportV4OverflowFixture } from './testSupport/providerCostFixture';
import { button, checkLabel, click, flushVue, mountVirtualProps } from "./testSupport/virtualHost";
const mounts: Array<{ unmount(): void }> = [];
function fixture() {
  const api = { read: vi.fn(async (): Promise<unknown> => reportFixture()), download: vi.fn(async (): Promise<unknown> => reportFixture()), save: vi.fn(async () => {}) };
  const c = new RunReportController(api); c.activate(true); c.selectSource(reportRun);
  const m = mountVirtualProps(RunReportPanel, { controller: c, active: true, blocked: false }); mounts.push(m); return { c, api, m };
}
beforeEach(() => { vi.stubGlobal("Document", class Document {}); vi.stubGlobal("ShadowRoot", class ShadowRoot {}); });
afterEach(() => { for (const m of mounts.splice(0)) m.unmount(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("report panel original controller", () => {
  it('shows original provider/cleanup quarantine separately from an actual failed close, without triggering execution', async () => {
    const { m, api } = fixture(), wire = reportV4Fixture(); wire.service.execution_admission = 'quarantined';
    api.read.mockResolvedValue(wire); await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('原执行准入已隔离');
    expect(m.root.textContent).toContain('不重试未知调用');
    expect(m.root.textContent).not.toContain('原服务关闭失败');
    expect(api.read).toHaveBeenCalledOnce(); expect(api.download).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
  });
  it('shows4 exact declared amount/provenance/scope but never invoice/source/cap authority or global unknown text', async () => {
    const { m, api } = fixture(); api.read.mockResolvedValue(reportV4Fixture()); await flushVue();
    expect(api.read).not.toHaveBeenCalled(); await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('声明费率估算：不是账单');
    expect(m.root.textContent).toContain('0.00001625 CNY'); expect(m.root.textContent).toContain('可计价 1 / 未知 0');
    expect(m.root.textContent).toContain('2026-10-01'); expect(m.root.textContent).toContain('2026-10-06');
    expect(m.root.textContent).toContain('来源未验证'); expect(m.root.textContent).toContain('不保证账户上限');
    expect(m.root.textContent).toContain('父任务费用归属'); expect(m.root.textContent).not.toContain('当前报告未提供计费价格投影');
    expect(m.root.textContent).not.toContain('费用未知，不是免费；');
    expect(api.download).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
  });
  it('shows4 price-unknown and closed reasons without losing known primary usage or pretending zero', async () => {
    const { m, api } = fixture(), wire = reportV4Fixture();
    Object.assign(wire.cost, { ...costSubset(false, true), state: 'unknown', source_state: 'missing', price_snapshot: null });
    wire.cost.scopes.root = costSubset(false, true); api.read.mockResolvedValue(wire);
    await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('可计价 0 / 未知 1'); expect(m.root.textContent).toContain('费用未知，不是免费');
    expect(m.root.textContent).toContain('缺少冻结费率：1'); expect(m.root.textContent).toMatch(/观测合计\s*10/);
    expect(api.read).toHaveBeenCalledOnce(); expect(api.save).not.toHaveBeenCalled();
  });
  it('prepared4 presentation remains tied to exported price snapshot even a historical display is read later', async () => {
    const { m, api } = fixture(); api.download.mockResolvedValue(reportV4Fixture());
    await click(button(m.root, '明确准备导出快照')); await flushVue();
    expect(m.root.textContent).toContain('导出费用状态 known_for_declared_observations');
    expect(m.root.textContent).toContain('导出估算 0.00001625 CNY');
    await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('导出估算 0.00001625 CNY'); expect(api.save).not.toHaveBeenCalled();
  });
  it('shows partial18 child scopes including unknown other_children, and exact large cost strings without rounding', async () => {
    const { m, api } = fixture(); api.read.mockResolvedValue(reportV4ChildrenFixture());
    await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('子费用作用域 16 / 18'); expect(m.root.textContent).toContain('未逐项展示 2');
    expect(m.root.textContent).toContain('其余子任务费用归属：0.00001625 CNY');
    expect(m.root.textContent).toContain('费用状态 partial'); expect(m.root.textContent).toContain('未知或省略的观测不补零');
    api.read.mockResolvedValue(reportV4OverflowFixture()); await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('22517998136.8524775 CNY');
    expect(m.root.textContent).toContain('18014398509481982'); expect(m.root.textContent).toContain('所选小计超出 safe integer');
    expect(api.download).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
  });
  it("mount/visibility/reentry do not fetch; explicit view remains honest about partial billing and redaction", async () => {
    const { m, api } = fixture(); await flushVue(); m.props.active = false; await flushVue(); m.props.active = true; await flushVue();
    expect(api.read).not.toHaveBeenCalled(); expect(api.download).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
    await click(button(m.root, "明确读取白名单报告")); await flushVue();
    expect(api.read).toHaveBeenCalledOnce(); expect(m.root.textContent).toContain("不等于完整账单");
    expect(m.root.textContent).toContain("不是匿名"); expect(m.root.textContent).toContain("费用未知");
  });
  it("uses two explicit export stages, prepared counters, and consumed fresh consent", async () => {
    const { m, api } = fixture(); await flushVue(); await click(button(m.root, "明确准备导出快照")); await flushVue();
    expect(api.download).toHaveBeenCalledOnce(); expect(api.save).not.toHaveBeenCalled();
    await checkLabel(m.root, "我知悉标识可关联"); await click(button(m.root, "明确请求下载已准备的 JSON")); await flushVue();
    expect(api.save).toHaveBeenCalledOnce(); expect(m.root.textContent).toContain("不证明文件写入完成");
  });
  it("panel unmount does not dispose root or drop a pending read", async () => {
    const { c, m, api } = fixture(); let resolve!: (value: ReturnType<typeof reportFixture>) => void;
    api.read.mockImplementation(() => new Promise(yes => { resolve = yes; }));
    const original = c.readReport(); await flushVue(); m.unmount();
    let closed = false; const close = c.prepareClose().then(() => { closed = true; }); await flushVue(); expect(closed).toBe(false);
    resolve(reportFixture()); await original; await close; c.finishClose(); c.activate(true); c.adoptDeferred();
    expect(c.state.report?.run_id).toBe(reportRun); expect(api.read).toHaveBeenCalledOnce();
  });
  it('renders v2 original receipt states, failed prefix and original execution revision without project/Git/drain claims', async () => {
    const { m, api } = fixture(); api.read.mockResolvedValue(reportV2Fixture()); await flushVue();
    await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('跨快照不原子'); expect(m.root.textContent).toContain('已退出 1');
    expect(m.root.textContent).toContain('剩余 1'); expect(m.root.textContent).toContain('原执行 revision 1');
    expect(m.root.textContent).toContain('当前计划 revision 2'); expect(m.root.textContent).toContain('不是项目验收通过');
    expect(api.download).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
  });
  it('renders canonical v3 primary separately, with full receipt denominators and no automatic read/export', async () => {
    const { m, api } = fixture(); api.read.mockResolvedValue(reportV3Fixture()); await flushVue();
    expect(api.read).not.toHaveBeenCalled();
    await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('主计量：所选原回执观测');
    expect(m.root.textContent).toContain('请求回执单位 1');
    expect(m.root.textContent).toContain('逻辑方法单位 0');
    expect(m.root.textContent).toContain('未匹配兼容观测（不加账）');
    expect(m.root.textContent).toContain('失败 0');
    expect(m.root.textContent).toContain('回调匹配 1');
    expect(m.root.textContent).toContain('wire 覆盖未知');
    expect(api.read).toHaveBeenCalledOnce(); expect(api.download).not.toHaveBeenCalled(); expect(api.save).not.toHaveBeenCalled();
  });
  it('labels historical v2 canonical primary as unknown rather than upgrading a callback count', async () => {
    const { m, api } = fixture(); api.read.mockResolvedValue(reportV2Fixture());
    await click(button(m.root, '明确读取白名单报告')); await flushVue();
    expect(m.root.textContent).toContain('旧版报告没有原 call/request 关联覆盖');
    expect(m.root.textContent).toContain('主计量未知');
    expect(api.read).toHaveBeenCalledOnce();
  });
});
