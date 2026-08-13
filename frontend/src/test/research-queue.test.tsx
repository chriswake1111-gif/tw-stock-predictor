import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResearchQueuePage } from "../pages/ResearchQueuePage";
import { renderWithProviders } from "./render";


describe("Research Queue", () => {
  it("isolates scoped research writes without embedding an admin credential", () => {
    const source = readFileSync(resolve(process.cwd(), "src/api/researchClient.ts"), "utf8");
    expect(source).toMatch(/method:\s*"POST"/);
    expect(source).toContain("X-CSRF-Token");
    expect(source).not.toContain("X-Admin-API-Key");
    expect(source).not.toMatch(/(?:api[_-]?key|secret|bearer)\s*[:=]\s*["'][^"']+/i);
  });

  it("renders null and stale as separate neutral workflow facts", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      status: "available", comparison_cutoff: "2026-08-13T00:00:00Z", items: [{
        watchlist_item: { watchlist_item_id: "item-1", symbol: "2330.TW", membership_state: "active", created_at: "x", updated_at: "x", archived_at: null, workflow_contract_version: "research_review_queue_v1" },
        analysis_status: "available", freshness_status: "stale", comparison_status: "not_run",
        review_state: "baseline_not_set", comparison_has_deltas: null,
        stored_delta_count: 0, current_context_delta_count: 0,
        latest_snapshot_reference: { snapshot_id: "snapshot-1", symbol: "2330.TW" },
        latest_review_event_reference: null, reason_codes: ["review_baseline_not_set"],
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    renderWithProviders(<ResearchQueuePage />, "/research");
    fireEvent.change(screen.getByLabelText("比較截止時間（含時區）"), { target: { value: "2026-08-13T00:00:00Z" } });
    fireEvent.click(screen.getByRole("button", { name: "載入清單" }));
    expect(await screen.findByText("尚未設定複核基準")).toBeInTheDocument();
    expect(screen.getByText("stale")).toBeInTheDocument();
    expect(screen.queryByText("沒有變化")).not.toBeInTheDocument();
  });

  it("does not acknowledge on render or row inspection", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      status: "available", comparison_cutoff: "2026-08-13T00:00:00Z", items: [],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    renderWithProviders(<ResearchQueuePage />, "/research");
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });
});
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
