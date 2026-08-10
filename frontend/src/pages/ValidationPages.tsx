import { useQuery } from "@tanstack/react-query";
import { BarChart3, ChevronRight } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { evidenceApi } from "../api/client";
import { NumeratorDenominator, OriginBadge } from "../components/evidence/EvidencePrimitives";
import { SectionState } from "../components/evidence/SectionState";

export function ValidationHistoryPage() {
  const query = useQuery({ queryKey: ["evaluation-runs"], queryFn: ({ signal }) => evidenceApi.evaluationRuns(undefined, 50, signal) });
  return <div className="page"><header className="workspace-heading"><div><span className="eyebrow">Historical descriptive evidence</span><h1>歷史觀察</h1><p className="muted">呈現固定 Run 的描述性結果；不產生選股排行或未來機率。</p></div></header>{query.isLoading ? <div className="loading-state">讀取驗證 Run…</div> : query.isError || !query.data ? <SectionState status="insufficient_data" reason="evaluation_runs_unavailable" /> : query.data.evaluation_runs.length ? <div className="record-list">{query.data.evaluation_runs.map((run) => <Link key={run.evaluation_run_id} to={`/validation/runs/${encodeURIComponent(run.evaluation_run_id)}`}><BarChart3 aria-hidden="true" /><div><strong>{run.evaluator_version}</strong><span>{run.evaluation_run_id}</span><small>{run.created_at}</small></div><div className="record-meta"><span>{run.universe_definition}</span></div><ChevronRight aria-hidden="true" /></Link>)}</div> : <SectionState status="insufficient_data" reason="no_completed_evaluation_runs" />}</div>;
}

export function ValidationRunPage() {
  const { runId = "" } = useParams();
  const runQuery = useQuery({ queryKey: ["evaluation-run", runId], queryFn: ({ signal }) => evidenceApi.evaluationRun(runId, signal) });
  const summaryQuery = useQuery({ queryKey: ["performance-summary", runId], queryFn: ({ signal }) => evidenceApi.performanceSummary(runId, signal) });
  if (runQuery.isLoading || summaryQuery.isLoading) return <div className="page loading-state">讀取歷史觀察…</div>;
  if (runQuery.isError || summaryQuery.isError || !runQuery.data || !summaryQuery.data) return <div className="page"><SectionState status="insufficient_data" reason="evaluation_run_not_found" /></div>;
  const run = runQuery.data.evaluation_run;
  const summary = summaryQuery.data.performance_summary;
  return <div className="page"><Link className="back-link" to="/validation">← 返回歷史觀察</Link><header className="workspace-heading"><div><span className="eyebrow">Evaluation run</span><h1>{run.evaluator_version}</h1><p className="muted">{run.evaluation_run_id}</p></div></header><section className="origin-disclosure"><OriginBadge origin="prospective_snapshot" /><span>前瞻保存與歷史重建在每個群組中分開揭露。</span></section><div className="performance-grid">{summary.groups.map((group, index) => <article key={`${group.evaluation_origin}-${group.method_family}-${group.horizon_sessions}-${index}`}><header><OriginBadge origin={group.evaluation_origin} /><span>{group.horizon_sessions} sessions</span></header><h2>{group.method_family}</h2><div className="sample-size"><span>有效樣本 n={group.n}</span><NumeratorDenominator numerator={group.numerator} denominator={group.denominator} /></div><dl className="metric-dl"><div><dt>歷史條件下目標區間觸及觀察</dt><dd>{group.historical_target_reach_rate ?? "—"}</dd></div><div><dt>Median return</dt><dd>{group.median_forward_return ?? "—"}</dd></div><div><dt>Median excess</dt><dd>{group.median_excess_return ?? "—"}</dd></div></dl></article>)}</div><section className="disclosure-panel"><BarChart3 aria-hidden="true" /><div><strong>限制</strong>{summary.disclosures.map((item) => <p key={item}>{item}</p>)}</div></section></div>;
}
