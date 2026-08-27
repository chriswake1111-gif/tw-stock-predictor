import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { evidenceApi } from "../api/client";
import type { EodCloseContextResponse } from "../api/types";

const statusLabel: Record<string, string> = {
  available: "官方資料可用",
  insufficient_data: "資料不足",
  partial: "來源部分資料",
  unknown: "狀態未知",
  blocked: "資料已阻擋",
  needs_human_input: "待人工確認",
  not_applicable: "情境不適用",
};

function ContextResult({ value }: { value: EodCloseContextResponse }) {
  const close = value.status === "available" ? value.close_value : null;
  return (
    <section className="evidence-card eod-context-card" aria-label="官方日收盤情境結果" aria-live="polite">
      <header>
        <div>
          <span className="eyebrow">Official EOD context</span>
          <h2>{value.canonical_symbol ?? "標的身份尚未確認"}</h2>
        </div>
        <span className={`freshness-badge freshness-badge--${value.freshness_state}`}>
          {statusLabel[value.status] ?? value.status}
        </span>
      </header>
      <p className="eod-context-disclaimer">
        官方未調整日收盤情境；不代表目標價、買賣或推薦
      </p>
      <dl className="metric-dl">
        <div><dt>官方代號／市場</dt><dd>{value.official_code ?? "—"}／{value.venue ?? "—"}</dd></div>
        <div><dt>官方未調整收盤</dt><dd>{close == null ? "—" : `${close} ${value.currency ?? ""}`}</dd></div>
        <div><dt>交易日</dt><dd>{value.selected_trade_date ?? value.source_trade_date ?? "—"}</dd></div>
        <div><dt>資料新鮮度</dt><dd>{value.freshness_state}</dd></div>
        <div><dt>資料狀態</dt><dd>{value.reason_codes.length ? value.reason_codes.join("、") : "—"}</dd></div>
        <div><dt>評估時間</dt><dd>{value.evaluated_at}</dd></div>
      </dl>
      {value.quality_flags.length ? (
        <p className="muted">品質標記：{value.quality_flags.join("、")}</p>
      ) : null}
    </section>
  );
}

export function EodCloseContextPage() {
  const [symbolInput, setSymbolInput] = useState("2330.TW");
  const [cutoffInput, setCutoffInput] = useState("");
  const [symbol, setSymbol] = useState("");
  const [cutoff, setCutoff] = useState("");
  const [mode, setMode] = useState<"current" | "as_of">("current");
  const query = useQuery({
    queryKey: ["eod-close-context", mode, symbol, cutoff],
    queryFn: ({ signal }) => mode === "current"
      ? evidenceApi.eodCloseCurrent(symbol, signal)
      : evidenceApi.eodCloseAsOf(symbol, cutoff, signal),
    enabled: Boolean(symbol && (mode === "current" || cutoff)),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const nextSymbol = symbolInput.trim().toUpperCase();
    if (!nextSymbol) return;
    setSymbol(nextSymbol);
    setCutoff(mode === "as_of" ? cutoffInput.trim() : "");
  }

  return (
    <div className="page">
      <header className="workspace-heading">
        <div>
          <span className="eyebrow">Phase 14 / read-only</span>
          <h1>官方日收盤情境</h1>
          <p className="muted">僅呈現官方未調整收盤與資料證據狀態，不連結分析、訊號或交易。</p>
        </div>
      </header>
      <form className="research-controls eod-context-form" onSubmit={submit}>
        <label>標準代號<input value={symbolInput} onChange={(event) => setSymbolInput(event.target.value)} required /></label>
        <label>查詢模式
          <select value={mode} onChange={(event) => setMode(event.target.value as "current" | "as_of")}>
            <option value="current">目前狀態</option>
            <option value="as_of">知識截止時間</option>
          </select>
        </label>
        {mode === "as_of" ? (
          <label>知識截止時間（含時區）<input value={cutoffInput} onChange={(event) => setCutoffInput(event.target.value)} placeholder="2026-08-27T16:00:00+08:00" required /></label>
        ) : null}
        <button type="submit">讀取收盤情境</button>
      </form>
      {query.isLoading ? <p role="status">正在讀取官方收盤證據…</p> : null}
      {query.isError ? <p role="alert">目前無法安全讀取收盤證據，請確認代號、時間與本機服務。</p> : null}
      {query.data ? <ContextResult value={query.data} /> : null}
    </div>
  );
}
