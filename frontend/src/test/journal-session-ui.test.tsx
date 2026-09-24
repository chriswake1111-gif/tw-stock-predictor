import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { researchMutation } from "../api/researchClient";
import { DailyJournalOverview, ResearchJournalPanel } from "../components/DailyResearchJournal";

vi.mock("../api/researchClient", async importOriginal => ({
  ...await importOriginal<typeof import("../api/researchClient")>(), researchMutation: vi.fn(),
}));

const initialCutoff = "2026-09-15T00:00:00Z";
const nextCutoff = "2026-09-16T00:00:00Z";

function mount(route: string) {
  function Panel() {
    const [cutoff, setCutoff] = useState(initialCutoff);
    return <><button onClick={() => setCutoff(nextCutoff)}>完成行情更新</button>
      <ResearchJournalPanel symbol="2408.TW" cutoff={cutoff} /></>;
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60000 } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><Routes>
    <Route path="/research/daily" element={<DailyJournalOverview />} />
    <Route path="/stocks/2408.TW" element={<Panel />} />
  </Routes></MemoryRouter></QueryClientProvider>);
  return client;
}

describe("research journal after session recovery and navigation", () => {
  beforeEach(() => { vi.restoreAllMocks(); vi.mocked(researchMutation).mockReset(); });

  it("reads the newly saved note when returning to a previously visited daily page", async () => {
    let note = "較早的筆記";
    const read = vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
      const entry = { entry_id: "synthetic-entry", note, created_at: initialCutoff, summary: { short_name: "南亞科", market_context: { official_close: 465.5 } } };
      if (String(input).includes("after_symbol")) return Response.json({ server_time: initialCutoff,
        items: [{ symbol: "2408.TW", current: entry, previous: entry, comparison: { status: "available", facts: [] } }] });
      return Response.json({ entries: [entry], comparison: { status: "available", facts: [] } });
    });
    vi.mocked(researchMutation).mockImplementation(async (_path, payload) => {
      note = (payload as { note: string }).note;
      return {};
    });
    const client = mount("/research/daily");
    await screen.findByText("前次筆記：較早的筆記");
    fireEvent.click(screen.getByRole("link", { name: "2408.TW 南亞科" }));
    fireEvent.change(screen.getByLabelText("研究筆記"), { target: { value: "剛保存的新筆記" } });
    fireEvent.click(screen.getByRole("button", { name: "保存當次研究與筆記" }));
    await screen.findByText(/已保存當次資料/);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存當次研究與筆記" })).toBeEnabled());
    fireEvent.click(screen.getByRole("link", { name: "查看每日複核" }));
    await screen.findByText("前次筆記：剛保存的新筆記");
    expect(read.mock.calls.filter(([url]) => String(url).includes("after_symbol"))).toHaveLength(2);
    expect(researchMutation).toHaveBeenCalledTimes(1);
    client.clear();
  });

  it("refreshes the comparison after a new data cutoff without clearing the unsaved note", async () => {
    let reads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      reads++;
      return Response.json({ entries: [], comparison: { status: "available", facts: [{ field: "official_close",
        before: { value: 2410, date: "2026-09-11" }, after: { value: reads === 1 ? 2410 : 2385, date: "2026-09-15" },
        delta: reads === 1 ? 0 : -25, status: "comparable" }] } });
    });
    const client = mount("/stocks/2408.TW");
    await screen.findByText("2,410／2026-09-15");
    fireEvent.change(screen.getByLabelText("研究筆記"), { target: { value: "尚未保存的觀察" } });
    fireEvent.click(screen.getByRole("button", { name: "完成行情更新" }));
    await screen.findByText("2,385／2026-09-15");
    expect(screen.getByLabelText("研究筆記")).toHaveValue("尚未保存的觀察");
    expect(researchMutation).not.toHaveBeenCalled();
    expect(reads).toBe(2);
    client.clear();
  });

  it("explains a rejected save, keeps the note and requires another click using the same key", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => Response.json({ entries: [], comparison: { status: "no_previous" } }));
    vi.mocked(researchMutation).mockRejectedValueOnce(new Error("csrf_refresh_required")).mockResolvedValueOnce({});
    const client = mount("/stocks/2408.TW");
    fireEvent.change(screen.getByLabelText("研究筆記"), { target: { value: "請保留這份筆記" } });
    const save = screen.getByRole("button", { name: "保存當次研究與筆記" });
    fireEvent.click(save);
    await screen.findByText(/操作驗證已更新，本次操作尚未執行/);
    expect(screen.getByLabelText("研究筆記")).toHaveValue("請保留這份筆記");
    expect(researchMutation).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/已保存當次資料/)).not.toBeInTheDocument();
    fireEvent.click(save);
    await screen.findByText(/已保存當次資料/);
    expect(researchMutation).toHaveBeenCalledTimes(2);
    const calls = vi.mocked(researchMutation).mock.calls;
    expect(calls[0]).toEqual(calls[1]);
    await waitFor(() => expect(save).toBeEnabled());
    client.clear();
  });
});
