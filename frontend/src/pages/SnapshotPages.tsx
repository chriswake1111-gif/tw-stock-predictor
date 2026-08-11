import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Clock3, FileArchive } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { evidenceApi } from "../api/client";
import type { CaptureMode } from "../api/types";
import { AsOfTimestamp, OriginBadge, StatusBadge } from "../components/evidence/EvidencePrimitives";
import { SectionState } from "../components/evidence/SectionState";

export function SnapshotHistoryPage() {
  const [symbol, setSymbol] = useState("");
  const [mode, setMode] = useState<"" | CaptureMode>("");
  const [before, setBefore] = useState<string | undefined>();
  const query = useQuery({ queryKey: ["snapshots", symbol, mode, before], queryFn: ({ signal }) => evidenceApi.snapshots({ symbol, captureMode: mode || undefined, before, limit: 25 }, signal) });
  return <div className="page">
    <header className="workspace-heading"><div><span className="eyebrow">Immutable evidence records</span><h1>歷史快照</h1><p className="muted">只讀取後端保存的分析輸出，不在瀏覽器重建歷史。</p></div></header>
    <div className="filter-bar"><label>股票代號<input value={symbol} onChange={(event) => { setSymbol(event.target.value.toUpperCase()); setBefore(undefined); }} placeholder="2330.TW" /></label><label>保存模式<select value={mode} onChange={(event) => { setMode(event.target.value as "" | CaptureMode); setBefore(undefined); }}><option value="">全部模式</option><option value="live_refresh">前瞻保存</option><option value="historical_reconstruction">歷史重建</option></select></label></div>
    {query.isLoading ? <div className="loading-state">讀取快照…</div> : query.isError || !query.data ? <SectionState status="insufficient_data" reason="snapshot_list_unavailable" /> : query.data.snapshots.length ? <div className="record-list">{query.data.snapshots.map((snapshot) => <Link key={snapshot.snapshot_id} to={`/snapshots/${encodeURIComponent(snapshot.snapshot_id)}`}><FileArchive aria-hidden="true" /><div><strong>{snapshot.symbol}</strong><span>{snapshot.snapshot_id}</span><small><Clock3 aria-hidden="true" size={13} /> {snapshot.created_at}</small></div><div className="record-meta"><OriginBadge origin={snapshot.capture_mode} /><StatusBadge status={snapshot.analysis_status} /></div><ChevronRight aria-hidden="true" /></Link>)}</div> : <SectionState status="insufficient_data" reason="no_stored_snapshots" />}
    {query.data?.next_before ? <button className="secondary-button" type="button" onClick={() => setBefore(query.data?.next_before ?? undefined)}>載入更早快照</button> : null}
  </div>;
}

const freshnessCopy = {
  current: { label: "依賴資料目前一致", detail: "依伺服器比較時點，快照仍使用最新可用且符合資格的資料。" },
  stale: { label: "已有較新可用資料", detail: "此快照仍是有效的歷史紀錄，但不代表目前最新分析狀態。" },
  unknown: { label: "無法確認資料新鮮度", detail: "系統採保守處理；快照仍保留，但不可視為目前最新分析。" },
  blocked: { label: "目前依賴資格已受阻", detail: "核准或依賴狀態已改變；歷史快照不會因此被改寫。" },
} as const;

function SnapshotFreshnessCard({ snapshotId }: { snapshotId: string }) {
  const query = useQuery({
    queryKey: ["snapshot-dependency-status", snapshotId],
    queryFn: ({ signal }) => evidenceApi.snapshotDependencyStatus(snapshotId, signal),
  });
  if (query.isLoading) return <section className="evidence-card freshness-card"><h2>資料新鮮度</h2><p className="muted">由伺服器檢查保存快照的資料依賴…</p></section>;
  if (query.isError || !query.data) return <section className="evidence-card freshness-card" data-freshness="unknown"><h2>資料新鮮度</h2><strong>{freshnessCopy.unknown.label}</strong><p className="muted">{freshnessCopy.unknown.detail}</p></section>;
  const freshness = query.data.dependency_status;
  const copy = freshnessCopy[freshness.freshness_status];
  return <section className="evidence-card freshness-card" data-freshness={freshness.freshness_status}><header><div><h2>資料新鮮度</h2><p className="muted">比較截止時間 {freshness.comparison_cutoff}</p></div><span className={`freshness-badge freshness-badge--${freshness.freshness_status}`}>{copy.label}</span></header><p>{copy.detail}</p>{freshness.reasons.length ? <ul className="freshness-reasons">{freshness.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : null}</section>;
}

export function SnapshotDetailPage() {
  const { snapshotId = "" } = useParams();
  const query = useQuery({ queryKey: ["snapshot", snapshotId], queryFn: ({ signal }) => evidenceApi.snapshot(snapshotId, signal) });
  if (query.isLoading) return <div className="page loading-state">讀取保存快照…</div>;
  if (query.isError || !query.data) return <div className="page"><SectionState status="insufficient_data" reason="snapshot_not_found" /></div>;
  const snapshot = query.data.snapshot;
  return <div className="page"><Link className="back-link" to="/snapshots">← 返回歷史快照</Link><header className="workspace-heading"><div><span className="eyebrow">Stored output · hash verified by server</span><div className="title-row"><h1>{snapshot.symbol}</h1><StatusBadge status={snapshot.output.status} /></div><AsOfTimestamp value={snapshot.knowledge_cutoff_at} /></div><OriginBadge origin={snapshot.capture_mode} /></header><SnapshotFreshnessCard snapshotId={snapshot.snapshot_id} /><section className="evidence-card snapshot-provenance"><h2>快照來源</h2><dl className="compact-dl"><div><dt>Snapshot ID</dt><dd>{snapshot.snapshot_id}</dd></div><div><dt>建立時間</dt><dd>{snapshot.created_at}</dd></div><div><dt>模型版本</dt><dd>{snapshot.model_version}</dd></div><div><dt>輸出 SHA-256</dt><dd>{snapshot.output_sha256}</dd></div></dl></section><section className="evidence-card"><h2>保存的分析輸出</h2><p className="muted">以下區段直接來自 immutable snapshot。</p><div className="snapshot-sections">{["valuation", "liquidity", "technical_support", "target_confluence", "deployment_plan", "screening"].map((name) => { const section = snapshot.output[name]; return <div key={name}><strong>{name}</strong><StatusBadge status={typeof section === "object" && section ? (section as { status?: unknown }).status : "unknown"} /></div>; })}</div></section></div>;
}
