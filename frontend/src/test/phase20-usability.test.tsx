import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import App from "../App";
import { mockReadApi, renderWithProviders } from "./render";
import { SearchHomePage } from "../pages/SearchHomePage";
import { AdvancedConsolePage } from "../pages/AdvancedConsolePage";
import { FirstRunPrepCard } from "../components/FirstRunPrepCard";
import { ShortNameUpgradeBanner } from "../components/ShortNameUpgradeBanner";

describe("Phase 20 Usability & Bootstrap Tests", () => {
  beforeEach(() => {
    localStorage.clear();
    mockReadApi();
  });

  it("renders Search-First Home page with search input and recent searches when present", async () => {
    localStorage.setItem(
      "tw_stock_recent_searches",
      JSON.stringify([
        { code: "2330.TW", name: "台積電" },
        { code: "2454.TW", name: "聯發科" },
      ])
    );
    renderWithProviders(<SearchHomePage />, "/");
    expect(screen.getByPlaceholderText(/請輸入股票代號/)).toBeInTheDocument();
    expect(screen.getByText("最近搜尋標的")).toBeInTheDocument();
    expect(screen.getAllByText("台積電").length).toBeGreaterThan(0);
    expect(screen.getAllByText("聯發科").length).toBeGreaterThan(0);
  });

  it("omits recent search section when search history is empty", async () => {
    localStorage.removeItem("tw_stock_recent_searches");
    renderWithProviders(<SearchHomePage />, "/");
    expect(screen.queryByText("最近搜尋標的")).not.toBeInTheDocument();
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
    expect(screen.getByText("官方日收盤價管理")).toBeInTheDocument();
    expect(screen.getByText("標的主檔治理")).toBeInTheDocument();
    expect(screen.getByText("歷史分析快照")).toBeInTheDocument();
    expect(screen.getByText("快照差異比對")).toBeInTheDocument();
    expect(screen.getByText("歷史觀察與驗證")).toBeInTheDocument();
    expect(screen.getByText("研究待辦隊列")).toBeInTheDocument();
  });

  // P1-1: FirstRunPrepCard parent status mapping tests
  describe("FirstRunPrepCard parent status handling", () => {
    const terminalSuccessStatuses = ["succeeded", "partial"] as const;
    terminalSuccessStatuses.forEach((status) => {
      it(`handles terminal success status: ${status}`, async () => {
        const onComplete = vi.fn();
        vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
          const url = String(input);
          if (url.includes("/csrf-token")) {
            return new Response(JSON.stringify({ csrf_token: "mock-csrf" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("/data-operations/sync")) {
            return new Response(JSON.stringify({ operation_id: "op_test_success" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("op_test_success")) {
            return new Response(JSON.stringify({ operation_id: "op_test_success", status }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          return new Response(JSON.stringify({}), { status: 200 });
        });

        renderWithProviders(<FirstRunPrepCard onPreparationComplete={onComplete} />);
        fireEvent.click(screen.getByText("準備股票清單"));

        await waitFor(() => {
          expect(onComplete).toHaveBeenCalled();
          expect(screen.getByText(/股票清單準備完成/)).toBeInTheDocument();
        });
      });
    });

    const terminalErrorStatuses = ["failed", "cancelled", "interrupted"] as const;
    terminalErrorStatuses.forEach((status) => {
      it(`handles terminal error status: ${status}`, async () => {
        vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
          const url = String(input);
          if (url.includes("/csrf-token")) {
            return new Response(JSON.stringify({ csrf_token: "mock-csrf" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("/data-operations/sync")) {
            return new Response(JSON.stringify({ operation_id: "op_test_err" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("op_test_err")) {
            return new Response(JSON.stringify({ operation_id: "op_test_err", status }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          return new Response(JSON.stringify({}), { status: 200 });
        });

        renderWithProviders(<FirstRunPrepCard />);
        fireEvent.click(screen.getByText("準備股票清單"));

        await waitFor(() => {
          expect(screen.getByText(new RegExp(`狀態：${status}`))).toBeInTheDocument();
        });
      });
    });
  });

  // P1-1: ShortNameUpgradeBanner parent status mapping tests
  describe("ShortNameUpgradeBanner parent status handling", () => {
    const coverage = {
      universe_status: "short_names_partial" as const,
      total_instruments: 100,
      phase20_materialized_count: 50,
      coverage_ratio: 0.5,
      degraded_search_mode: true,
    };

    const terminalSuccessStatuses = ["succeeded", "partial"] as const;
    terminalSuccessStatuses.forEach((status) => {
      it(`handles terminal success status: ${status}`, async () => {
        const onUpgrade = vi.fn();
        vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
          const url = String(input);
          if (url.includes("/csrf-token")) {
            return new Response(JSON.stringify({ csrf_token: "mock-csrf" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("/data-operations/sync")) {
            return new Response(JSON.stringify({ operation_id: "op_banner_success" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("op_banner_success")) {
            return new Response(JSON.stringify({ operation_id: "op_banner_success", status }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          return new Response(JSON.stringify({}), { status: 200 });
        });

        renderWithProviders(<ShortNameUpgradeBanner coverage={coverage} onUpgradeComplete={onUpgrade} />);
        fireEvent.click(screen.getByText("更新股票簡稱清單"));

        await waitFor(() => {
          expect(onUpgrade).toHaveBeenCalled();
          expect(screen.getByText("更新完成")).toBeInTheDocument();
        });
      });
    });

    const terminalErrorStatuses = ["failed", "cancelled", "interrupted"] as const;
    terminalErrorStatuses.forEach((status) => {
      it(`handles terminal error status: ${status}`, async () => {
        vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
          const url = String(input);
          if (url.includes("/csrf-token")) {
            return new Response(JSON.stringify({ csrf_token: "mock-csrf" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("/data-operations/sync")) {
            return new Response(JSON.stringify({ operation_id: "op_banner_err" }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          if (url.includes("op_banner_err")) {
            return new Response(JSON.stringify({ operation_id: "op_banner_err", status }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          }
          return new Response(JSON.stringify({}), { status: 200 });
        });

        renderWithProviders(<ShortNameUpgradeBanner coverage={coverage} />);
        fireEvent.click(screen.getByText("更新股票簡稱清單"));

        await waitFor(() => {
          expect(screen.getByText(new RegExp(`狀態：${status}`))).toBeInTheDocument();
        });
      });
    });
  });

  it("P1-A: StockResearchPage re-evaluates bootstrap when unrelated waiting_for_data_operation terminates", async () => {
    let bootstrapCallCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/csrf-token")) {
        return new Response(JSON.stringify({ csrf_token: "mock-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/research/bootstrap")) {
        bootstrapCallCount += 1;
        if (bootstrapCallCount === 1) {
          // First bootstrap returns waiting_for_data_operation (unrelated global sync)
          return new Response(
            JSON.stringify({
              status: "waiting_for_data_operation",
              canonical_symbol: "2330.TW",
              operation_id: "op_global_sync",
              message: "Another operation is active",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        if (bootstrapCallCount === 2) {
          // Second bootstrap returns preparing (target-aware ENABLE_SYMBOL)
          return new Response(
            JSON.stringify({
              status: "preparing",
              canonical_symbol: "2330.TW",
              operation_id: "op_target_enable",
              message: "Target enable started",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        // Terminal bootstrap returns ready
        return new Response(
          JSON.stringify({
            status: "ready",
            canonical_symbol: "2330.TW",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("op_global_sync")) {
        return new Response(
          JSON.stringify({ operation_id: "op_global_sync", status: "succeeded" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("op_target_enable")) {
        return new Response(
          JSON.stringify({ operation_id: "op_target_enable", status: "succeeded" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("/research/summary/2330.TW")) {
        return new Response(
          JSON.stringify({
            canonical_symbol: "2330.TW",
            official_code: "2330",
            venue: "TWSE",
            company_name: "台積電",
            market_context: {
              settled_trade_date: "2026-09-04",
              official_close: 980.0,
              close_status: "available",
              is_market_closed: true,
              market_status_label: "已正式結算收盤",
            },
            valuation_context: { status: "available" },
            technical_context: { status: "available" },
            screening_context: {},
            human_decision_queue: [],
            audit_reference: { model_version: "2.0.0" },
            knowledge_cutoff_at: "2026-09-04T16:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });

    renderWithProviders(<App />, "/stocks/2330.TW");

    await waitFor(() => {
      expect(bootstrapCallCount).toBeGreaterThanOrEqual(2);
      expect(screen.getByText(/980.00 元/)).toBeInTheDocument();
    });
  });

  it("P2: StockResearchPage handles multiple repeated waiting cycles before preparing and ready", async () => {
    let bootstrapCallCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/csrf-token")) {
        return new Response(JSON.stringify({ csrf_token: "mock-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/research/bootstrap")) {
        bootstrapCallCount += 1;
        if (bootstrapCallCount === 1) {
          // Cycle 1: waiting for op1
          return new Response(
            JSON.stringify({
              status: "waiting_for_data_operation",
              canonical_symbol: "2330.TW",
              operation_id: "op_sync_cycle_1",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        if (bootstrapCallCount === 2) {
          // Cycle 2: waiting for op2
          return new Response(
            JSON.stringify({
              status: "waiting_for_data_operation",
              canonical_symbol: "2330.TW",
              operation_id: "op_sync_cycle_2",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        if (bootstrapCallCount === 3) {
          // Cycle 3: preparing target
          return new Response(
            JSON.stringify({
              status: "preparing",
              canonical_symbol: "2330.TW",
              operation_id: "op_target_enable",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } }
          );
        }
        // Cycle 4: ready
        return new Response(
          JSON.stringify({
            status: "ready",
            canonical_symbol: "2330.TW",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("op_sync_cycle_1")) {
        return new Response(
          JSON.stringify({ operation_id: "op_sync_cycle_1", status: "succeeded" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("op_sync_cycle_2")) {
        return new Response(
          JSON.stringify({ operation_id: "op_sync_cycle_2", status: "succeeded" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("op_target_enable")) {
        return new Response(
          JSON.stringify({ operation_id: "op_target_enable", status: "succeeded" }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (url.includes("/research/summary/2330.TW")) {
        return new Response(
          JSON.stringify({
            canonical_symbol: "2330.TW",
            official_code: "2330",
            venue: "TWSE",
            company_name: "台積電",
            market_context: {
              settled_trade_date: "2026-09-04",
              official_close: 980.0,
              close_status: "available",
              is_market_closed: true,
              market_status_label: "已正式結算收盤",
            },
            valuation_context: { status: "available" },
            technical_context: { status: "available" },
            screening_context: {},
            human_decision_queue: [],
            audit_reference: { model_version: "2.0.0" },
            knowledge_cutoff_at: "2026-09-04T16:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });

    renderWithProviders(<App />, "/stocks/2330.TW");

    await waitFor(() => {
      expect(bootstrapCallCount).toBe(4);
      expect(screen.getByText(/980.00 元/)).toBeInTheDocument();
    });
  });
});
