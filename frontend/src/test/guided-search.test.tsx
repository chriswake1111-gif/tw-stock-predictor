import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SearchHomePage } from "../pages/SearchHomePage";
import { getUniverseCoverage, searchUniverse } from "../api/phase20Client";
import type { UniverseSearchResponse } from "../api/types";
import { renderWithProviders } from "./render";

vi.mock("../api/phase20Client", () => ({ searchUniverse: vi.fn(), getUniverseCoverage: vi.fn() }));
const coverage = { universe_status: "ready" as const, total_instruments: 2, phase20_materialized_count: 2, coverage_ratio: 1, degraded_search_mode: false };
function response(code: string, name: string): UniverseSearchResponse {
  return { query: code, coverage, total_matches: 1, results: [{ canonical_symbol: `${code}.TW`, official_code: code, short_name: name, display_name: name, venue: "TWSE", security_type: "股票", has_short_name: true }] };
}
beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); vi.mocked(getUniverseCoverage).mockResolvedValue(coverage); });

describe("guided search reliability", () => {
  it("distinguishes a failed request from no matches and provides retry", async () => {
    vi.mocked(searchUniverse).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(response("2330", "台積電"));
    renderWithProviders(<SearchHomePage />);
    fireEvent.change(screen.getByRole("textbox", { name: "搜尋股票代號或中文名稱" }), { target: { value: "台積電" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("尚不能判定有沒有這檔股票");
    expect(screen.queryByText(/查無符合/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重試搜尋" }));
    expect(await screen.findByRole("button", { name: /2330 台積電/ })).toBeInTheDocument();
  });

  it("ignores a late response when the user quickly changes symbols", async () => {
    let finish!: (value: UniverseSearchResponse) => void;
    vi.mocked(searchUniverse).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValueOnce(response("2408", "南亞科"));
    renderWithProviders(<SearchHomePage />);
    const input = screen.getByRole("textbox", { name: "搜尋股票代號或中文名稱" });
    fireEvent.change(input, { target: { value: "台積電" } });
    await waitFor(() => expect(searchUniverse).toHaveBeenCalledTimes(1));
    const oldSignal = vi.mocked(searchUniverse).mock.calls[0]?.[2];
    fireEvent.change(input, { target: { value: "南亞科" } });
    expect(await screen.findByRole("button", { name: /2408 南亞科/ })).toBeInTheDocument();
    await act(async () => finish(response("2330", "台積電")));
    expect(oldSignal?.aborted).toBe(true);
    expect(screen.queryByRole("button", { name: /2330 台積電/ })).not.toBeInTheDocument();
  });
});
