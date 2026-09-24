import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders } from "./render";
import { LocalAssumptionEditor } from "../components/LocalAssumptionEditor";

function openEditor() {
  renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "設定全年預估 EPS" }));
  for (const [label, value] of Object.entries({"財政年度":"2027", "EPS 基準":"5", "來源":"研究報告", "來源日期":"2026-08-01", "理由":"採用報告的全年預估"})) {
    fireEvent.change(screen.getByLabelText(label), {target:{value}});
  }
}

describe("LocalAssumptionEditor", () => {
  beforeEach(() => { vi.restoreAllMocks(); });
  it("allows reading first and preserves entered values when returning without writes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({items: []})));
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />);
    await screen.findByText(/目前沒有未被新版取代/);
    expect(screen.getByLabelText("財政年度")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", {name: "設定全年預估 EPS"}));
    expect(screen.getByLabelText("EPS 基準")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("財政年度"), {target: {value: "2027"}});
    fireEvent.click(screen.getByRole("button", {name: "稍後設定，先看資料"}));
    expect(screen.getByLabelText("財政年度")).not.toBeVisible();
    expect(screen.getByRole("link", {name: "查看客觀資料"})).toHaveAttribute("href", "#daily-public-data");
    fireEvent.click(screen.getByRole("button", {name: "設定全年預估 EPS"}));
    expect(screen.getByLabelText("財政年度")).toHaveValue("2027");
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });
  it("summarizes approved revisions and opens their existing values for revision", async () => {
    const base = {kind:"eps", revision_number:1, fiscal_year:2027, eps_base:5, source_name:"研究報告", published_at:"2026-08-01", approval:{decision:"approved"}};
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({items:[
      {...base,id:"active"}, {...base,id:"old",superseded:true,eps_base:4}, {...base,id:"revoked",eps_base:3,approval:{decision:"revoked"}},
    ]})));
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />);
    const summary = within(screen.getByRole("region", {name:"已保存假設摘要"}));
    await summary.findByText("2027 年預估 EPS：5 元／股");
    expect(summary.queryByText(/EPS：4|EPS：3/)).not.toBeInTheDocument();
    fireEvent.click(summary.getByRole("button", {name:"修改這份EPS假設"}));
    expect(screen.getByLabelText("版本方式")).toHaveValue("active");
    expect(screen.getByLabelText("EPS 基準")).toHaveValue("5");
    expect(screen.getByLabelText("來源")).toHaveValue("研究報告");
    fireEvent.click(screen.getByRole("button", {name:"稍後設定，先看資料"}));
    fireEvent.click(screen.getByRole("button", {name:"設定全年預估 EPS"}));
    expect(screen.getByLabelText("版本方式")).toHaveValue("active");
  });
  it("does not confuse a failed read with absence of saved assumptions", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", {status:503}));
    renderWithProviders(<LocalAssumptionEditor symbol="2330.TW" onChanged={vi.fn()} />);
    await screen.findByRole("alert");
    expect(screen.queryByText(/目前沒有未被新版取代/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name:"稍後設定，先看資料"})).toBeEnabled();
  });
  it("previews then saves a draft without auto approval", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/assumptions/2330.TW") && (!init || init.method !== "POST")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
      if (url.endsWith("/preview")) return new Response(JSON.stringify({ calculation: "preview_only" }), { status: 200 });
      if (url.endsWith("/draft")) return new Response(JSON.stringify({ status: "draft", record: { id: "draft-1" } }), { status: 200 });
      if (url.includes("csrf-token")) return new Response(JSON.stringify({ csrf_token: "csrf" }), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    });
    openEditor();
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
    openEditor();
    await waitFor(() => expect(screen.getByLabelText("類型")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("類型"), { target: { value: "pe" } });
    fireEvent.change(screen.getByLabelText("版本方式"), { target: { value: "old" } });
    fireEvent.change(screen.getByLabelText("標籤"), {target:{value:"基準"}}); fireEvent.change(screen.getByLabelText("PE 值"), {target:{value:"20"}}); fireEvent.change(screen.getByLabelText("理由"), {target:{value:"確認倍數適用"}});
    fireEvent.click(screen.getByRole("button", { name: "先預覽" }));
    await screen.findByText("預覽（僅計算，不寫入）"); fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByRole("button", { name: "明確核准" }); fireEvent.click(screen.getByRole("button", { name: "明確核准" }));
    await waitFor(() => expect(calls.some(([input]) => String(input).endsWith("/new/approve"))).toBe(true));
    const draftCall = calls.find(([input]) => String(input).endsWith("/pe/draft")); expect(JSON.stringify(draftCall?.[1]?.body)).toContain("previous_id");
  });
  it("sends EPS source/date and complete two-anchor payloads", async () => {
    const bodies: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => { const url = String(input); if (init?.body) bodies.push(String(init.body)); if (!init || init.method !== "POST") return new Response(JSON.stringify({ items: [] }), { status: 200 }); return new Response(JSON.stringify({ status: url.endsWith("preview") ? "preview_only" : "draft", record: { id: "d" } }), { status: 200 }); });
    openEditor(); await waitFor(() => expect(screen.getByLabelText("財政年度")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("財政年度"), { target: { value: "2027" } }); fireEvent.change(screen.getByLabelText("EPS 基準"), { target: { value: "5" } }); fireEvent.change(screen.getByLabelText("來源"), { target: { value: "財報" } }); fireEvent.change(screen.getByLabelText("來源日期"), { target: { value: "2026-08-01" } }); fireEvent.change(screen.getByLabelText("理由"), { target: { value: "人工輸入" } }); fireEvent.click(screen.getByRole("button", { name: "先預覽" })); await screen.findByText("預覽（僅計算，不寫入）"); expect(bodies[0]).toContain("source_date");
    fireEvent.change(screen.getByLabelText("類型"), { target: { value: "anchor" } }); const prices = screen.getAllByLabelText("價格", { selector: "input" }); const dates = screen.getAllByLabelText("市場日期", { selector: "input" }); fireEvent.change(prices[0]!, { target: { value: "100" } }); fireEvent.change(dates[0]!, { target: { value: "2026-01-01" } }); fireEvent.change(dates[1]!, { target: { value: "2026-02-01" } }); fireEvent.change(prices[1]!, { target: { value: "120" } }); fireEvent.change(screen.getByLabelText("來源"), { target: { value: "手動" } }); fireEvent.change(screen.getByLabelText("理由"), { target: { value: "錨點依據" } }); fireEvent.click(screen.getByRole("button", { name: "先預覽" })); await waitFor(() => expect(bodies.some(b => b.includes('"anchors"') && b.includes("origin") && b.includes("swing_end"))).toBe(true));
  });
  it("invalidates preview when values change", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => { if (!init || init.method !== "POST") return new Response(JSON.stringify({ items: [] }), { status: 200 }); return new Response(JSON.stringify({ status: "preview_only" }), { status: 200 }); });
    openEditor(); await waitFor(() => expect(screen.getByLabelText("財政年度")).toBeInTheDocument()); fireEvent.change(screen.getByLabelText("財政年度"), { target: { value: "2027" } }); fireEvent.click(screen.getByRole("button", { name: "先預覽" })); await screen.findByText("預覽（僅計算，不寫入）"); fireEvent.change(screen.getByLabelText("EPS 基準"), { target: { value: "9" } }); expect(screen.queryByRole("button", { name: "保存草稿" })).not.toBeInTheDocument();
  });
});
