import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { vi } from "vitest";
import type { AnalysisResponse } from "../api/types";

export function renderWithProviders(ui: ReactElement, route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter></QueryClientProvider>);
}

export function mockReadApi(analysisOverride?: AnalysisResponse, dependencyOverride?: unknown, comparisonOverride?: unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const { analysisFixture, marketFixture, performanceFixture, snapshotFixture, snapshotComparisonFixture } = await import("./fixtures");
    let body: unknown;
    if (url.includes("/analysis/snapshots/compare")) body = comparisonOverride ?? snapshotComparisonFixture;
    else if (url.includes("/dependency-status")) body = dependencyOverride ?? {
      status: "available",
      dependency_status: {
        snapshot_id: "snapshot-1",
        comparison_cutoff: "2026-08-11T02:00:00Z",
        checked_at: "2026-08-11T02:00:00Z",
        freshness_status: "stale",
        reasons: ["newer_eligible_forward_eps_revision", "newer_eligible_pe_scenario_revision"],
        checked_dependencies: [],
        historical_snapshot_validity: "unchanged",
      },
      cutoff_policy: { mode: "request_received_at", timezone: "UTC" },
    };
    else if (url.includes("/market-overview")) body = marketFixture;
    else if (url.includes("/analysis/snapshots/snapshot-1")) body = snapshotFixture;
    else if (url.includes("/analysis/snapshots")) body = {
      status: "available",
      snapshots: [
        { ...snapshotFixture.snapshot, analysis_status: snapshotFixture.snapshot.output.status },
        { ...snapshotFixture.snapshot, snapshot_id: "snapshot-2", created_at: "2026-08-11T02:00:00Z", analysis_status: snapshotFixture.snapshot.output.status },
      ],
      next_before: null,
      filters: { symbol: null, capture_mode: null },
    };
    else if (url.includes("/performance/summary")) body = performanceFixture;
    else if (url.includes("/evaluations/runs/run-1")) body = { status: "available", evaluation_run: { evaluation_run_id: "run-1", evaluation_profile_revision_id: "profile-1", evaluator_version: "phase8_mvp_v1", evaluation_origin_policy: "separate_by_evaluation_origin", outcome_resource_manifest_id: "manifest-1", universe_definition: "fixed cohort", created_at: "2026-08-10T02:00:00Z", status: "completed", snapshot_memberships: [], results: [] } };
    else if (url.includes("/evaluations/runs")) body = { status: "available", evaluation_runs: [], next_before: null };
    else if (url.includes("/model-rules")) body = { model_version: "2.0.0", official_affiliation: false, rules: [] };
    else if (url.includes("/csrf-token")) body = { csrf_token: "mock-csrf" };
    else if (url.includes("/universe/coverage")) body = { universe_status: "ready", total_instruments: 2, phase20_materialized_count: 2, coverage_ratio: 1.0, degraded_search_mode: false };
    else if (url.includes("/universe/search")) body = { query: "", total_matches: 0, results: [], coverage: { universe_status: "ready", total_instruments: 2, phase20_materialized_count: 2, coverage_ratio: 1.0, degraded_search_mode: false } };
    else if (url.includes("/research/bootstrap")) body = { status: "ready", operation_id: null, canonical_symbol: "2330.TW" };
    else if (url.includes("/research/summary")) body = {
      canonical_symbol: "2330.TW",
      official_code: "2330",
      venue: "TWSE",
      company_name: "台灣積體電路製造股份有限公司",
      short_name: "台積電",
      market_context: {
        settled_trade_date: "2026-09-04",
        official_close: 980.0,
        close_status: "available",
        close_reason: null,
        currency: "TWD",
        unit: "TWD_per_share",
        is_market_closed: false,
        market_status_label: "最新已結算行情：2026-09-04",
        market_turnover_total: 300000000000.0,
        market_turnover_status: "available",
        cbc_m1b_ratio: null,
        cbc_status: "insufficient_data",
      },
      valuation_context: {
        status: "needs_human_judgment",
        reason_code: "forward_eps_missing_at_knowledge_cutoff",
        target_matrix: [],
      },
      technical_context: {
        status: "needs_human_judgment",
        reason_code: "manual_anchor_required",
        targets: null,
      },
      screening_context: {
        pe: { status: "unavailable", value: null, label: "本益比 (PE)", ui_copy: "尚無可用資料" },
        pb: { status: "unavailable", value: null, label: "股價淨值比 (PB)", ui_copy: "尚無可用資料" },
        dividend_yield: { status: "unavailable", value: null, label: "殖利率", ui_copy: "尚無可用資料" },
      },
      human_decision_queue: [
        {
          item_id: "val_02_forward_eps",
          title: "核准預估 EPS（Forward EPS）",
          rule_id: "VAL-02",
          evidence_level: "A",
          description: "依杜金龍估值模型規範，預估 EPS 屬核心假設，系統嚴禁自動合成假值。",
          suggested_action: "請至估值決策面板輸入經核准的 Forward EPS",
          status: "pending",
        }
      ],
      audit_reference: {
        source_snapshot_id: "snap_2026-09-04",
        available_at: "2026-09-04T13:35:00Z",
        ingested_at: "2026-09-04T13:36:00Z",
        model_version: "2.0.0",
        rule_traces: ["VAL-01", "VAL-02", "FB-03", "FB-04", "ENT-02", "SEL-01"],
      },
      knowledge_cutoff_at: "2026-09-04T16:00:00Z",
    };
    else body = analysisOverride ?? analysisFixture;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}
