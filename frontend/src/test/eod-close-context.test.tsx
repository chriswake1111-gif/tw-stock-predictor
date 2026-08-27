import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EodCloseContextPage } from "../pages/EodCloseContextPage";
import { renderWithProviders } from "./render";

const baseContext = {
  contract_version: "eod_close_context_v1" as const,
  reason_codes: [],
  canonical_symbol: "2330.TW",
  instrument_id: "instrument-1",
  official_code: "2330",
  venue: "TWSE",
  security_type: "股票",
  product_scope: "supported_stock" as const,
  knowledge_cutoff_at: null,
  evaluated_at: "2026-08-27T08:00:00.000000Z",
  selection_scope: null,
  selected_trade_date: "2026-08-27",
  currency: "TWD",
  unit: "TWD_per_share",
  price_semantics: "official_reported_close_v1",
  freshness_state: "current" as const,
  current_complete: true,
  provider: "TWSE",
  resource_key: "twse.eod.stock_day_all",
  source_url: "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
  source_trade_date: "2026-08-27",
  observed_at: "2026-08-27T05:00:00Z",
  source_scope: "twse_whole_market_daily_close",
  quality_flags: [],
};

describe("Phase 14 EOD context surface", () => {
  it("renders non-available close as null and keeps the browser GET-only", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      ...baseContext,
      status: "insufficient_data",
      close_value: null,
      current_complete: false,
      reason_codes: ["official_zero_volume_not_public_eligible"],
      freshness_state: "current",
    }), { status: 200 }));
    const user = userEvent.setup();
    renderWithProviders(<EodCloseContextPage />);

    await user.click(screen.getByRole("button", { name: "讀取收盤情境" }));
    const card = await screen.findByRole("region");
    expect(card).toHaveTextContent("資料不足");
    expect(card).toHaveTextContent("—");
    expect(card).not.toHaveTextContent("1005");
    expect(fetchSpy.mock.calls.every(([, init]) => !init || init.method === "GET")).toBe(true);
  });

  it("discloses the neutral context boundary when a close is available", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      ...baseContext,
      status: "available",
      close_value: "1005",
    }), { status: 200 }));
    const user = userEvent.setup();
    renderWithProviders(<EodCloseContextPage />);

    await user.click(screen.getByRole("button", { name: "讀取收盤情境" }));
    const card = await screen.findByRole("region");
    expect(card).toHaveTextContent("1005 TWD");
    expect(card).toHaveTextContent("官方未調整日收盤情境；不代表目標價、買賣或推薦");
  });
});
