export const sectionStatusValues = [
  "available",
  "partial",
  "needs_human_input",
  "insufficient_data",
  "quality_warning",
  "pending",
  "unsupported",
  "not_applicable",
] as const;

export type SectionStatus = (typeof sectionStatusValues)[number];
export type CaptureMode = "live_refresh" | "historical_reconstruction";
export type EvaluationOrigin =
  | "prospective_snapshot"
  | "historical_reconstruction";
export type EvidenceLevel = "A" | "B" | "C" | "U";
export type UnknownRecord = Record<string, unknown>;

export interface UniverseOperationalFreshness {
  freshness: "current" | "stale" | "unknown" | "blocked" | string;
  current_complete: boolean;
  freshness_mode?: string | null;
  availability_mode?: string | null;
  latest_visible_state?: string | null;
  reasons: string[];
}

export interface UniverseIdentityReference {
  instrument_id: string | null;
  canonical_symbol: string | null;
  official_code: string | null;
  venue: "TWSE" | "TPEX" | string | null;
  display_name?: string | null;
  security_type?: string | null;
  listing_status?: string | null;
  trading_state?: string | null;
  membership_state?: string | null;
  source_reference?: string | null;
  mapping_basis?: string | null;
}

export interface UniverseResponse {
  status: "available" | "partial" | "needs_human_input" | "insufficient_data";
  status_policy_version: "universe_status_matrix_v1";
  knowledge_cutoff_at: string;
  cutoff_policy: UnknownRecord;
  identity_reference: UniverseIdentityReference | null;
  operational_freshness: UniverseOperationalFreshness;
  effective?: UnknownRecord;
  provenance?: UnknownRecord;
  reasons: string[];
}

export interface UniverseListResponse extends UniverseResponse {
  items: UniverseResponse[];
  per_venue_status: Record<string, string>;
  next_cursor: string | null;
  order: string;
  limit: number;
}

export type EodCloseStatus =
  | "available"
  | "insufficient_data"
  | "partial"
  | "unknown"
  | "blocked"
  | "needs_human_input"
  | "not_applicable";
export type EodFreshnessState = "current" | "stale" | "unknown" | "blocked";
export type EodProductScope =
  | "supported_stock"
  | "not_applicable"
  | "needs_human_input";

export interface EodCloseContextResponse {
  contract_version: "eod_close_context_v1";
  status: EodCloseStatus;
  reason_codes: string[];
  canonical_symbol: string | null;
  instrument_id: string | null;
  official_code: string | null;
  venue: "TWSE" | "TPEX" | string | null;
  security_type: string | null;
  product_scope: EodProductScope;
  knowledge_cutoff_at: string | null;
  evaluated_at: string;
  selection_scope: string | null;
  selected_trade_date: string | null;
  close_value: string | null;
  currency: string | null;
  unit: string | null;
  price_semantics: string | null;
  freshness_state: EodFreshnessState;
  current_complete: boolean;
  provider: string | null;
  resource_key: string | null;
  source_url: string | null;
  source_trade_date: string | null;
  observed_at: string | null;
  source_scope: string | null;
  quality_flags: string[];
}

export interface RuleTrace extends UnknownRecord {
  rule_id: string;
  rule_version?: string;
  version?: string;
  evidence_level?: EvidenceLevel;
  implementation_mode?: string;
  project_operationalization?: boolean;
  approval_id?: string;
}

export interface SourceResourceVersion extends UnknownRecord {
  section?: string;
  resource_type: string;
  resource_id: string;
  logical_resource_id?: string | null;
  revision_number?: number | null;
  available_at?: string | null;
  ingested_at?: string | null;
  approval_ids?: string[];
}

export interface AnalysisSection extends UnknownRecord {
  status: SectionStatus;
  reason?: string | null;
  rules_used?: RuleTrace[];
  source_resource_versions?: SourceResourceVersion[];
}

export interface ForwardPeTargetCell extends UnknownRecord {
  status: SectionStatus;
  observation_id: string;
  pe_scenario_id: string;
  fiscal_year: number;
  source_name: string;
  eps_scenario: string;
  eps_value: number;
  pe_value: number;
  target_price: number | null;
}

export interface ValuationSection extends AnalysisSection {
  target_matrix: ForwardPeTargetCell[];
}

export interface TechnicalScenario extends UnknownRecord {
  anchor_set_revision_id: string;
  scenario_type: "equal_amplitude" | "retracement_0382";
  semantic_role: "target" | "support";
  calculated_level: number;
  price_unit: "TWD_per_share";
  rule_trace: RuleTrace;
}

export interface TechnicalSupportSection extends AnalysisSection {
  scenarios: TechnicalScenario[];
}

export interface TargetConfluenceCluster extends UnknownRecord {
  cluster_id: string;
  price_low: string;
  price_high: string;
  price_unit: "TWD_per_share";
  candidate_count: number;
  support_count: number;
  independent_method_count: number;
  evidence_strength: string | null;
  target_method_families: string[];
  candidate_ids: string[];
  shared_dependencies: string[];
}

export interface TargetConfluenceSection extends AnalysisSection {
  overlap_ranges: TargetConfluenceCluster[];
  summary_policy?: string;
}

export interface DeploymentEntry extends UnknownRecord {
  stage: number;
  weight: string;
  capital_budget: string;
  currency: "TWD";
  trigger: UnknownRecord | null;
  remaining_entries_after_stage: number;
}

export interface DeploymentPlan extends UnknownRecord {
  plan_revision_id: string;
  logical_campaign_id: string;
  entries: DeploymentEntry[];
}

export interface DeploymentPlanSection extends AnalysisSection {
  plans: DeploymentPlan[];
}

export interface DataQuality extends UnknownRecord {
  status: SectionStatus;
  section_statuses: Record<string, SectionStatus>;
  available_sections: string[];
  missing_sections: string[];
  partial_sections: string[];
  quality_warning_sections: string[];
  unsupported_sections: string[];
  not_applicable_sections: string[];
  stale_sections: string[];
  needs_human_input: string[];
}

export interface AnalysisResponse extends UnknownRecord {
  status: SectionStatus;
  symbol: string;
  knowledge_cutoff_at: string;
  cutoff_policy: UnknownRecord;
  model: {
    name: string;
    version: string;
    official_affiliation: false;
  };
  data_quality: DataQuality;
  valuation: ValuationSection;
  liquidity: AnalysisSection;
  technical_support: TechnicalSupportSection;
  target_confluence: TargetConfluenceSection;
  deployment_plan: DeploymentPlanSection;
  screening: AnalysisSection;
  rules_used: RuleTrace[];
  unsupported: string[];
  snapshot_id: string | null;
}

export interface SnapshotBase extends UnknownRecord {
  snapshot_id: string;
  symbol: string;
  knowledge_cutoff_at: string;
  capture_mode: CaptureMode;
  model_version: string;
  created_at: string;
  supersedes_snapshot_id: string | null;
}

export interface SnapshotSummary extends SnapshotBase {
  analysis_status: SectionStatus;
}

export interface SnapshotListResponse {
  status: "available";
  snapshots: SnapshotSummary[];
  next_before: string | null;
  filters: {
    symbol: string | null;
    capture_mode: CaptureMode | null;
  };
}

export interface SnapshotDetail extends SnapshotBase {
  used_rule_versions: Record<string, string>;
  source_resource_versions: SourceResourceVersion[];
  manual_approval_ids: string[];
  output: AnalysisResponse;
  output_sha256: string;
  synthesis_profile_revision_id?: string | null;
  synthesis_profile_approval_id?: string | null;
}

export interface SnapshotDetailResponse {
  status: "available";
  snapshot: SnapshotDetail;
}

export type SnapshotFreshnessStatus = "current" | "stale" | "unknown" | "blocked";

export interface SnapshotDependencyStatus extends UnknownRecord {
  snapshot_id: string;
  comparison_cutoff: string;
  checked_at: string;
  freshness_status: SnapshotFreshnessStatus;
  reasons: string[];
  checked_dependencies: UnknownRecord[];
  historical_snapshot_validity: "unchanged";
}

export interface SnapshotDependencyStatusResponse {
  status: "available";
  dependency_status: SnapshotDependencyStatus;
  cutoff_policy: UnknownRecord;
}

export type CanonicalComparisonValue =
  | string
  | number
  | boolean
  | null
  | { state: "missing" }
  | CanonicalComparisonValue[]
  | { [key: string]: CanonicalComparisonValue };

export interface SnapshotComparisonDelta {
  category: "stored_fact" | "current_context";
  change_type: string;
  section: string;
  resource_type: string | null;
  canonical_identity: string;
  field_path: string;
  before: CanonicalComparisonValue;
  after: CanonicalComparisonValue;
  absolute_delta?: string;
}

export interface SnapshotComparisonReference {
  snapshot_id: string;
  symbol: string;
  knowledge_cutoff_at: string;
  capture_mode: CaptureMode;
  model_version: string;
}

export interface SnapshotComparisonContext {
  snapshot_id: string;
  comparison_cutoff: string;
  checked_at: string;
  freshness_status: SnapshotFreshnessStatus;
  reasons: string[];
  checked_dependencies: UnknownRecord[];
  historical_snapshot_validity: "unchanged";
}

export interface SnapshotComparisonResponse {
  status: "available" | "incomparable_contract";
  comparison_policy_version: "1.0";
  comparison_snapshot_contract: "analysis_snapshot_v1";
  comparison_cutoff: string;
  direction: {
    base_snapshot_id: string;
    comparison_snapshot_id: string;
    absolute_delta_formula: "comparison_minus_base";
  };
  base_snapshot: SnapshotComparisonReference;
  comparison_snapshot: SnapshotComparisonReference;
  compatibility: { compatible: boolean; reasons: string[] };
  stored_deltas: SnapshotComparisonDelta[];
  base_current_context: SnapshotComparisonContext | null;
  comparison_current_context: SnapshotComparisonContext | null;
  current_context_deltas: SnapshotComparisonDelta[];
  warnings: string[];
  reasons: string[];
}

export interface EvaluationRunSummary extends UnknownRecord {
  evaluation_run_id: string;
  evaluation_profile_revision_id: string;
  evaluator_version: string;
  evaluation_origin_policy: string;
  outcome_resource_manifest_id: string;
  universe_definition: string;
  created_at: string;
  status: "completed";
}

export interface EvaluationRunListResponse {
  status: "available";
  evaluation_runs: EvaluationRunSummary[];
  next_before: string | null;
}

export interface ScenarioEvaluation extends UnknownRecord {
  evaluation_id: string;
  evaluation_run_id: string;
  snapshot_id: string;
  symbol: string;
  evaluation_origin: EvaluationOrigin;
  method_family: string;
  semantic_role: "target" | "support";
  evidence_strength: string | null;
  horizon_sessions: number;
  terminal_outcome: string;
  quality_status: string;
}

export interface EvaluationRunDetail extends EvaluationRunSummary {
  snapshot_memberships: UnknownRecord[];
  results: ScenarioEvaluation[];
}

export interface EvaluationRunDetailResponse {
  status: "available";
  evaluation_run: EvaluationRunDetail;
}

export interface EvaluationResultsResponse {
  status: "available";
  results: ScenarioEvaluation[];
}

export interface PerformanceGroup extends UnknownRecord {
  evaluation_origin: EvaluationOrigin;
  horizon_sessions: number;
  method_family: string;
  evidence_strength: string | null;
  subject_type: string;
  n: number;
  numerator: number;
  denominator: number;
  historical_target_reach_rate: string | null;
  median_forward_return: string | null;
  median_excess_return: string | null;
  median_upside_excursion: string | null;
  median_downside_excursion: string | null;
  status_counts: Record<string, number>;
}

export interface PerformanceSummary extends UnknownRecord {
  evaluation_run_id: string;
  coverage: UnknownRecord;
  groups: PerformanceGroup[];
  disclosures: string[];
}

export interface PerformanceSummaryResponse {
  status: "available";
  performance_summary: PerformanceSummary;
}

export interface MarketOverview extends AnalysisSection {
  as_of_date: string;
  trade_date?: string;
  turnover_m1b_ratio_pct: number | null;
  rolling_mean_20d_pct: number | null;
  rolling_mean_60d_pct: number | null;
  historical_percentile_5y: number | null;
  historical_percentile_10y: number | null;
  alert_level: string;
  turnover_twd: {
    twse: number | null;
    tpex: number | null;
    total: number | null;
    unit: "TWD";
  };
  m1b_twd: {
    value: number | null;
    period: string | null;
    available_at: string | null;
    unit: "TWD";
  };
  reference_case: {
    range_pct: [number, number];
    label: string;
    meaning: string;
  };
  latest_complete_observation?: UnknownRecord;
}

export interface MarketOverviewResponse {
  status: SectionStatus;
  knowledge_cutoff_at: string;
  cutoff_policy: UnknownRecord;
  market_overview: MarketOverview;
}

export interface EvidenceRule extends UnknownRecord {
  rule_id: string;
  title: string;
  version: string;
  evidence_level: EvidenceLevel;
  implementation_mode: string;
  project_operationalization: boolean;
  human_approval_required: boolean;
  allowed_outputs?: string[];
  forbidden_uses?: string[];
}

export interface RuleRegistryResponse {
  model_version: string;
  official_affiliation: false;
  rules: EvidenceRule[];
}

export type ResearchReviewState =
  | "no_snapshot" | "baseline_not_set" | "comparable_with_deltas"
  | "comparable_without_deltas" | "incomparable_contract" | "blocked"
  | "unknown" | "snapshot_integrity_error";

export type ResearchComparisonStatus =
  | "not_run"
  | "comparable"
  | "incomparable_contract"
  | "unavailable";

export interface ResearchWatchlistItem {
  watchlist_item_id: string;
  symbol: string;
  membership_state: "active" | "archived";
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  workflow_contract_version: "research_review_queue_v1";
}

export type ResearchMembershipMutationResponse = ResearchWatchlistItem & {
  changed: boolean;
};

export interface ResearchQueueItem {
  watchlist_item: ResearchWatchlistItem;
  analysis_status: string;
  freshness_status: SnapshotFreshnessStatus;
  comparison_status: ResearchComparisonStatus;
  review_state: ResearchReviewState;
  comparison_has_deltas: boolean | null;
  stored_delta_count: number;
  current_context_delta_count: number;
  latest_snapshot_reference: { snapshot_id: string; symbol: string } | null;
  latest_review_event_reference: {
    review_event_id: string;
    acknowledged_snapshot_id: string;
    comparison_cutoff_at: string;
    reviewed_at: string;
  } | null;
  reason_codes: string[];
}

export interface ResearchQueueResponse {
  status: "available";
  workflow_contract_version: "research_review_queue_v1";
  comparison_cutoff: string;
  items: ResearchQueueItem[];
}

export interface ResearchQueueDetail extends ResearchQueueItem {
  comparison: SnapshotComparisonResponse | null;
}

export type DailyResearchStatus =
  | "available" | "partial" | "insufficient_data" | "unknown" | "blocked";

export interface DailyResearchSnapshotReference {
  snapshot_id: string;
  symbol: string;
  venue: string;
  created_at: string | null;
  knowledge_cutoff_at: string | null;
  capture_mode: string | null;
  model_version: string | null;
  integrity_status: "valid" | "invalid";
  provenance_status: "available" | "unavailable";
  eligible_for_requested_d_k: boolean;
}

export interface DailyResearchItem {
  watchlist_reference: ResearchWatchlistItem;
  canonical_symbol: string;
  venue: string;
  identity: Record<string, unknown>;
  status: DailyResearchStatus;
  review_state: ResearchReviewState;
  workflow_review_state: string;
  workflow_evaluated_at: string;
  review_needed: boolean;
  review_blocked: boolean;
  review_limited: boolean;
  reason_codes: string[];
  latest_snapshot_reference: DailyResearchSnapshotReference | null;
  workflow_latest_snapshot_reference: DailyResearchSnapshotReference | null;
  acknowledged_baseline_reference: DailyResearchSnapshotReference | null;
  k_visible_acknowledgment_reference: Record<string, unknown> | null;
  workflow_acknowledgment_reference: Record<string, unknown> | null;
  comparison_status: ResearchComparisonStatus;
  comparison_has_deltas: boolean | null;
  stored_delta_summary: { count: number; change_types: Record<string, number>; sections: Record<string, number> };
  current_context_delta_summary: { count: number; change_types: Record<string, number>; sections: Record<string, number> };
  phase16_context: Record<string, unknown>;
  freshness_status: string;
  quality: Record<string, unknown>;
  provenance: Record<string, unknown>;
  permitted_actions: {
    open_review: boolean;
    acknowledge: boolean;
    refresh_snapshot: boolean;
    archive: boolean;
    restore: boolean;
  };
  baseline_selection_policy_version: string;
  baseline_selection_reason_registry_version: string;
  baseline_selection_eligible: boolean;
  baseline_selection_blocked: boolean;
  baseline_selection_reason_codes: string[];
  comparison?: SnapshotComparisonResponse | null;
}

export interface DailyResearchResponse {
  contract_version: "daily_research_review_context_v1";
  policy_version: string;
  workflow_time_policy_version: string;
  snapshot_selection_policy_version: string;
  reason_registry_version: string;
  d_k_policy_version: string;
  order_version: string;
  cursor_version: string;
  snapshot_integration_version: string;
  status_scope: "page_items";
  request: {
    market_date: string;
    knowledge_cutoff_at: string;
    request_received_at: string;
    workflow_evaluated_at: string;
    population: "active_research_queue";
    population_evaluated_at: string;
    internal_venue_scope: "TWSE_TPEX";
    d_k_policy_version: string;
    workflow_time_policy_version: string;
    snapshot_selection_policy_version: string;
    order_version: string;
  };
  status: DailyResearchStatus;
  preflight: {
    status: DailyResearchStatus;
    status_scope: "full_daily_preflight";
    market_context_status: DailyResearchStatus;
    active_queue_total_count: number;
    active_population_checksum: string;
    page_item_count: number;
    page_has_more: boolean;
    page_review_needed_count: number;
    page_review_blocked_count: number;
    page_review_limited_count: number;
    page_status_counts: Record<DailyResearchStatus, number>;
    venue_statuses: Record<string, string>;
    reasons: string[];
    aggregate_completeness_proven: false;
  };
  aggregate: Record<string, unknown>;
  items: DailyResearchItem[];
  limit: number;
  next_cursor: string | null;
  item?: DailyResearchItem;
}

export interface DailyBaselineSelectionResponse {
  status: "available";
  baseline_selection_policy_version: string;
  baseline_selection_reason_registry_version: string;
  baseline_selection_event: {
    review_event_id: string;
    watchlist_item_id: string;
    baseline_snapshot_id: string;
    knowledge_cutoff_at: string;
    selected_at: string;
    created: boolean;
    workflow_contract_version: string;
  };
  workflow_evaluated_at: string;
  correlation_id: string;
}

export interface DailySnapshotRefreshResponse {
  status: "available";
  created: boolean;
  refresh_received_at: string;
  new_knowledge_cutoff_at: string;
  snapshot: DailyResearchSnapshotReference;
  refresh_gate: Record<string, unknown>;
}

export interface DataOperationsActiveOperation {
  operation_id: string;
  operation_type: string;
  status: string;
  current_stage: string;
  lease_expires_at: string;
  created_at: string;
}

export interface DataOperationsStatusResponse {
  readiness: "not_initialized" | "partial" | "ready" | "stale";
  is_syncing: boolean;
  active_operation: DataOperationsActiveOperation | null;
  market_context_summary: {
    calendar_status: string;
    latest_eod_date: string | null;
    m1b_latest_period: string | null;
  };
}

export interface SyncTriggerResponse {
  operation_id: string;
  status: string;
  current_stage: string;
}

export interface EnableSymbolResponse {
  operation_id: string;
  status: string;
  current_stage: string;
}
