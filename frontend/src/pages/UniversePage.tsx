import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { evidenceApi } from "../api/client";
import type { UniverseResponse } from "../api/types";

const reasonText: Record<string, string> = {
  instrument_not_found: "找不到可安全引用的標的主檔。",
  source_revision_awaiting_review: "來源版本等待人工確認。",
  manual_publication_evidence_required: "此來源需要人工發布證據。",
  source_schema_review_required: "來源格式需要人工審查。",
  source_revision_revoked_without_corrected_revision: "最新來源版本已撤銷，尚無更正版本。",
  canonical_mapping_unverified: "代號與交易所映射尚未核准。",
  freshness_unknown: "來源沒有可證明的更新週期。",
  current_freshness_blocked: "目前完整性被資料狀態阻擋。",
};

function Result({ value }: { value: UniverseResponse }) {
  const identity = value.identity_reference;
  return <section className="evidence-card" aria-live="polite">
    <header><h2>標的主檔狹義參考</h2><span className={`freshness-badge freshness-badge--${value.status}`}>{value.status}</span></header>
    <p>{value.reasons.length ? value.reasons.map((reason) => reasonText[reason] ?? reason).join(" ") : "目前可安全讀取此身份參考。"}</p>
    <dl>
      <div><dt>代號</dt><dd>{identity?.official_code ?? "—"}</dd></div>
      <div><dt>交易所／市場</dt><dd>{identity?.venue ?? "—"}</dd></div>
      <div><dt>標準代號</dt><dd>{identity?.canonical_symbol ?? "尚未核准"}</dd></div>
      <div><dt>上市狀態</dt><dd>{identity?.listing_status ?? "unknown"}</dd></div>
      <div><dt>交易狀態</dt><dd>{identity?.trading_state ?? "unknown"}</dd></div>
      <div><dt>成員／完整性狀態</dt><dd>{identity?.membership_state ?? "unknown"}</dd></div>
      <div><dt>資料新鮮度</dt><dd>{value.operational_freshness.freshness}</dd></div>
      <div><dt>知識截止時間</dt><dd>{value.knowledge_cutoff_at}</dd></div>
    </dl>
    <p className="muted">此頁只描述身份與資料狀態，不提供價格、排名或投資建議。</p>
  </section>;
}

export function UniversePage() {
  const [cutoff, setCutoff] = useState("");
  const [cutoffInput, setCutoffInput] = useState("");
  const [symbol, setSymbol] = useState("2330.TW");
  const [submitted, setSubmitted] = useState("");
  const query = useQuery({
    queryKey: ["universe", submitted, cutoff],
    queryFn: ({ signal }) => evidenceApi.universeInstrument(submitted, cutoff, signal),
    enabled: Boolean(submitted && cutoff),
  });
  function submit(event: FormEvent) {
    event.preventDefault();
    if (cutoffInput.trim() && symbol.trim()) { setCutoff(cutoffInput.trim()); setSubmitted(symbol.trim().toUpperCase()); }
  }
  return <div className="page">
    <header className="workspace-heading"><div><span className="eyebrow">Universe foundation</span><h1>標的主檔與市場狀態</h1><p className="muted">以明確的含時區截止時間查詢身份、交易狀態與資料新鮮度。</p></div></header>
    <form className="research-controls" onSubmit={submit}>
      <label>標準代號或代號.交易所<input value={symbol} onChange={(event) => setSymbol(event.target.value)} aria-label="標準代號" required /></label>
      <label>知識截止時間（含時區）<input value={cutoffInput} onChange={(event) => setCutoffInput(event.target.value)} placeholder="2026-08-21T08:00:00+08:00" required /></label>
      <button type="submit">查詢主檔</button>
    </form>
    {query.isLoading ? <p role="status">正在讀取主檔狀態…</p> : null}
    {query.isError ? <p role="alert">目前無法安全讀取主檔，請確認截止時間與本機服務。</p> : null}
    {query.data ? <Result value={query.data} /> : null}
  </div>;
}
