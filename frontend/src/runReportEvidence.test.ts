// B2a definitions FIRST, ALL UNRUN. Closed projection, not backend/native proof.
import { describe, expect, it } from 'vitest';
import { parseRunReport } from './runReportApi';
import { reportRun } from './testSupport/runReportFixture';
import { reportV2Fixture, reportEvidenceFixture } from './testSupport/reportEvidenceFixture';
const bad: Array<(v: ReturnType<typeof reportEvidenceFixture>) => unknown> = [
  v => Object.assign(v, { private_argv: 'UNKNOWN_PRIVATE_CREDENTIAL' }),
  v => Object.assign(v.patches.items[0]!, { tool_call_id: 'UNKNOWN_PRIVATE_CREDENTIAL' }),
  v => Object.assign(v.patches.items[0]!, { status: 'running', confirmed_applied: true }),
  v => Object.assign(v.patches, { total: null }),
  v => Object.assign(v.inverses.items[0]!, { effect_receipt_state: 'secret' }),
  v => Object.assign(v.inverses.items[0]!, { source_receipt_confirmed: false }),
  v => Object.assign(v.work_orders.items[0]!, { execution_revision: 3 }),
  v => Object.assign(v.verifications.items[0]!, { project_acceptance: true }),
  v => Object.assign(v.verifications.items[0]!, { remaining_commands: 0 }),
  v => Object.assign(v.verifications.items[0]!, { all_commands_exited: true }),
  v => Object.assign(v.verifications.items[0]!.steps[0]!, { exit_code: 0, success: false }),
  v => Object.assign(v.verifications.items[0]!.steps[0]!, { failure: 'PRIVATE_ERROR' }),
  v => Object.assign(v, { snapshot_consistency: 'atomic' }),
  v => Object.assign(v, { approval_granted: true }),
];
describe('v2 receipt projection closed schema', () => {
  it('keeps original execution revision, failed prefix and source receipt, without proving Git ownership or acceptance', () => {
    const wire = reportV2Fixture(), report = parseRunReport(wire, reportRun);
    expect(report.version).toBe(2); expect(report.evidence?.verifications.items[0]?.remaining_commands).toBe(1);
    expect(report.evidence?.work_orders.items[0]?.execution_revision).toBe(1);
    expect(report.evidence?.work_orders.items[0]?.active_plan_revision).toBe(2);
    expect(report.evidence?.current_git_ownership_proven).toBe(false); expect(Object.isFrozen(report.evidence?.patches.items[0])).toBe(true);
    wire.evidence.patches.items[0]!.files = 32; expect(report.evidence?.patches.items[0]?.files).toBe(1);
  });
  it.each(bad)('refuses arbitrary raw IDs/text and inconsistent proof claims', mutate => {
    const wire = reportV2Fixture(); mutate(wire.evidence); expect(() => parseRunReport(wire, reportRun)).toThrow('invalid_run_report');
  });
  it('distinguishes absent tables, partial receipt dependencies and truncation from observed empty scopes', () => {
    const wire = reportV2Fixture();
    Object.assign(wire.evidence.patches, { state: 'unknown', total: null, scanned: 0, emitted: 0, omitted: 0, truncated: null, items: [], reason: 'missing_tables' });
    Object.assign(wire.evidence.inverses, { state: 'partial', reason: 'incomplete_evidence' });
    wire.evidence.inverses.items[0]!.effect_receipt_state = 'unavailable';
    Object.assign(wire.evidence.work_orders, { state: 'partial', total: 17, scanned: 16, omitted: 15, emitted: 1, truncated: true, reason: 'incomplete_evidence' });
    const report = parseRunReport(wire, reportRun);
    expect(report.evidence?.patches.total).toBeNull(); expect(report.evidence?.inverses.state).toBe('partial');
    expect(report.evidence?.work_orders.truncated).toBe(true);
  });
  it('does not allow evidence extras under v1 or silently omit the v2 envelope', () => {
    const wire = reportV2Fixture(); expect(() => parseRunReport({ ...wire, version: 1 }, reportRun)).toThrow('invalid_run_report');
    const { evidence: _unused, ...missing } = wire; expect(() => parseRunReport(missing, reportRun)).toThrow('invalid_run_report');
  });
});
