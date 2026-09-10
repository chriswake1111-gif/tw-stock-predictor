import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { Routes, Route, useNavigate } from "react-router-dom";
import { renderWithProviders } from "./render";
import { StockResearchPage } from "../pages/StockResearchPage";
import { bootstrapSymbol, getResearchSummary } from "../api/phase20Client";
import { getOperationDetails } from "../api/dataOperationsClient";
import type { ResearchSummaryResponse } from "../api/types";

vi.mock("../api/phase20Client", () => ({ bootstrapSymbol: vi.fn(), getResearchSummary: vi.fn() }));
vi.mock("../api/dataOperationsClient", () => ({ getOperationDetails: vi.fn() }));

const summary = (symbol: string) => ({
  canonical_symbol: symbol, official_code: symbol.split(".")[0], venue: "TWSE", company_name: symbol,
  market_context: { settled_trade_date: "2026-09-04", official_close: 980, close_status: "available", is_market_closed: false },
  valuation_context: { status: "insufficient_data" }, technical_context: { status: "insufficient_data" },
  screening_context: {}, human_decision_queue: [], audit_reference: { model_version: "2.0.0" },
  knowledge_cutoff_at: "2026-09-08T11:00:00Z",
}) as unknown as ResearchSummaryResponse;

function Harness() {
  const navigate = useNavigate();
  return <><button onClick={() => navigate("/stocks/2408.TW")}>換成南亞科</button>
    <Routes><Route path="/stocks/:symbol" element={<StockResearchPage />} /></Routes></>;
}

describe("installed research refresh", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getResearchSummary).mockImplementation(async symbol => summary(symbol));
    vi.mocked(bootstrapSymbol).mockResolvedValue({ status: "preparing", canonical_symbol: "2330.TW", operation_id: "op1" });
  });
  afterEach(() => vi.useRealTimers());

  it("shows cached quotes while the update is still pending", async () => {
    vi.mocked(getOperationDetails).mockImplementation(() => new Promise(() => {}));
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    expect(await screen.findByText(/980.00 元/)).toBeInTheDocument();
    expect(screen.getByText(/可先查閱下方本機資料/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新資料" })).toBeDisabled();
  });

  it("renders calculated model prices and their evidence", async () => {
    const result = summary("2330.TW");
    result.valuation_context = { status: "available", reason_code: null, target_matrix: [{
      target_price: 1040, eps_value: 52, pe_value: 20, pe_label: "基準", fiscal_year: 2027,
      source_name: "測試研究來源", approval_ids: { "VAL-02": "eps-approval", "VAL-04": "pe-approval" },
    }] };
    vi.mocked(getResearchSummary).mockResolvedValue(result);
    vi.mocked(getOperationDetails).mockResolvedValue({ status: "partial", error_detail: "Phase16 context is identity_unresolved" });
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    expect(await screen.findByText(/基準：1,040 元/)).toBeInTheDocument();
    expect(screen.getByText(/測試研究來源/, { selector: "p" })).toBeInTheDocument();
    expect(await screen.findByText(/歷史研究的事件時間證據仍待確認/)).toBeInTheDocument();
    expect(screen.getByText(/980.00 元/)).toBeInTheDocument();
  });

  it("stops on partial and keeps local data with the actual calendar explanation", async () => {
    vi.mocked(getOperationDetails).mockResolvedValue({ status: "partial", error_detail: "calendar proof missing" });
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    expect(await screen.findByText(/缺少官方交易日證據/)).toBeInTheDocument();
    expect(screen.getByText(/980.00 元/)).toBeInTheDocument();
    expect(bootstrapSymbol).toHaveBeenCalledTimes(1);
    expect(bootstrapSymbol).toHaveBeenCalledWith("2330.TW", false, expect.any(AbortSignal));
    expect(screen.getByRole("button", { name: "更新資料" })).toBeEnabled();
  });

  it("a successful target operation loads results without starting a second refresh", async () => {
    vi.mocked(getOperationDetails).mockResolvedValue({ status: "succeeded" });
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    await screen.findByText(/已完成官方來源檢查/);
    expect(bootstrapSymbol).toHaveBeenCalledTimes(1);
    expect(screen.getByText("本機行情日期：2026-09-04")).toBeInTheDocument();
  });

  it("a failed update preserves the local summary and shows failure", async () => {
    vi.mocked(getOperationDetails).mockResolvedValue({ status: "failed", error_detail: "official source timeout" });
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    expect(await screen.findByText(/official source timeout/)).toBeInTheDocument();
    expect(screen.getByText(/以下保留本機資料/)).toBeInTheDocument();
    expect(screen.getByText(/980.00 元/)).toBeInTheDocument();
    expect(bootstrapSymbol).toHaveBeenCalledTimes(1);
  });

  it("historical navigation and reload never trigger external updates", async () => {
    renderWithProviders(<Harness />, "/stocks/2330.TW?as_of=2026-09-04T16:00:00Z");
    await screen.findByText(/980.00 元/);
    fireEvent.click(screen.getByRole("button", { name: "重新載入" }));
    await waitFor(() => expect(getResearchSummary).toHaveBeenCalledTimes(2));
    expect(bootstrapSymbol).not.toHaveBeenCalled();
  });

  it("switching symbol aborts the old request and ignores its late result", async () => {
    let finishOld!: (value: Record<string, unknown>) => void;
    let oldSignal: AbortSignal | undefined;
    vi.mocked(getOperationDetails).mockImplementationOnce((_id, signal) => {
      oldSignal = signal;
      return new Promise(resolve => { finishOld = resolve; });
    }).mockResolvedValue({ status: "succeeded" });
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    await waitFor(() => expect(getOperationDetails).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByText("換成南亞科"));
    await screen.findByText(/已完成官方來源檢查/);
    expect(oldSignal?.aborted).toBe(true);
    await act(async () => finishOld({ status: "failed", error_detail: "old failure" }));
    expect(screen.queryByText(/old failure/)).not.toBeInTheDocument();
    expect(bootstrapSymbol).toHaveBeenLastCalledWith("2408.TW", false, expect.any(AbortSignal));
  });

  it("uses an explicit refresh only when the user clicks update", async () => {
    vi.mocked(bootstrapSymbol).mockResolvedValue({ status: "ready", operation_id: null, canonical_symbol: "2330.TW" });
    vi.mocked(getOperationDetails).mockResolvedValue({ status: "ready" });
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    await screen.findByText("本機行情日期：2026-09-04");
    await waitFor(() => expect(screen.getByRole("button", { name: "更新資料" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "更新資料" }));
    await waitFor(() => expect(bootstrapSymbol).toHaveBeenLastCalledWith("2330.TW", true, expect.any(AbortSignal)));
  });

  it("the total deadline aborts a stalled HTTP request and restores controls", async () => {
    vi.useFakeTimers();
    vi.mocked(getOperationDetails).mockImplementation((_id, signal) => new Promise((_resolve, reject) => {
      signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    renderWithProviders(<Harness />, "/stocks/2330.TW");
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(getOperationDetails).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(180001); });
    expect(screen.getByText("資料準備逾時，請重試更新。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新資料" })).toBeEnabled();
    expect(bootstrapSymbol).toHaveBeenCalledTimes(1);
  });
});
