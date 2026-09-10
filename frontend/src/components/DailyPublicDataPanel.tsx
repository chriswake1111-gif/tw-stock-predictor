import { useMemo, useState } from "react";

type PriceRow = { date?: string; open?: number | null; high?: number | null; low?: number | null; close?: number | null; volume?: number | null; value?: number | null; change?: number | null; zero_volume?: boolean };
type PerRow = { date?: string; pe?: number | null; pb?: number | null; yield_ratio?: number | null };
type EpsRow = { period_end?: string; quarterly_eps?: number | null; available_at?: string | null };
type Dataset = { status?: string; source?: string; official_exchange_source?: boolean; observed_at?: string | null; last_checked_at?: string | null; last_update_status?: string | null; last_update_reason?: string | null; rows?: PriceRow[] | PerRow[] | EpsRow[] };
export type DailyPublicData = Record<"TaiwanStockPrice" | "TaiwanStockPER" | "TaiwanStockFinancialStatements", Dataset>;

interface Props { data: DailyPublicData | Record<string, Dataset>; onSelectPrice?: (date: string, price: number) => void }
const fmt = (v: unknown, suffix = "") => v === null || v === undefined || v === "" ? "缺值" : `${v}${suffix}`;
const taipeiDate = (value?: string | null) => value ? value.slice(0, 10) : "";
const displayNumber = (value: unknown) => typeof value === "number" ? value.toLocaleString("zh-TW", { maximumFractionDigits: 4 }) : fmt(value);

export function DailyPublicDataPanel({ data, onSelectPrice }: Props) {
  const [range, setRange] = useState<"3m" | "1y">("1y");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const prices = useMemo(() => (data.TaiwanStockPrice?.rows || []) as PriceRow[], [data.TaiwanStockPrice?.rows]);
  const pers = (data.TaiwanStockPER?.rows || []) as PerRow[];
  const eps = (data.TaiwanStockFinancialStatements?.rows || []) as EpsRow[];
  const visiblePrices = useMemo(() => {
    const anchor = new Date(taipeiDate(data.TaiwanStockPrice?.observed_at) || taipeiDate(new Date().toISOString()));
    anchor.setMonth(anchor.getMonth() - (range === "3m" ? 3 : 12));
    return prices.filter(r => !r.date || Number.isNaN(Date.parse(r.date)) || new Date(r.date) >= anchor);
  }, [prices, range, data.TaiwanStockPrice?.observed_at]);
  const selectable = visiblePrices.filter(r => r.date && typeof r.close === "number" && !r.zero_volume && Number(r.volume || 0) > 0);
  const chartRows = visiblePrices.filter(r => typeof r.close === "number");
  const maxVolume = Math.max(1, ...chartRows.map(r => Number(r.volume || 0)));
  const statusLabel = (value?: string) => ({ available: "可用", insufficient_data: "資料不足", success: "成功", partial: "部分完成", failed: "失敗", not_started: "尚未開始", unavailable: "不可用" }[value || ""] || value || "缺值");
  const chartDates = chartRows.map(r => Date.parse(r.date || ""));
  const firstDate = Math.min(...chartDates.filter(Number.isFinite));
  const lastDate = Math.max(...chartDates.filter(Number.isFinite));
  const priceMin = chartRows.length ? Math.min(...chartRows.map(r => Number(r.close))) : 0;
  const priceMax = chartRows.length ? Math.max(...chartRows.map(r => Number(r.close))) : 1;
  const xFor = (r: PriceRow) => { const t = Date.parse(r.date || ""); return Number.isFinite(t) && lastDate > firstDate ? ((t - firstDate) / (lastDate - firstDate)) * 260 + 30 : 30; };
  const points = chartRows.length ? chartRows.map(r => `${xFor(r)},${20 + (1 - (Number(r.close) - priceMin) / Math.max(1, priceMax - priceMin)) * 55}`).join(" ") : "";
  const section = (key: keyof DailyPublicData, title: string, children: React.ReactNode) => {
    const d = data[key];
    const rows = d?.rows || [];
    const dataDate = rows.length ? ((rows.at(-1) as Record<string, unknown>).date || (rows.at(-1) as Record<string, unknown>).period_end) : null;
    return <details open style={{ marginBottom: "1rem", border: "1px solid #e2e8f0", borderRadius: 8, padding: "0.75rem" }}><summary style={{ cursor: "pointer", fontWeight: 700 }}>{title}</summary><div style={{ marginTop: "0.65rem" }}><div style={{ fontSize: "0.82rem", color: "#475569" }}>狀態：{statusLabel(d?.status)} 來源：{d?.source || "FinMind"} 官方交易所來源：{d?.official_exchange_source ? "是" : "否"}</div><div style={{ fontSize: "0.82rem", color: "#64748b", marginTop: 4 }}>資料日期：{fmt(dataDate)}；本機取得時間：{fmt(d?.observed_at)}；最近檢查時間：{fmt(d?.last_checked_at)}</div>{d?.last_update_status && !["available", "success", "not_started"].includes(d.last_update_status) && <div role="alert" style={{ color: "#b91c1c", marginTop: 4 }}>更新狀態：{statusLabel(d.last_update_status)}；{d.last_update_reason || "原因未提供"}。以下保留既有資料。</div>}{children}</div></details>;
  };
  return <section aria-label="每日公開資料" style={{ width: "100%", minWidth: 0 }}>
    <h2 style={{ marginBottom: 6 }}>每日公開資料</h2>
    <p style={{ marginTop: 0, color: "#92400e", fontSize: "0.85rem" }}>FinMind 非官方資料；未還原權息，交易日完整性未稽核，不能視為官方 K 線。</p>
    {section("TaiwanStockPrice", "行情（近一年）", <>
      <div style={{ display: "flex", gap: 6, margin: "0.7rem 0" }}><button type="button" aria-pressed={range === "1y"} onClick={() => setRange("1y")}>近一年</button><button type="button" aria-pressed={range === "3m"} onClick={() => setRange("3m")}>近三個月</button></div>
      {chartRows.length > 0 && <svg viewBox="0 0 300 145" role="img" aria-label="收盤價折線與成交量柱" style={{ width: "100%", maxWidth: 600, height: 190 }}><line x1="30" y1="80" x2="290" y2="80" stroke="#cbd5e1" /><line x1="30" y1="115" x2="290" y2="115" stroke="#cbd5e1" /><polyline fill="none" stroke="#0284c7" strokeWidth="2" points={points} />{chartRows.map((r, i) => <g key={`${r.date}-${i}`}><rect x={xFor(r) - 1} y={115 - (Number(r.volume || 0) / maxVolume) * 25} width="2" height={(Number(r.volume || 0) / maxVolume) * 25} fill={r.zero_volume ? "#cbd5e1" : "#94a3b8"} />{onSelectPrice && r.date && typeof r.close === "number" && !r.zero_volume && Number(r.volume || 0) > 0 && <circle cx={xFor(r)} cy={20 + (1 - (Number(r.close) - priceMin) / Math.max(1, priceMax - priceMin)) * 55} r={selectedDate === r.date ? 4 : 3} fill={selectedDate === r.date ? "#b45309" : "#0284c7"} role="button" tabIndex={0} aria-label={`${r.date} 收盤價 ${displayNumber(r.close)} 元`} onClick={() => { setSelectedDate(r.date!); onSelectPrice(r.date!, r.close!); }} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedDate(r.date!); onSelectPrice(r.date!, r.close!); } }} />}</g>)}<text x="2" y="25" fontSize="7">{displayNumber(priceMax)}</text><text x="2" y="78" fontSize="7">{displayNumber(priceMin)}</text><text x="30" y="140" fontSize="7">{taipeiDate(chartRows[0]?.date) || "日期缺值"}</text><text x="235" y="140" fontSize="7">{taipeiDate(chartRows.at(-1)?.date) || "日期缺值"}</text></svg>}
      <p style={{ color: "#92400e", fontSize: "0.82rem" }}>圖表按實際日期距離呈現；缺口與交易日完整性尚未稽核。</p>
      <div style={{ fontSize: "0.9rem", marginBottom: 6 }}>最新收盤：{displayNumber(visiblePrices.at(-1)?.close)} 元，漲跌：{displayNumber(visiblePrices.at(-1)?.change)} 元，成交量：{displayNumber(visiblePrices.at(-1)?.volume)} 股</div>
      {onSelectPrice && <><label htmlFor="daily-price-point">選擇有成交日期：</label><select id="daily-price-point" value={selectedDate || ""} onChange={e => { const row = selectable.find(r => r.date === e.target.value); if (row?.date && typeof row.close === "number") { setSelectedDate(row.date); onSelectPrice(row.date, row.close); } }}><option value="">請選擇</option>{selectable.map(r => <option key={r.date} value={r.date}>{r.date}｜{displayNumber(r.close)}</option>)}</select></>}
      {prices.length === 0 && <p>目前沒有行情資料可顯示。</p>}
    </>)}
    {section("TaiwanStockPER", "本益比／股價淨值比／殖利率", <div style={{ overflowX: "auto" }}><table><thead><tr><th>日期</th><th>PE</th><th>PB</th><th>殖利率</th></tr></thead><tbody>{pers.slice(-1).map((r, i) => <tr key={`${r.date}-${i}`}><td>{fmt(r.date)}</td><td>{displayNumber(r.pe)}</td><td>{displayNumber(r.pb)}</td><td>{r.yield_ratio == null ? "缺值" : `${displayNumber(Number(r.yield_ratio) * 100)}%`}</td></tr>)}</tbody></table>{pers.length > 1 && <details><summary>查看歷史估值（{pers.length - 1} 筆）</summary><ul>{pers.slice(0, -1).map((r, i) => <li key={`${r.date}-${i}`}>{fmt(r.date)}：PE {displayNumber(r.pe)}、PB {displayNumber(r.pb)}、殖利率 {r.yield_ratio == null ? "缺值" : `${displayNumber(Number(r.yield_ratio) * 100)}%`}</li>)}</ul></details>}{pers.length === 0 && <p>目前沒有估值資料可顯示。</p>}</div>)}
    {section("TaiwanStockFinancialStatements", "EPS（逐季）", <div><p style={{ color: "#92400e" }}>股數基準尚未核對，TTM暫不計算</p>{eps.length === 0 ? <p>目前沒有 EPS 資料可顯示。</p> : <ul>{eps.map((r, i) => <li key={`${r.period_end}-${i}`}>{fmt(r.period_end)}：{fmt(r.quarterly_eps)}；本機首次可用時間：{fmt(r.available_at)}</li>)}</ul>}</div>)}
  </section>;
}
