import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, Database, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { evidenceApi } from "../api/client";
import type { AnalysisSection, RuleTrace } from "../api/types";
import { EvidenceDrawer } from "../components/evidence/EvidenceDrawer";
import { AsOfTimestamp, EvidenceLevelBadge, MethodStrengthIndicator, StatusBadge } from "../components/evidence/EvidencePrimitives";
import { SectionState } from "../components/evidence/SectionState";
import { formatPrice, numberOf, recordOf, recordsOf, textOf } from "../lib/records";

function EvidenceButton({ section, onOpen }: { section: AnalysisSection; onOpen: () => void }) {
  const count = section.rules_used?.length ?? 0;
  return <button className="evidence-link" type="button" onClick={onOpen}><ShieldCheck aria-hidden="true" size={16} />{count ? `${count} 條規則證據` : "查看資料狀態"}<ChevronRight aria-hidden="true" size={15} /></button>;
}

function RuleList({ rules }: { rules: RuleTrace[] | undefined }) {
  if (!rules?.length) return <p className="muted">此區段沒有可列示的已用規則。</p>;
  return <div className="drawer-rule-list">{rules.map((rule, index) => (
    <article key={`${rule.rule_id}-${rule.approval_id ?? index}`}>
      <div className="rule-heading"><strong>{rule.rule_id}</strong>{rule.evidence_level ? <EvidenceLevelBadge level={rule.evidence_level} /> : null}</div>
      <dl className="compact-dl">
        <div><dt>版本</dt><dd>{rule.rule_version ?? rule.version ?? "—"}</dd></div>
        <div><dt>實作模式</dt><dd>{rule.implementation_mode ?? "—"}</dd></div>
        <div><dt>核准 ID</dt><dd>{rule.approval_id ?? "不適用"}</dd></div>
      </dl>
    </article>
  ))}</div>;
}

export function StockWorkspacePage() {
  const { symbol = "2330.TW" } = useParams();
  const [drawer, setDrawer] = useState<{ title: string; section: AnalysisSection } | null>(null);
  const query = useQuery({ queryKey: ["analysis", symbol], queryFn: ({ signal }) => evidenceApi.analysis(symbol, undefined, signal) });

  if (query.isLoading) return <div className="page loading-state">讀取 Evidence Workspace…</div>;
  if (query.isError || !query.data) return <div className="page"><SectionState status="insufficient_data" reason="read_api_unavailable" /></div>;

  const analysis = query.data;
  const valuationCells = recordsOf(analysis.valuation.target_matrix);
  const clusters = recordsOf(analysis.target_confluence.overlap_ranges);
  const scenarios = recordsOf(analysis.technical_support.scenarios);
  const plans = recordsOf(analysis.deployment_plan.plans);
  const screeningComponents = recordOf(analysis.screening.components);
  const primaryCluster = clusters[0] ?? {};
  const independentMethods = numberOf(analysis.target_confluence.independent_method_count) ?? 0;
  const openEvidence = (title: string, section: AnalysisSection) => setDrawer({ title, section });

  return <div className="page stock-workspace">
    <header className="workspace-heading">
      <div><span className="eyebrow">Evidence workspace / individual security</span><div className="title-row"><h1>{analysis.symbol}</h1><StatusBadge status={analysis.status} /></div><AsOfTimestamp value={analysis.knowledge_cutoff_at} /></div>
      <div className="model-disclosure"><Database aria-hidden="true" size={18} /><span>{analysis.model.name} · {analysis.model.version}</span><small>非官方關係 · 不構成投資建議</small></div>
    </header>

    <section className="decision-strip" aria-label="決策支援摘要">
      <article><span>整體資料狀態</span><StatusBadge status={analysis.data_quality.status} /><small>{analysis.data_quality.available_sections.length} 個可用區段</small></article>
      <article><span>估值情境</span><strong>{valuationCells.length}</strong><small>直接呈現後端核准矩陣</small></article>
      <article><span>目標交集區</span><strong>{clusters.length}</strong><small>{textOf(analysis.target_confluence.summary_policy, "尚無摘要政策")}</small></article>
      <article><span>待人工處理</span><strong>{analysis.data_quality.needs_human_input.length}</strong><small>{analysis.data_quality.needs_human_input.join("、") || "目前無"}</small></article>
    </section>

    <div className="workspace-grid">
      <section className="evidence-card evidence-card--wide">
        <header><div><span className="eyebrow">Valuation</span><h2>Forward EPS × PE 情境矩陣</h2></div><StatusBadge status={analysis.valuation.status} reason={analysis.valuation.reason} /></header>
        {valuationCells.length ? <div className="scenario-table-wrap" tabIndex={0} role="region" aria-label="估值情境矩陣，可水平捲動"><table className="scenario-table"><thead><tr><th>年度</th><th>來源</th><th>EPS 情境</th><th>PE</th><th>目標價</th></tr></thead><tbody>{valuationCells.slice(0, 8).map((cell, index) => <tr key={textOf(cell.cell_id, String(index))}><td>{textOf(cell.fiscal_year)}</td><td>{textOf(cell.source_name)}</td><td>{textOf(cell.eps_scenario ?? cell.eps_case)}</td><td>{textOf(cell.pe_multiple)}</td><td className="numeric">{formatPrice(cell.target_price)}</td></tr>)}</tbody></table></div> : <SectionState status={analysis.valuation.status} reason={analysis.valuation.reason} />}
        <EvidenceButton section={analysis.valuation} onOpen={() => openEvidence("估值證據", analysis.valuation)} />
      </section>

      <section className="evidence-card evidence-card--focus">
        <header><div><span className="eyebrow">Target confluence</span><h2>多方法目標交集</h2></div><StatusBadge status={analysis.target_confluence.status} reason={analysis.target_confluence.reason} /></header>
        {clusters.length ? <div className="target-range"><span>交集區 1</span><strong>{formatPrice(primaryCluster.price_low)} <ArrowRight aria-hidden="true" /> {formatPrice(primaryCluster.price_high)}</strong><small>TWD / share</small><MethodStrengthIndicator strength={textOf(primaryCluster.evidence_strength, "未分級")} independentMethods={independentMethods} /></div> : <SectionState status={analysis.target_confluence.status} reason={analysis.target_confluence.reason} />}
        <EvidenceButton section={analysis.target_confluence} onOpen={() => openEvidence("目標交集證據", analysis.target_confluence)} />
      </section>

      <section className="evidence-card">
        <header><div><span className="eyebrow">Technical support</span><h2>人工錨點技術情境</h2></div><StatusBadge status={analysis.technical_support.status} /></header>
        {scenarios.length ? <div className="stack-list">{scenarios.map((item, index) => <article key={textOf(item.anchor_set_revision_id, String(index))}><strong>{textOf(recordOf(item.rule_trace).rule_id, "技術情境")}</strong><span>{textOf(item.method)} · {formatPrice(item.target_price ?? item.price)} TWD</span><small>支撐情境參考，不是目標或保證價格</small></article>)}</div> : <SectionState status={analysis.technical_support.status} reason={analysis.technical_support.reason} />}
        <EvidenceButton section={analysis.technical_support} onOpen={() => openEvidence("技術支撐證據", analysis.technical_support)} />
      </section>

      <section className="evidence-card">
        <header><div><span className="eyebrow">Screening</span><h2>二低一高研究篩選</h2></div><StatusBadge status={analysis.screening.status} /></header>
        {Object.keys(screeningComponents).length ? <div className="metric-list">{Object.entries(screeningComponents).map(([name, value]) => { const component = recordOf(value); return <div key={name}><span>{name}</span><StatusBadge status={component.status} reason={textOf(component.reason, "")} /><strong>{textOf(component.percentile)}</strong></div>; })}</div> : <SectionState status={analysis.screening.status} reason={analysis.screening.reason} />}
        <EvidenceButton section={analysis.screening} onOpen={() => openEvidence("篩選證據", analysis.screening)} />
      </section>

      <section className="evidence-card evidence-card--wide">
        <header><div><span className="eyebrow">Deployment context</span><h2>核准的三等份資金規劃</h2></div><StatusBadge status={analysis.deployment_plan.status} /></header>
        {plans.length ? <div className="plan-grid">{plans.map((plan, index) => { const entries = recordsOf(plan.entries); return <article key={textOf(plan.plan_revision_id, String(index))}><strong>{textOf(plan.logical_campaign_id, "Deployment plan")}</strong><div>{entries.map((entry, entryIndex) => <span key={textOf(entry.stage, String(entryIndex))}>{textOf(entry.stage, `Stage ${entryIndex + 1}`)} · {textOf(entry.allocation_fraction ?? entry.allocation_percent)}</span>)}</div><small>僅限虛擬配置，不送出委託</small></article>; })}</div> : <SectionState status={analysis.deployment_plan.status} reason={analysis.deployment_plan.reason} />}
        <EvidenceButton section={analysis.deployment_plan} onOpen={() => openEvidence("資金規劃證據", analysis.deployment_plan)} />
      </section>
    </div>

    <section className="disclosure-panel"><ShieldCheck aria-hidden="true" /><div><strong>研究邊界</strong><p>本頁只讀取 Evidence Model V2 後端輸出。沒有即時報價、下單、跟單、報酬保證或瀏覽器端金融推算。</p></div></section>
    <EvidenceDrawer title={drawer?.title ?? "證據"} open={drawer !== null} onClose={() => setDrawer(null)}>{drawer ? <><StatusBadge status={drawer.section.status} reason={drawer.section.reason} /><RuleList rules={drawer.section.rules_used} /></> : null}</EvidenceDrawer>
  </div>;
}
