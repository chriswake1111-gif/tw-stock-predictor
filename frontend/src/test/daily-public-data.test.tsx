import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "./render";
import { DailyPublicDataPanel } from "../components/DailyPublicDataPanel";

const data = {
  TaiwanStockPrice: { status: "available", source: "FinMind", official_exchange_source: false, observed_at: "2026-09-10", last_checked_at: "2026-09-11", rows: [
    { date: "2026-09-10", close: 100, change: 2, volume: 1000 },
    { date: "2026-09-09", close: 98, change: -1, volume: 0, zero_volume: true },
  ] },
  TaiwanStockPER: { status: "partial", last_update_status: "failed", last_update_reason: "來源暫時無法連線", rows: [{ date: "2026-09-10", pe: null, pb: 2, yield_ratio: 0.035 }] },
  TaiwanStockFinancialStatements: { status: "available", rows: [{ period_end: "2026-Q2", quarterly_eps: 3, available_at: null }, { period_end: "2026-Q1", quarterly_eps: 2, available_at: "2026-08-01" }] },
};

describe("DailyPublicDataPanel", () => {
  it("orders reversed rows by date and shows the actual most recent quote without mutating input", () => {
    const before = JSON.stringify(data);
    renderWithProviders(<DailyPublicDataPanel data={data} />);
    expect(screen.getByText(/最新收盤：100 元，漲跌：2 元，成交量：1,000 股/)).toBeInTheDocument();
    expect(JSON.stringify(data)).toBe(before);
  });
  it("shows null values, provenance warnings, and does not calculate TTM", () => {
    renderWithProviders(<DailyPublicDataPanel data={data} />);
    expect(screen.getByText(/FinMind 非官方資料/)).toBeInTheDocument();
    expect(screen.getByText(/未還原權息/)).toBeInTheDocument();
    expect(screen.getAllByText(/資料日期：2026-09-10/)).toHaveLength(2);
    expect(screen.getByText(/本機取得時間：2026-09-10/)).toBeInTheDocument();
    expect(screen.getByText("缺值")).toBeInTheDocument();
    expect(screen.getAllByText("殖利率").length).toBeGreaterThan(0);
    expect(screen.getByText("3.5%")).toBeInTheDocument();
    expect(screen.getByText("股數基準尚未核對，TTM暫不計算")).toBeInTheDocument();
    expect(screen.getByText(/本機首次可用時間：缺值/)).toBeInTheDocument();
  });

  it("switches between one year and three months", () => {
    renderWithProviders(<DailyPublicDataPanel data={data} onSelectPrice={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "近三個月" }));
    expect(screen.getByRole("button", { name: "近三個月" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps old rows visible after an update failure", () => {
    renderWithProviders(<DailyPublicDataPanel data={data} onSelectPrice={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/來源暫時無法連線/);
    expect(screen.getAllByRole("option", { name: /2026-09-10/ }).length).toBeGreaterThan(0);
  });

  it("does not show a false warning for the normal available update status", () => {
    const normal = { ...data, TaiwanStockPER: { ...data.TaiwanStockPER, last_update_status: "available" } };
    renderWithProviders(<DailyPublicDataPanel data={normal} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("uses observed_at as the historical range anchor", () => {
    const old = { ...data, TaiwanStockPrice: { ...data.TaiwanStockPrice, observed_at: "2026-09-11T09:00:00+08:00", rows: [
      { date: "2026-06-01", close: 80, volume: 10 },
      { date: "2025-01-01", close: 70, volume: 10 },
    ] } };
    renderWithProviders(<DailyPublicDataPanel data={old} onSelectPrice={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "近三個月" }));
    expect(screen.queryByRole("option", { name: /2026-06-01/ })).not.toBeInTheDocument();
  });

  it("selects traded prices only and calls onSelectPrice", () => {
    const onSelectPrice = vi.fn();
    renderWithProviders(<DailyPublicDataPanel data={data} onSelectPrice={onSelectPrice} />);
    fireEvent.change(screen.getByLabelText("選擇有成交日期："), { target: { value: "2026-09-10" } });
    expect(onSelectPrice).toHaveBeenCalledWith("2026-09-10", 100);
    expect(screen.queryByRole("option", { name: /2026-09-09/ })).not.toBeInTheDocument();
    const point = screen.getByRole("button", { name: /2026-09-10 收盤價 100 元/ });
    fireEvent.keyDown(point, { key: "Enter" });
    expect(onSelectPrice).toHaveBeenCalledTimes(2);
  });

  it("renders available status without exposing English status text", () => {
    const normal = { ...data, TaiwanStockPER: { ...data.TaiwanStockPER, status: "available", last_update_status: "not_started" } };
    renderWithProviders(<DailyPublicDataPanel data={normal} />);
    expect(screen.getAllByText(/狀態：可用/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("discloses excluded dates, breaks the line, and never offers them as anchors", () => {
    const onSelectPrice = vi.fn();
    const missing = { ...data, TaiwanStockPrice: { ...data.TaiwanStockPrice,
      rows: [
        { date: "2026-09-07", close: 94, volume: 800 },
        { date: "2026-09-08", close: 96, volume: 900 },
        { date: "2026-09-10", close: 100, volume: 1000 },
      ],
      excluded_rows: [{ date: "2026-09-09", reason: "source_all_zero_price_and_activity" }],
    } };
    const before = JSON.stringify(missing);
    renderWithProviders(<DailyPublicDataPanel data={missing} onSelectPrice={onSelectPrice} />);
    expect(screen.getByRole("note")).toHaveTextContent("2026-09-09：來源價格、成交量與成交金額皆為零");
    expect(screen.getByRole("note")).toHaveTextContent("是否停牌仍須核對公告");
    const chart = screen.getByRole("img", { name: "收盤價折線與成交量柱" });
    const segments = chart.querySelectorAll("polyline");
    expect(segments).toHaveLength(2);
    expect(segments[0]!.getAttribute("points")?.split(" ")).toHaveLength(2);
    expect(segments[1]!.getAttribute("points")?.split(" ")).toHaveLength(1);
    expect(chart.querySelectorAll("rect")).toHaveLength(3);
    expect(screen.queryByRole("option", { name: /2026-09-09/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /2026-09-09 收盤價/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "2026-09-10 收盤價 100 元" }));
    expect(onSelectPrice).toHaveBeenCalledWith("2026-09-10", 100);
    expect(JSON.stringify(missing)).toBe(before);
  });

  it("keeps other datasets visible when every price is excluded", () => {
    const empty = { ...data, TaiwanStockPrice: { ...data.TaiwanStockPrice,
      status: "insufficient_data", rows: [],
      excluded_rows: [{ date: "2026-09-10", reason: "source_all_zero_price_and_activity" }],
    } };
    renderWithProviders(<DailyPublicDataPanel data={empty} onSelectPrice={vi.fn()} />);
    expect(screen.getByRole("note")).toHaveTextContent("2026-09-10");
    expect(screen.queryByRole("img", { name: "收盤價折線與成交量柱" })).not.toBeInTheDocument();
    expect(screen.getByText("目前沒有行情資料可顯示。")).toBeInTheDocument();
    expect(screen.getByText("3.5%")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /2026-09-10/ })).not.toBeInTheDocument();
  });
});
