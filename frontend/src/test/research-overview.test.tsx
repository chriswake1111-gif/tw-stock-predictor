import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResearchOverview } from "../components/ResearchOverview";
import { ResearchModelResults } from "../components/ResearchModelResults";
import { ResearchJournalPanel } from "../components/DailyResearchJournal";
import { guidedResearchFixture } from "./guidedResearchFixture";
import { renderWithProviders } from "./render";

describe("guided stock research", () => {
  it("keeps valid calculations visible while explaining unmatched years and legacy PE", () => {
    const summary = guidedResearchFixture();
    summary.valuation_context.year_pairing = { policy_version: "same_fiscal_year_v1", status: "needs_human_input", unmatched_eps_years: [2027], unbound_pe_ids: ["legacy-pe"] };
    renderWithProviders(<><ResearchOverview summary={summary} historical={false} onOpenSection={vi.fn()} /><ResearchModelResults summary={summary} /></>);
    expect(screen.getByText(/部分估值缺少同年度 PE/)).toBeInTheDocument();
    expect(screen.getByText(/2027 年預估 EPS 尚無同年度已核准 PE/)).toBeInTheDocument();
    expect(screen.getByText(/有舊 PE 尚未註明適用年度/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "設定 PE 適用年度" })).toHaveAttribute("href", "#local-assumptions");
    expect(screen.getByText(/自訂敏感度 20 倍：2,154.8 元/)).toBeInTheDocument();
  });
  it("separates dated official and third-party prices and keeps missing data visible despite an empty queue", () => {
    renderWithProviders(<ResearchOverview summary={guidedResearchFixture()} historical={false} onOpenSection={vi.fn()} />);
    for (const name of ["目前發生什麼", "程式算出什麼", "還缺什麼、哪些要留意", "下一步做什麼"]) expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    expect(screen.getByText("2,410.00 元")).toBeInTheDocument();
    expect(screen.getByText("補充行情：2,380 元")).toBeInTheDocument();
    expect(screen.getByText(/兩筆行情日期不同/)).toBeInTheDocument();
    expect(screen.getByText(/2026-09-14 · FinMind（非官方交易所來源）/)).toBeInTheDocument();
    expect(screen.getByText("0.92%")).toBeInTheDocument();
    expect(screen.getByText(/本次更新失敗，保留已取得資料/)).toBeInTheDocument();
    expect(screen.getByText(/暫不提供可採用的 TTM EPS/)).toBeInTheDocument();
    expect(screen.getByText(/沒有可用的計算結果/)).toBeInTheDocument();
  });

  it("does not label undated or missing data as zero or official", () => {
    const summary = guidedResearchFixture();
    summary.public_data = {};
    summary.market_context.official_close = null;
    summary.market_context.close_status = "insufficient_data";
    renderWithProviders(<ResearchOverview summary={summary} historical={false} onOpenSection={vi.fn()} />);
    expect(screen.getByText("尚無官方報價")).toBeInTheDocument();
    expect(screen.getByText(/歷史價量尚缺/)).toBeInTheDocument();
    expect(screen.queryByText("0 倍")).not.toBeInTheDocument();
    expect(screen.queryByText(/補充行情：/)).not.toBeInTheDocument();
  });

  it("keeps historical navigation read-only", () => {
    const open = vi.fn();
    renderWithProviders(<ResearchOverview summary={guidedResearchFixture()} historical onOpenSection={open} />);
    expect(screen.queryByRole("button", { name: "比較前次／保存筆記" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看或設定研究假設" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看程式情境與依據" }));
    expect(open).toHaveBeenCalledWith("research-models");
  });

  it("uses actions only to navigate; no automatic save or approval", () => {
    const open = vi.fn();
    const fetch = vi.spyOn(globalThis, "fetch");
    renderWithProviders(<ResearchOverview summary={guidedResearchFixture()} historical={false} onOpenSection={open} />);
    fireEvent.click(screen.getByRole("button", { name: "比較前次／保存筆記" }));
    expect(open).toHaveBeenCalledWith("daily-journal");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("filters display by EPS identity without changing any calculated values", () => {
    const summary = guidedResearchFixture();
    const original = JSON.stringify(summary);
    renderWithProviders(<ResearchModelResults summary={summary} />);
    expect(screen.getByText(/自訂敏感度 20 倍：2,154.8 元/)).toBeInTheDocument();
    const choice = screen.getByRole("option", { name: /2026 年 · EPS 107.74/ }) as HTMLOptionElement;
    fireEvent.change(screen.getByLabelText("想先看哪一組 EPS？"), { target: { value: choice.value } });
    expect(screen.queryByText(/舊年度敏感度：900 元/)).not.toBeInTheDocument();
    expect(screen.getByText(/自訂敏感度 20 倍：2,154.8 元/)).toBeInTheDocument();
    expect(screen.getByText(/保存研究仍包含全部情境/)).toBeInTheDocument();
    expect(JSON.stringify(summary)).toBe(original);
  });

  it("never presents stale targets from an unavailable context as calculated scenarios", () => {
    const summary = guidedResearchFixture();
    summary.valuation_context.status = "needs_human_judgment";
    summary.technical_context = { status: "insufficient_data", reason_code: null, targets: { scenarios: [{ calculated_level: 1234 }] } };
    renderWithProviders(<><ResearchOverview summary={summary} historical={false} onOpenSection={vi.fn()} /><ResearchModelResults summary={summary} /></>);
    expect(screen.queryByText(/2,154.8/)).not.toBeInTheDocument();
    expect(screen.queryByText(/1,234/)).not.toBeInTheDocument();
    expect(screen.getByText("估值情境尚不可計算")).toBeInTheDocument();
  });

  it("appends optional note prompts without overwriting text or saving", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ entries: [], comparison: { status: "no_previous" } })));
    renderWithProviders(<ResearchJournalPanel symbol="2330.TW" cutoff="2026-09-14T16:00:00Z" />);
    await waitFor(() => expect(screen.queryByText("正在讀取前次保存研究…")).not.toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox", { name: "研究筆記" }), { target: { value: "原有觀察" } });
    fireEvent.click(screen.getByRole("button", { name: "加入研究筆記引導" }));
    expect((screen.getByRole("textbox", { name: "研究筆記" }) as HTMLTextAreaElement).value).toContain("原有觀察\n\n研究時間尺度");
    expect(screen.getByRole("status")).toHaveTextContent("尚未保存");
    expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
});
