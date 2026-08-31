import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DailyResearchPage } from "../pages/DailyResearchPage";
import type { DailyResearchResponse } from "../api/types";
import { renderWithProviders } from "./render";

function dailyResponse(): DailyResearchResponse {
  const snapshot = {
    snapshot_id: "snapshot-1",
    symbol: "2330.TW",
    venue: "TWSE",
    created_at: "2026-08-30T01:00:00Z",
    knowledge_cutoff_at: "2026-08-30T00:00:00Z",
    capture_mode: "historical_reconstruction",
    model_version: "2.0.0",
    integrity_status: "valid" as const,
    provenance_status: "available" as const,
    eligible_for_requested_d_k: true,
  };
  return {
    contract_version: "daily_research_review_context_v1",
    policy_version: "daily_research_review_context_policy_v1",
    workflow_time_policy_version: "daily_research_workflow_time_v1",
    snapshot_selection_policy_version: "daily_research_snapshot_selection_v1",
    reason_registry_version: "daily_research_review_reason_registry_v1",
    d_k_policy_version: "daily_research_review_d_k_v1",
    order_version: "daily_research_review_order_v1",
    cursor_version: "daily_research_review_cursor_v1",
    snapshot_integration_version: "daily_research_snapshot_integration_v1",
    status_scope: "page_items",
    request: {
      market_date: "2026-08-31",
      knowledge_cutoff_at: "2026-08-31T08:00:00+08:00",
      request_received_at: "2026-08-31T09:00:00Z",
      workflow_evaluated_at: "2026-08-31T09:00:00Z",
      population: "active_research_queue",
      population_evaluated_at: "2026-08-31T09:00:00Z",
      internal_venue_scope: "TWSE_TPEX",
      d_k_policy_version: "daily_research_review_d_k_v1",
      workflow_time_policy_version: "daily_research_workflow_time_v1",
      snapshot_selection_policy_version: "daily_research_snapshot_selection_v1",
      order_version: "daily_research_review_order_v1",
    },
    status: "available",
    preflight: {
      status: "available",
      status_scope: "full_daily_preflight",
      market_context_status: "available",
      active_queue_total_count: 1,
      active_population_checksum: "checksum-1",
      page_item_count: 1,
      page_has_more: false,
      page_review_needed_count: 1,
      page_review_blocked_count: 0,
      page_review_limited_count: 0,
      page_status_counts: { available: 1, partial: 0, insufficient_data: 0, unknown: 0, blocked: 0 },
      venue_statuses: { TWSE: "available", TPEX: "available" },
      reasons: [],
      aggregate_completeness_proven: false,
    },
    aggregate: {},
    items: [{
      watchlist_reference: {
        watchlist_item_id: "item-1",
        symbol: "2330.TW",
        membership_state: "active",
        created_at: "2026-08-29T00:00:00Z",
        updated_at: "2026-08-29T00:00:00Z",
        archived_at: null,
        workflow_contract_version: "research_review_queue_v1",
      },
      canonical_symbol: "2330.TW",
      venue: "TWSE",
      identity: { identity_status: "resolved" },
      status: "available",
      review_state: "baseline_not_set",
      workflow_review_state: "baseline_not_set",
      workflow_evaluated_at: "2026-08-31T09:00:00Z",
      review_needed: true,
      review_blocked: false,
      review_limited: false,
      reason_codes: ["baseline_not_set"],
      latest_snapshot_reference: snapshot,
      workflow_latest_snapshot_reference: snapshot,
      acknowledged_baseline_reference: null,
      k_visible_acknowledgment_reference: null,
      workflow_acknowledgment_reference: null,
      comparison_status: "not_run",
      comparison_has_deltas: null,
      stored_delta_summary: { count: 0, change_types: {}, sections: {} },
      current_context_delta_summary: { count: 0, change_types: {}, sections: {} },
      phase16_context: { aggregate_status: "available" },
      freshness_status: "current",
      quality: { status: "available" },
      provenance: { status: "available" },
      permitted_actions: {
        open_review: true,
        acknowledge: true,
        refresh_snapshot: true,
        archive: true,
        restore: false,
      },
      baseline_selection_policy_version: "daily_research_baseline_selection_policy_v1",
      baseline_selection_reason_registry_version: "daily_research_baseline_selection_reason_registry_v1",
      baseline_selection_eligible: true,
      baseline_selection_blocked: false,
      baseline_selection_reason_codes: [],
    }],
    limit: 25,
    next_cursor: null,
  };
}

function submitContext() {
  fireEvent.change(screen.getByLabelText("市場日期 D（YYYY-MM-DD）"), {
    target: { value: "2026-08-31" },
  });
  fireEvent.change(screen.getByLabelText("知識截止 K（含時區）"), {
    target: { value: "2026-08-31T08:00:00+08:00" },
  });
  fireEvent.click(screen.getByRole("button", { name: "載入每日脈絡" }));
}

describe("Daily Research Context", () => {
  it("requires explicit D/K before issuing the scoped read", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify(dailyResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    renderWithProviders(<DailyResearchPage />, "/research");
    expect(calls).toHaveLength(0);
    submitContext();
    expect(await screen.findByRole("heading", { name: "2330.TW" })).toBeInTheDocument();
    expect(calls.some((url) => url.includes("/api/v2/research/daily-context"))).toBe(true);
    expect(calls.some((url) => url.includes("market_date=2026-08-31"))).toBe(true);
    expect(calls.some((url) => url.includes("knowledge_cutoff_at=2026-08-31T08%3A00%3A00%2B08%3A00"))).toBe(true);
  });

  it("uses the Daily baseline endpoint and never the legacy acknowledgment route", async () => {
    const calls: Array<{ url: string; method: string; body: string }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: String(init?.body ?? "") });
      if (url.endsWith("/csrf-token")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-token" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (method === "POST" && url.includes("/baseline-selections")) {
        return new Response(JSON.stringify({
          status: "available",
          correlation_id: "server-correlation-id",
          baseline_selection_event: { review_event_id: "review-1", created: true },
        }), { status: 201, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify(dailyResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    renderWithProviders(<DailyResearchPage />, "/research");
    submitContext();
    expect(await screen.findByRole("button", { name: "設為每日複核基準" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "設為每日複核基準" }));

    await waitFor(() => expect(calls.some((call) => (
      call.method === "POST" && call.url.includes("/baseline-selections")
    ))).toBe(true));
    expect(calls.some((call) => call.method === "POST" && call.url.includes("/acknowledgments"))).toBe(false);
    const baselineCall = calls.find((call) => call.method === "POST" && call.url.includes("/baseline-selections"));
    expect(baselineCall?.body).toContain("baseline_snapshot_id");
    expect(baselineCall?.body).toContain("knowledge_cutoff_at");
  });
});
