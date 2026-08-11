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

export function mockReadApi(analysisOverride?: AnalysisResponse) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const { analysisFixture, marketFixture, performanceFixture, snapshotFixture } = await import("./fixtures");
    let body: unknown;
    if (url.includes("/market-overview")) body = marketFixture;
    else if (url.includes("/analysis/snapshots/snapshot-1")) body = snapshotFixture;
    else if (url.includes("/analysis/snapshots")) body = {
      status: "available",
      snapshots: [{ ...snapshotFixture.snapshot, analysis_status: snapshotFixture.snapshot.output.status }],
      next_before: null,
      filters: { symbol: null, capture_mode: null },
    };
    else if (url.includes("/performance/summary")) body = performanceFixture;
    else if (url.includes("/evaluations/runs/run-1")) body = { status: "available", evaluation_run: { evaluation_run_id: "run-1", evaluation_profile_revision_id: "profile-1", evaluator_version: "phase8_mvp_v1", evaluation_origin_policy: "separate_by_evaluation_origin", outcome_resource_manifest_id: "manifest-1", universe_definition: "fixed cohort", created_at: "2026-08-10T02:00:00Z", status: "completed", snapshot_memberships: [], results: [] } };
    else if (url.includes("/evaluations/runs")) body = { status: "available", evaluation_runs: [], next_before: null };
    else if (url.includes("/model-rules")) body = { model_version: "2.0.0", official_affiliation: false, rules: [] };
    else body = analysisOverride ?? analysisFixture;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}
