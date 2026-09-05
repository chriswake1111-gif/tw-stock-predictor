import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import App from "../App";
import { mockReadApi, renderWithProviders } from "./render";
import { SearchHomePage } from "../pages/SearchHomePage";
import { AdvancedConsolePage } from "../pages/AdvancedConsolePage";

describe("Phase 20 Usability & Bootstrap Tests", () => {
  beforeEach(() => {
    mockReadApi();
  });

  it("renders Search-First Home page with search input and quick stocks", async () => {
    renderWithProviders(<SearchHomePage />, "/");
    expect(screen.getByPlaceholderText(/請輸入股票代號/)).toBeInTheDocument();
    expect(screen.getAllByText("台積電").length).toBeGreaterThan(0);
    expect(screen.getAllByText("聯發科").length).toBeGreaterThan(0);
  });

  it("shows matching results when user types in search box", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/universe/search")) {
        return new Response(
          JSON.stringify({
            query: "2330",
            total_matches: 1,
            results: [
              {
                canonical_symbol: "2330.TW",
                official_code: "2330",
                venue: "TWSE",
                short_name: "台積電",
                display_name: "台灣積體電路製造股份有限公司",
                security_type: "股票",
                has_short_name: true,
              },
            ],
            coverage: {
              universe_status: "ready",
              total_instruments: 100,
              phase20_materialized_count: 100,
              coverage_ratio: 1.0,
              degraded_search_mode: false,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("/universe/coverage")) {
        return new Response(
          JSON.stringify({
            universe_status: "ready",
            total_instruments: 100,
            phase20_materialized_count: 100,
            coverage_ratio: 1.0,
            degraded_search_mode: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
    });

    renderWithProviders(<SearchHomePage />, "/");
    const input = screen.getByPlaceholderText(/請輸入股票代號/);
    fireEvent.change(input, { target: { value: "2330" } });

    await waitFor(() => {
      expect(screen.getByText("台灣積體電路製造股份有限公司")).toBeInTheDocument();
    });
  });

  it("renders StockResearchPage with official close, decision queue, and audit drawer", async () => {
    renderWithProviders(<App />, "/stocks/2330.TW");

    // Check header and official close
    await waitFor(() => {
      expect(screen.getByText(/2330/)).toBeInTheDocument();
      expect(screen.getByText(/980.00 元/)).toBeInTheDocument();
    });

    // Check Human Decision Queue
    expect(screen.getByText(/待人工審查與決策隊列/)).toBeInTheDocument();
    expect(screen.getByText(/VAL-02/)).toBeInTheDocument();

    // Check Audit Drawer toggle
    const auditBtn = screen.getByText("資料審計抽屜");
    fireEvent.click(auditBtn);

    expect(screen.getByText("數據來源與模型審計抽屜")).toBeInTheDocument();
    expect(screen.getByText(/snap_2026-09-04/)).toBeInTheDocument();
  });

  it("renders 5 primary navigation links in AppShell", async () => {
    renderWithProviders(<App />, "/");
    expect(screen.getAllByRole("link", { name: /首頁 \/ 搜尋/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /個股研究/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /市場概況/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /模型說明/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("link", { name: /進階與審計/ }).length).toBeGreaterThan(0);
  });

  it("renders AdvancedConsolePage with links to legacy and governance surfaces", async () => {
    renderWithProviders(<AdvancedConsolePage />, "/advanced");
    expect(screen.getByText("官方日收盤價材料化")).toBeInTheDocument();
    expect(screen.getByText("標的主檔治理")).toBeInTheDocument();
    expect(screen.getByText("歷史分析快照")).toBeInTheDocument();
    expect(screen.getByText("快照差異比對")).toBeInTheDocument();
    expect(screen.getByText("歷史觀察與驗證")).toBeInTheDocument();
    expect(screen.getByText("研究待辦隊列")).toBeInTheDocument();
  });
});
