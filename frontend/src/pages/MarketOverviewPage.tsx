import { useQuery } from "@tanstack/react-query";
import { Activity, Landmark, Scale } from "lucide-react";
import { evidenceApi } from "../api/client";
import { AsOfTimestamp, StatusBadge } from "../components/evidence/EvidencePrimitives";
import { SectionState } from "../components/evidence/SectionState";
import { formatPrice } from "../lib/records";

export function MarketOverviewPage() {
  const query = useQuery({ queryKey: ["market-overview"], queryFn: ({ signal }) => evidenceApi.marketOverview(undefined, signal) });
  if (query.isLoading) return <div className="page loading-state">讀取市場資料…</div>;
  if (query.isError || !query.data) return <div className="page"><SectionState status="insufficient_data" reason="market_read_api_unavailable" /></div>;
  const { market_overview: market, knowledge_cutoff_at: cutoff } = query.data;
  return <div className="page">
    <header className="workspace-heading"><div><span className="eyebrow">Market liquidity</span><div className="title-row"><h1>市場概況</h1><StatusBadge status={market.status} reason={market.reason} /></div><AsOfTimestamp value={cutoff} /></div></header>
    <section className="market-metrics">
      <article><Activity aria-hidden="true" /><span>市場成交金額</span><strong>{formatPrice(market.turnover_twd.total)}</strong><small>TWD · TWSE + TPEx</small></article>
      <article><Landmark aria-hidden="true" /><span>M1B</span><strong>{formatPrice(market.m1b_twd.value)}</strong><small>TWD · {market.m1b_twd.period ?? "期間未提供"}</small></article>
      <article><Scale aria-hidden="true" /><span>成交金額 / M1B</span><strong>{market.turnover_m1b_ratio_pct ?? "—"}</strong><small>% · 後端權威計算</small></article>
    </section>
    <div className="workspace-grid">
      <section className="evidence-card"><header><div><span className="eyebrow">Rolling context</span><h2>歷史相對位置</h2></div></header><dl className="metric-dl"><div><dt>20 日均值</dt><dd>{market.rolling_mean_20d_pct ?? "—"}%</dd></div><div><dt>60 日均值</dt><dd>{market.rolling_mean_60d_pct ?? "—"}%</dd></div><div><dt>5 年百分位</dt><dd>{market.historical_percentile_5y ?? "—"}</dd></div><div><dt>10 年百分位</dt><dd>{market.historical_percentile_10y ?? "—"}</dd></div></dl></section>
      <section className="evidence-card"><header><div><span className="eyebrow">Disclosure</span><h2>公開案例參考</h2></div></header><p className="range-callout">{market.reference_case.range_pct.join(" – ")}%</p><strong>{market.reference_case.label}</strong><p className="muted">{market.reference_case.meaning}</p><small className="muted">這是參考案例，不是訊號或機率預測。</small></section>
    </div>
  </div>;
}
