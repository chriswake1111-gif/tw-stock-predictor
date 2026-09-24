import type { ResearchSummaryResponse } from "../api/types";

// Synthetic UI fixture only; never imported by product code or saved as research.
export function guidedResearchFixture(): ResearchSummaryResponse {
  return {
    canonical_symbol: "2330.TW", official_code: "2330", short_name: "台積電", company_name: "台灣積體電路製造股份有限公司", venue: "TWSE",
    knowledge_cutoff_at: "2026-09-14T16:00:00Z",
    market_context: {
      settled_trade_date: "2026-09-11", official_close: 2410, close_status: "available", close_reason: null,
      currency: "TWD", unit: "TWD_per_share", is_market_closed: false, market_status_label: "本機行情",
      market_turnover_total: null, market_turnover_status: "insufficient_data", cbc_m1b_ratio: null, cbc_status: "insufficient_data",
    },
    public_data: {
      TaiwanStockPrice: { source: "FinMind", official_exchange_source: false, status: "quality_warning", last_update_status: "failed", last_update_reason: "來源暫時無法連線", observed_at: "2026-09-14T10:00:00Z", rows: [
        { date: "2026-09-14", close: 2380, change: -30, volume: 12000 }, { date: "2026-09-11", close: 2410, volume: 10000 },
      ] },
      TaiwanStockPER: { source: "FinMind", status: "available", rows: [{ date: "2026-09-14", pe: 27.59, pb: 9.59, yield_ratio: 0.0092 }] },
      TaiwanStockFinancialStatements: { source: "FinMind", status: "insufficient_data", rows: [] },
    },
    valuation_context: { status: "available", reason_code: null, target_matrix: [
      { status: "available", fiscal_year: 2026, observation_id: "eps-new", eps_scenario: "base", eps_value: 107.74, source_name: "測試來源甲", observation_revision_number: 1, pe_revision_number: 2, pe_value: 20, pe_label: "自訂敏感度 20 倍", target_price: 2154.8, approval_ids: { "VAL-02": "eps-approved", "VAL-04": "pe-approved" } },
      { status: "available", fiscal_year: 2025, observation_id: "eps-old", eps_scenario: "base", eps_value: 45, source_name: "測試來源乙", observation_revision_number: 1, pe_revision_number: 2, pe_value: 20, pe_label: "舊年度敏感度", target_price: 900 },
    ] },
    technical_context: { status: "insufficient_data", reason_code: "anchors_missing", targets: null },
    screening_context: {
      pe: { status: "insufficient_data", value: null, label: "PE", ui_copy: "缺值" },
      pb: { status: "insufficient_data", value: null, label: "PB", ui_copy: "缺值" },
      dividend_yield: { status: "insufficient_data", value: null, label: "殖利率", ui_copy: "缺值" },
    },
    human_decision_queue: [], audit_reference: { source_snapshot_id: null, available_at: null, ingested_at: null, model_version: "2.0.test", rule_traces: [] },
  };
}
