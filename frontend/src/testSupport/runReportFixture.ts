// Wire fixture only. Not actual provider, SQL, browser download or native proof.
export const reportRun = "a".repeat(32), otherReportRun = "c".repeat(32);
export function reportFixture(run_id = reportRun) {
  return {
    version: 1, run_id,
    source: { status: "completed", mode: "graph", lease_active: false },
    snapshot: { event_total: 3, event_high_water: 3, scanned_events: 3, limit: 5000, truncated: false,
      omitted_usage_rows: 0, consistency: "same_runtime_database_read_transaction" },
    redaction: { policy: "allowlisted_identifiers_enums_numeric_counters_only", free_text_exported: false,
      anonymous: false, original_evidence_modified: false },
    usage: { state: "known_for_recorded_model_events",
      observed: { known_model_events: 1, input_tokens: 10, output_tokens: 2, subtotal_overflow: false },
      root: { known_model_events: 1, input_tokens: 10, output_tokens: 2, subtotal_overflow: false }, children: [],
      unknown_model_events: 0, unclassified_usage_envelopes: 0, billing_complete: false,
      old_child_attribution: "missing_tags_not_reconstructed", estimated_tokens_as_actual: false },
    cost: { state: "unknown", amount: null, currency: null, price_snapshot: null,
      reason: "no_frozen_billing_price_receipt", account_cap_guaranteed: false },
    recorded_effect_counts: { "graph.tool_finished": 1, "deep.tool_finished": 0, "mcp.tool_executed": 0, "patch.applied": 0 },
    physical_drain_verified: false, task_quality_scored: false,
    query: { sql_read_only: true, filesystem_zero_write_guarantee: false, decoded_bytes: 120 },
    service: { read_only: true, owner_held: true, failed_close_diagnostic: false, execution_admission: "not_checked_by_read" },
  };
}
