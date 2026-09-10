import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { researchMutation, researchWorkflowApi } from "../api/researchClient";
import "./DailyResearchJournal.css";
import type { ResearchSummaryResponse } from "../api/types";

type Entry = { entry_id?: string; created_at?: string; note: string; knowledge_cutoff_at: string; summary: Partial<ResearchSummaryResponse>; status: string };
type Fact = { value: number | null; date: string | null; source: string | null; unit: string };
type Comparison = { status: string; assumptions_changed: boolean; facts: { field: string; before: Fact; after: Fact; delta: number | null; status: string }[] };
type History = { entries: Entry[]; comparison: Comparison | null };
const localTime = (value?: string) => value ? new Date(value).toLocaleString("zh-TW", {timeZone:"Asia/Taipei",hour12:false}) : "未知";
const number = (v: number | null, field: string) => v == null ? "缺值" : field === "yield_ratio" ? `${(v * 100).toLocaleString("zh-TW", {maximumFractionDigits:4})}%` : v.toLocaleString("zh-TW", {maximumFractionDigits:4});
const fieldNames: Record<string, string> = { official_close: "官方收盤價", pe: "本益比", pb: "股價淨值比", yield_ratio: "殖利率", volume: "成交股數" };
async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(path, { signal, credentials: "same-origin" });
  if (!r.ok) throw new Error("目前無法讀取保存研究，請重試。");
  const data = await r.json();
  if (!path.includes("after_symbol") && !Array.isArray(data?.entries)) throw new Error("保存紀錄格式不完整，請重試。");
  if (!data || (path.includes("after_symbol") && (!Array.isArray(data.items) || data.items.some((i: {current?: unknown}) => !i.current)))) throw new Error("研究回應格式不完整，請重試。");
  return data as T;
}
function ComparisonView({ value }: { value?: Comparison | null }) {
  if (value?.status === "unavailable") return <p role="alert">此標的目前無法讀取；其他標的仍可查閱。</p>;
  if (!value || value.status === "no_previous") return <p>保存第一次研究後，下次便可比較。</p>;
  return <div><p>{value.assumptions_changed ? "模型假設或核准狀態已變更，請重新評估情境；下方僅比較客觀資料。" : "模型假設與核准狀態未變更。"}</p><div style={{ overflowX: "auto" }}><table className="daily-journal-table"><thead><tr><th>項目</th><th>前次值／日期</th><th>目前值／日期</th><th>差異</th></tr></thead><tbody>{value.facts.map(f => <tr key={f.field}><td>{fieldNames[f.field] || f.field}</td><td>{number(f.before.value,f.field)}／{f.before.date || "未知"}</td><td>{number(f.after.value,f.field)}／{f.after.date || "未知"}</td><td>{f.status === "comparable" ? number(f.delta,f.field) + (f.field === "yield_ratio" ? "（百分點）" : "") : "資料或來源不可比較"}</td></tr>)}</tbody></table></div></div>;
}

export function ResearchJournalPanel({ symbol, cutoff }: { symbol: string; cutoff: string }) {
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const keys = useRef<Record<string, string>>({});
  const history = useQuery({ queryKey: ["research-journal", symbol], queryFn: ({ signal }) => read<History>(`/api/v2/research/journal/${encodeURIComponent(symbol)}`, signal) });
  async function save() {
    const payload = { knowledge_cutoff_at: cutoff, note };
    const identity = JSON.stringify(payload);
    keys.current[identity] ||= crypto.randomUUID();
    setBusy(true);
    try {
      await researchMutation(`/api/v2/research/journal/${encodeURIComponent(symbol)}`, payload, keys.current[identity]);
      setMessage("已保存當次資料、缺失狀態與筆記；保存不代表完整分析。");
      await history.refetch();
    } catch (e) { setMessage(e instanceof Error ? e.message : "保存失敗，請重試。"); }
    finally { setBusy(false); }
  }
  return <section className="evidence-card" aria-label="保存當次研究"><h2>保存與隔日複核</h2>
    <button type="button" disabled={busy} onClick={async () => { setBusy(true); try { await researchWorkflowApi.addSymbol(symbol); setMessage("已加入自選清單。"); } catch { setMessage("加入自選失敗，請重試。"); } finally { setBusy(false); } }}>加入自選</button>
    <label style={{ display: "block", marginTop: 12 }}>研究筆記<textarea maxLength={4000} rows={3} value={note} onChange={e => setNote(e.target.value)} placeholder="記下本次觀察、假設或明天要確認的事項" style={{ display: "block", width: "100%" }} /></label>
    <button type="button" disabled={busy || !cutoff} onClick={() => void save()}>保存當次研究與筆記</button> <Link to="/research/daily">查看每日複核</Link>
    {message && <p role="status">{message}</p>}{history.isError && <p role="alert">保存紀錄讀取失敗。<button onClick={() => void history.refetch()}>重試</button></p>}
    <ComparisonView value={history.data?.comparison} />
    <details><summary>先前保存紀錄（{history.data?.entries.length || 0}）</summary>{history.data?.entries.map(e => <article key={e.entry_id}><h3>{e.summary.market_context?.settled_trade_date || "行情日期尚缺"}</h3><p>保存時間：{localTime(e.created_at)}；保留部分研究與缺失狀態</p><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{e.note || "未填筆記"}</p><p>收盤價：{e.summary.market_context?.official_close ?? "缺值"}；估值：{e.summary.valuation_context?.status === "available" ? "已計算" : "待確認"}</p></article>)}</details>
  </section>;
}

export function DailyJournalOverview() {
  const [cursor, setCursor] = useState("");
  const query = useQuery({ queryKey: ["daily-journal-overview", cursor], queryFn: ({ signal }) => read<{ server_time: string; next_symbol: string | null; items: { symbol: string; current: Entry; previous: Entry | null; comparison: Comparison }[] }>(`/api/v2/research/journal?after_symbol=${encodeURIComponent(cursor)}`, signal) });
  return <section aria-label="每日研究複核"><h1>每日研究</h1><p>以伺服器目前時間檢視本機資料，並與各標的前次保存內容比較。</p><button onClick={() => void query.refetch()}>重新檢查本機資料</button>
    {query.isLoading && <p role="status">正在讀取自選與保存研究…</p>}{query.isError && <p role="alert">每日研究暫時無法讀取，請重試。</p>}
    {query.data && <p>本次檢視時間：{localTime(query.data.server_time)}</p>}{query.data?.items.length === 0 && <p>自選清單目前沒有標的。<Link to="/">搜尋股票開始研究</Link></p>}
    {query.data?.items.map(item => <article className="evidence-card" key={item.symbol}><h2><Link to={`/stocks/${item.symbol}`}>{item.symbol} {item.current.summary.short_name || item.current.summary.company_name}</Link></h2>
      <p>本機行情日期：{item.current.summary.market_context?.settled_trade_date || "尚無行情"}；收盤價：{item.current.summary.market_context?.official_close ?? "缺值"}</p>
      <p>待確認事項：{item.current.summary.human_decision_queue?.map(x => x.title).join("、") || "請依各模組資料狀態判讀"}</p>
      <ComparisonView value={item.comparison} />{item.previous && <p style={{ whiteSpace: "pre-wrap" }}>前次筆記：{item.previous.note || "未填筆記"}</p>}
    </article>)}
    {query.data?.next_symbol && <button onClick={() => setCursor(query.data!.next_symbol!)}>下一頁自選</button>}{cursor && <button onClick={() => setCursor("")}>回到第一頁</button>}
  </section>;
}
