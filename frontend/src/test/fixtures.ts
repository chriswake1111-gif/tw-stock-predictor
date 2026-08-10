import type { AnalysisResponse, MarketOverviewResponse, PerformanceSummaryResponse, SnapshotDetailResponse } from "../api/types";

const emptySection = { status: "needs_human_input" as const, reason: "approval_required", rules_used: [], source_resource_versions: [] };

export const analysisFixture: AnalysisResponse = {
  status: "partial",
  symbol: "2330.TW",
  knowledge_cutoff_at: "2026-08-10T04:00:00Z",
  cutoff_policy: { input: "timestamp" },
  model: { name: "Evidence Model", version: "2.0.0", official_affiliation: false },
  data_quality: {
    status: "partial",
    section_statuses: { valuation: "available", liquidity: "quality_warning", technical_support: "available", target_confluence: "available", deployment_plan: "needs_human_input", screening: "partial" },
    available_sections: ["valuation", "technical_support", "target_confluence"],
    missing_sections: [], partial_sections: ["screening"], quality_warning_sections: ["liquidity"],
    unsupported_sections: [], not_applicable_sections: [], stale_sections: [], needs_human_input: ["approved_deployment_plan"],
  },
  valuation: {
    status: "available", reason: null,
    target_matrix: [{ cell_id: "cell-1", fiscal_year: 2027, source_name: "approved-research", eps_scenario: "base", pe_multiple: 20, target_price: 800 }],
    rules_used: [{ rule_id: "VAL-01", rule_version: "2.0.0", evidence_level: "A", implementation_mode: "verified_core", approval_id: "approval-val-1" }],
    source_resource_versions: [],
  },
  liquidity: { ...emptySection, status: "quality_warning", reason: "latest_turnover_partial" },
  technical_support: {
    status: "available", reason: null,
    scenarios: [{ anchor_set_revision_id: "anchor-1", method: "0.382 retracement support", target_price: 610, rule_trace: { rule_id: "FB-04" } }],
    rules_used: [{ rule_id: "FB-04", rule_version: "2.0.0", evidence_level: "A", implementation_mode: "verified_core", approval_id: "approval-fb04" }],
    source_resource_versions: [],
  },
  target_confluence: {
    status: "available", reason: null, independent_method_count: 2, summary_policy: "maximum_cluster_strength",
    overlap_ranges: [{ cluster_id: "cluster-1", price_low: "790", price_high: "805", evidence_strength: "moderate" }],
    rules_used: [{ rule_id: "TGT-01", rule_version: "2.0.0", evidence_level: "A", implementation_mode: "verified_core", approval_id: "approval-tgt" }], source_resource_versions: [],
  },
  deployment_plan: { ...emptySection, plans: [] },
  screening: { ...emptySection, status: "partial", components: {} },
  rules_used: [], unsupported: [], snapshot_id: null,
};

export const marketFixture: MarketOverviewResponse = {
  status: "quality_warning", knowledge_cutoff_at: "2026-08-10T04:00:00Z", cutoff_policy: { input: "timestamp" },
  market_overview: {
    status: "quality_warning", reason: "latest_turnover_partial", as_of_date: "2026-08-10", turnover_m1b_ratio_pct: null,
    rolling_mean_20d_pct: null, rolling_mean_60d_pct: null, historical_percentile_5y: null, historical_percentile_10y: null,
    alert_level: "reference_only", turnover_twd: { twse: 800000000000, tpex: null, total: null, unit: "TWD" },
    m1b_twd: { value: 30000000000000, period: "2026-06", available_at: "2026-07-20T08:00:00Z", unit: "TWD" },
    reference_case: { range_pct: [3.3, 3.4], label: "公開案例參考區", meaning: "僅供流動性背景解讀" }, rules_used: [], source_resource_versions: [],
  },
};

export const snapshotFixture: SnapshotDetailResponse = {
  status: "available",
  snapshot: {
    snapshot_id: "snapshot-1", symbol: "2330.TW", knowledge_cutoff_at: "2026-08-01T00:00:00Z", capture_mode: "historical_reconstruction",
    model_version: "2.0.0", created_at: "2026-08-10T02:00:00Z", supersedes_snapshot_id: null, analysis_status: "partial",
    used_rule_versions: {}, source_resource_versions: [], manual_approval_ids: [], output: analysisFixture, output_sha256: "abc123",
  },
};

export const performanceFixture: PerformanceSummaryResponse = {
  status: "available",
  performance_summary: {
    evaluation_run_id: "run-1", coverage: {}, disclosures: ["描述性歷史觀察，不是未來機率。"],
    groups: [{ evaluation_origin: "historical_reconstruction", horizon_sessions: 20, method_family: "valuation_forward_pe", evidence_strength: "moderate", subject_type: "scenario", n: 10, numerator: 7, denominator: 10, historical_target_reach_rate: "0.7", median_forward_return: "0.04", median_excess_return: "0.01", median_upside_excursion: "0.08", median_downside_excursion: "-0.03", status_counts: { evaluated: 10 } }],
  },
};
