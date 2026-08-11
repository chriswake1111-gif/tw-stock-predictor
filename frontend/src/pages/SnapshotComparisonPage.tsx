import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { evidenceApi } from "../api/client";
import type {
  CanonicalComparisonValue,
  SnapshotComparisonDelta,
  SnapshotComparisonResponse,
} from "../api/types";

function renderValue(value: CanonicalComparisonValue): string {
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function DeltaList({ title, deltas }: { title: string; deltas: SnapshotComparisonDelta[] }) {
  const headingId = `${deltas[0]?.category ?? "empty"}-${title.replaceAll(" ", "-")}`;
  return (
    <section className="evidence-card comparison-section" aria-labelledby={headingId}>
      <header>
        <div>
          <h2 id={headingId}>{title}</h2>
          <p className="muted">僅呈現 Comparison Policy v1 已登錄的語意差異。</p>
        </div>
        <span className="comparison-count">{deltas.length} 項</span>
      </header>
      {deltas.length === 0 ? (
        <p className="comparison-empty">沒有已登錄的語意變化。</p>
      ) : (
        <div className="comparison-deltas">
          {deltas.map((delta) => (
            <article key={`${delta.change_type}-${delta.canonical_identity}-${delta.field_path}`}>
              <header><strong>{delta.change_type}</strong><span>{delta.section}</span></header>
              <code>{delta.canonical_identity}</code>
              <dl>
                <div><dt>Before</dt><dd>{renderValue(delta.before)}</dd></div>
                <div><dt>After</dt><dd>{renderValue(delta.after)}</dd></div>
                {delta.absolute_delta !== undefined ? <div><dt>Absolute delta</dt><dd>{delta.absolute_delta}</dd></div> : null}
              </dl>
              <small>{delta.field_path}</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function ContextSummary({ result }: { result: SnapshotComparisonResponse }) {
  const contexts = [["Base", result.base_current_context], ["Comparison", result.comparison_current_context]] as const;
  return (
    <div className="comparison-context-grid">
      {contexts.map(([label, context]) => (
        <article key={label}>
          <strong>{label} context</strong>
          <span className={`freshness-badge freshness-badge--${context?.freshness_status ?? "unknown"}`}>
            {context?.freshness_status ?? "not_resolved"}
          </span>
          {context?.reasons.length ? (
            <ul>{context.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          ) : <p className="muted">沒有目前依賴警示。</p>}
        </article>
      ))}
    </div>
  );
}

export function SnapshotComparisonPage() {
  const snapshots = useQuery({
    queryKey: ["snapshots", "comparison-selectors"],
    queryFn: ({ signal }) => evidenceApi.snapshots({ limit: 100 }, signal),
  });
  const [baseId, setBaseId] = useState("");
  const [comparisonId, setComparisonId] = useState("");
  const [cutoffInput, setCutoffInput] = useState("");
  const [request, setRequest] = useState<{ base: string; comparison: string; cutoff: string } | null>(null);
  const comparison = useQuery({
    queryKey: ["snapshot-comparison", request],
    queryFn: ({ signal }) => evidenceApi.compareSnapshots(request!.base, request!.comparison, request!.cutoff, signal),
    enabled: request !== null,
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (baseId && comparisonId && cutoffInput.trim()) {
      setRequest({ base: baseId, comparison: comparisonId, cutoff: cutoffInput.trim() });
    }
  }

  const result = comparison.data;
  return (
    <div className="page snapshot-comparison-page">
      <Link className="back-link" to="/snapshots">← 返回歷史快照</Link>
      <header className="workspace-heading">
        <div>
          <span className="eyebrow">Deterministic change detection</span>
          <h1>快照比較</h1>
          <p className="muted">比較保存事實與同一截止時間下的目前依賴狀態，不重跑分析模型。</p>
        </div>
      </header>
      <form className="comparison-form" onSubmit={submit}>
        <label>
          Base snapshot
          <select aria-label="Base snapshot" required value={baseId} onChange={(event) => setBaseId(event.target.value)}>
            <option value="">請選擇</option>
            {snapshots.data?.snapshots.map((snapshot) => <option key={`base-${snapshot.snapshot_id}`} value={snapshot.snapshot_id}>{snapshot.symbol} · {snapshot.snapshot_id}</option>)}
          </select>
        </label>
        <label>
          Comparison snapshot
          <select aria-label="Comparison snapshot" required value={comparisonId} onChange={(event) => setComparisonId(event.target.value)}>
            <option value="">請選擇</option>
            {snapshots.data?.snapshots.map((snapshot) => <option key={`comparison-${snapshot.snapshot_id}`} value={snapshot.snapshot_id}>{snapshot.symbol} · {snapshot.snapshot_id}</option>)}
          </select>
        </label>
        <label>
          Comparison cutoff（含時區）
          <input aria-label="Comparison cutoff" required type="text" value={cutoffInput} onChange={(event) => setCutoffInput(event.target.value)} placeholder="2026-08-12T12:00:00+08:00" />
        </label>
        <button type="submit">執行只讀比較</button>
      </form>
      <p className="comparison-timezone-note">截止時間必須明確包含 Z 或 UTC offset；系統不使用瀏覽器本地時區推測。</p>
      <div aria-live="polite">
        {comparison.isFetching ? <div className="loading-state">讀取比較結果中…</div> : null}
        {comparison.isError ? <section className="section-state"><strong>無法取得比較結果</strong><p>請確認截止時間格式與快照識別碼。</p></section> : null}
        {result?.status === "incomparable_contract" ? (
          <section className="section-state comparison-incomparable"><strong>快照契約不可直接比較</strong><ul>{result.compatibility.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></section>
        ) : null}
        {result?.status === "available" ? (
          <div className="comparison-results">
            <section className="evidence-card comparison-summary">
              <header><div><h2>比較契約</h2><p className="muted">{result.comparison_snapshot_contract} · Policy {result.comparison_policy_version}</p></div><span>{result.comparison_cutoff}</span></header>
              <dl><div><dt>Base</dt><dd>{result.base_snapshot.snapshot_id}</dd></div><div><dt>Comparison</dt><dd>{result.comparison_snapshot.snapshot_id}</dd></div></dl>
            </section>
            <DeltaList title="Stored Snapshot Facts" deltas={result.stored_deltas} />
            <section className="evidence-card comparison-section">
              <header><div><h2>Current Dependency Context</h2><p className="muted">兩個快照都在同一 SQLite read snapshot 與相同 cutoff 下解析。</p></div></header>
              <ContextSummary result={result} />
            </section>
            <DeltaList title="Current Context Changes" deltas={result.current_context_deltas} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
