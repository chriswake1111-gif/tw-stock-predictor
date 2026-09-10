import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "./render";
import { LocalAssumptionEditor } from "../components/LocalAssumptionEditor";

describe("LocalAssumptionEditor", () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  it("previews then saves a draft without auto approval", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/assumptions/2330.TW") && (!init || init.method !== "POST")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
      if (url.endsWith("/preview")) return new Response(JSON.stringify({ calculation: "preview_only" }), { status: 200 });
      if (url.endsWith("/draft")) return new Response(JSON.stringify({ status: "draft", record: { id: "draft-1" } }), { status: 200 });
      if (url.includes("csrf-token")) return new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    });
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("財政年度"), { target: { value: "2027" } });
    fireEvent.change(screen.getByLabelText("EPS 基準"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "先預覽" }));
    await screen.findByText(/預覽（僅計算，不寫入）/);
    expect(screen.queryByText("明確核准")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByText(/尚未核准/);
    expect(fetchMock.mock.calls.some(([, init]) => Boolean(new Headers(init?.headers).get("Idempotency-Key")))).toBe(true);
  });
  it("requires explicit confirmation to approve a selected revision", async () => {
    vi.spyOn(globalThis, "confirm").mockReturnValue(true);
    const calls: Array<[unknown, RequestInit | undefined]> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => { calls.push([input, init]); const url = String(input); if (!init || init.method !== "POST") return new Response(JSON.stringify({ items: [{ id: "old", kind: "pe", revision_number: 1, superseded: false, approval: null }] }), { status: 200 }); if (url.endsWith("/preview")) return new Response(JSON.stringify({ status: "preview_only" }), { status: 200 }); if (url.endsWith("/draft")) return new Response(JSON.stringify({ status: "draft", record: { id: "new" } }), { status: 200 }); return new Response(JSON.stringify({ ok: true }), { status: 200 }); });
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/PE/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("類型"), { target: { value: "pe" } });
    fireEvent.change(screen.getByLabelText("版本方式"), { target: { value: "old" } });
    fireEvent.click(screen.getByRole("button", { name: "先預覽" }));
    await screen.findByText("預覽（僅計算，不寫入）"); fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByRole("button", { name: "明確核准" }); fireEvent.click(screen.getByRole("button", { name: "明確核准" }));
    await waitFor(() => expect(calls.some(([input]) => String(input).endsWith("/new/approve"))).toBe(true));
    const draftCall = calls.find(([input]) => String(input).endsWith("/pe/draft")); expect(JSON.stringify(draftCall?.[1]?.body)).toContain("previous_id");
  });
  it("sends EPS source/date and complete two-anchor payloads", async () => {
    const bodies: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => { const url = String(input); if (init?.body) bodies.push(String(init.body)); if (!init || init.method !== "POST") return new Response(JSON.stringify({ items: [] }), { status: 200 }); return new Response(JSON.stringify({ status: url.endsWith("preview") ? "preview_only" : "draft", record: { id: "d" } }), { status: 200 }); });
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />); await waitFor(() => expect(screen.getByLabelText("財政年度")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("財政年度"), { target: { value: "2027" } }); fireEvent.change(screen.getByLabelText("EPS 基準"), { target: { value: "5" } }); fireEvent.change(screen.getByLabelText("來源"), { target: { value: "財報" } }); fireEvent.change(screen.getByLabelText("來源日期"), { target: { value: "2026-08-01" } }); fireEvent.change(screen.getByLabelText("理由"), { target: { value: "人工輸入" } }); fireEvent.click(screen.getByRole("button", { name: "先預覽" })); await screen.findByText("預覽（僅計算，不寫入）"); expect(bodies[0]).toContain("source_date");
    fireEvent.change(screen.getByLabelText("類型"), { target: { value: "anchor" } }); const prices = screen.getAllByLabelText("價格", { selector: "input" }); const dates = screen.getAllByLabelText("市場日期", { selector: "input" }); fireEvent.change(prices[0]!, { target: { value: "100" } }); fireEvent.change(dates[0]!, { target: { value: "2026-01-01" } }); fireEvent.change(dates[1]!, { target: { value: "2026-02-01" } }); fireEvent.change(prices[1]!, { target: { value: "120" } }); fireEvent.change(screen.getByLabelText("來源"), { target: { value: "手動" } }); fireEvent.change(screen.getByLabelText("理由"), { target: { value: "錨點依據" } }); fireEvent.click(screen.getByRole("button", { name: "先預覽" })); await waitFor(() => expect(bodies.some(b => b.includes('"anchors"') && b.includes("origin") && b.includes("swing_end"))).toBe(true));
  });
  it("invalidates preview when values change", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => { if (!init || init.method !== "POST") return new Response(JSON.stringify({ items: [] }), { status: 200 }); return new Response(JSON.stringify({ status: "preview_only" }), { status: 200 }); });
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />); await waitFor(() => expect(screen.getByLabelText("財政年度")).toBeInTheDocument()); fireEvent.change(screen.getByLabelText("財政年度"), { target: { value: "2027" } }); fireEvent.click(screen.getByRole("button", { name: "先預覽" })); await screen.findByText("預覽（僅計算，不寫入）"); fireEvent.change(screen.getByLabelText("EPS 基準"), { target: { value: "9" } }); expect(screen.queryByRole("button", { name: "保存草稿" })).not.toBeInTheDocument();
  });
});
