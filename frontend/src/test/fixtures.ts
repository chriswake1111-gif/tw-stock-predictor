import phase9Contracts from "../../../tests/contracts/phase9_frontend_contracts.json" with { type: "json" };
import type {
  AnalysisResponse,
  DeploymentEntry,
  ForwardPeTargetCell,
  MarketOverviewResponse,
  PerformanceSummaryResponse,
  RuleTrace,
  SnapshotDetailResponse,
  SnapshotComparisonResponse,
  TargetConfluenceCluster,
  TechnicalScenario,
} from "../api/types";

const contracts = phase9Contracts as unknown as {
  valuation_target_matrix: ForwardPeTargetCell[];
  technical_scenarios: TechnicalScenario[];
  deployment_entries: DeploymentEntry[];
  target_confluence_clusters: TargetConfluenceCluster[];
  target_confluence_rule: RuleTrace;
};

const emptySection = { status: "needs_human_input" as const, reason: "approval_required", rules_used: [], source_resource_versions: [] };

export const analysisFixture: AnalysisResponse = {
  status: "partial",
  symbol: "2330.TW",
  knowledge_cutoff_at: "2026-08-10T04:00:00Z",
  cutoff_policy: { input: "timestamp" },
  model: { name: "Evidence Model", version: "2.0.0", official_affiliation: false },
  data_quality: {
    status: "partial",
    section_statuses: { valuation: "available", liquidity: "quality_warning", technical_support: "available", target_confluence: "available", deployment_plan: "available", screening: "partial" },
    available_sections: ["valuation", "technical_support", "target_confluence", "deployment_plan"],
    missing_sections: [], partial_sections: ["screening"], quality_warning_sections: ["liquidity"],
    unsupported_sections: [], not_applicable_sections: [], stale_sections: [], needs_human_input: [],
  },
  valuation: {
    status: "available", reason: null,
    target_matrix: contracts.valuation_target_matrix,
    rules_used: [{ rule_id: "VAL-01", rule_version: "2.0.0", evidence_level: "A", implementation_mode: "verified_core", approval_id: "approval-val-1" }],
    source_resource_versions: [],
  },
  liquidity: { ...emptySection, status: "quality_warning", reason: "latest_turnover_partial" },
  technical_support: {
    status: "available", reason: null,
    scenarios: contracts.technical_scenarios,
    rules_used: [{ rule_id: "FB-04", rule_version: "2.0.0", evidence_level: "A", implementation_mode: "verified_core", approval_id: "approval-fb04" }],
    source_resource_versions: [],
  },
  target_confluence: {
    status: "available", reason: null, independent_method_count: 2, evidence_strength: "moderate", summary_policy: "maximum_cluster_strength",
    overlap_ranges: contracts.target_confluence_clusters,
    rules_used: [contracts.target_confluence_rule], source_resource_versions: [],
  },
  deployment_plan: {
    status: "available",
    reason: null,
    plans: [{
      plan_revision_id: "deployment_971c6bcba8287928411375ba",
      logical_campaign_id: "phase9-contract-plan",
      entries: contracts.deployment_entries,
    }],
    rules_used: [{ rule_id: "ENT-02", rule_version: "2.0.0", evidence_level: "A", implementation_mode: "verified_core", approval_id: "approval-ent02" }],
    source_resource_versions: [],
  },
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
    model_version: "2.0.0", created_at: "2026-08-10T02:00:00Z", supersedes_snapshot_id: null,
    used_rule_versions: {}, source_resource_versions: [], manual_approval_ids: [], output: analysisFixture, output_sha256: "abc123",
  },
};

export const snapshotComparisonFixture: SnapshotComparisonResponse = {
  status: "available",
  comparison_policy_version: "1.0",
  comparison_snapshot_contract: "analysis_snapshot_v1",
  comparison_cutoff: "2026-08-12T04:00:00Z",
  direction: {
    base_snapshot_id: "snapshot-1",
    comparison_snapshot_id: "snapshot-2",
    absolute_delta_formula: "comparison_minus_base",
  },
  base_snapshot: {
    snapshot_id: "snapshot-1", symbol: "2330.TW",
    knowledge_cutoff_at: "2026-08-01T00:00:00Z",
    capture_mode: "historical_reconstruction", model_version: "2.0.0",
    output_sha256: "abc123",
  },
  comparison_snapshot: {
    snapshot_id: "snapshot-2", symbol: "2330.TW",
    knowledge_cutoff_at: "2026-08-02T00:00:00Z",
    capture_mode: "historical_reconstruction", model_version: "2.0.0",
    output_sha256: "def456",
  },
  compatibility: { compatible: true, reasons: [] },
  stored_deltas: [{
    category: "stored_fact", change_type: "resource_revision_changed",
    section: "valuation", resource_type: "forward_eps_revision",
    canonical_identity: "valuation|forward_eps_revision|eps-series",
    field_path: "source_resource_versions.revision",
    before: { resource_id: "eps-r1", revision_number: 1 },
    after: { resource_id: "eps-r2", revision_number: 2 },
  }],
  base_current_context: {
    snapshot_id: "snapshot-1", comparison_cutoff: "2026-08-12T04:00:00Z",
    checked_at: "2026-08-12T04:00:00Z", freshness_status: "blocked",
    reasons: ["approval_revoked"], checked_dependencies: [],
    historical_snapshot_validity: "unchanged",
  },
  comparison_current_context: {
    snapshot_id: "snapshot-2", comparison_cutoff: "2026-08-12T04:00:00Z",
    checked_at: "2026-08-12T04:00:00Z", freshness_status: "current",
    reasons: [], checked_dependencies: [], historical_snapshot_validity: "unchanged",
  },
  current_context_deltas: [{
    category: "current_context", change_type: "approval_revoked",
    section: "valuation", resource_type: "forward_eps_revision",
    canonical_identity: "valuation|forward_eps_revision|eps-series",
    field_path: "checked_dependencies.effective_approval_status",
    before: "approved", after: "revoked",
  }],
  warnings: ["current_dependency_context_requires_review"],
  reasons: ["approval_revoked"],
};

export const performanceFixture: PerformanceSummaryResponse = {
  status: "available",
  performance_summary: {
    evaluation_run_id: "run-1", coverage: {}, disclosures: ["描述性歷史觀察，不是未來機率。"],
    groups: [{ evaluation_origin: "historical_reconstruction", horizon_sessions: 20, method_family: "valuation_forward_pe", evidence_strength: "moderate", subject_type: "scenario", n: 10, numerator: 7, denominator: 10, historical_target_reach_rate: "0.7", median_forward_return: "0.04", median_excess_return: "0.01", median_upside_excursion: "0.08", median_downside_excursion: "-0.03", status_counts: { evaluated: 10 } }],
  },
};
