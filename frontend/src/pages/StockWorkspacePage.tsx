import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, Database, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { evidenceApi } from "../api/client";
import type { AnalysisSection, RuleTrace } from "../api/types";
import { EvidenceDrawer } from "../components/evidence/EvidenceDrawer";
import { AsOfTimestamp, EvidenceLevelBadge, MethodStrengthIndicator, StatusBadge } from "../components/evidence/EvidencePrimitives";
import { SectionState } from "../components/evidence/SectionState";
import { formatPrice, recordOf, textOf } from "../lib/records";

const scenarioRolePresentation = {
  target: { title: "等幅目標情境", label: "Target" },
  support: { title: "0.382 支撐情境", label: "Support" },
} as const;

function EvidenceButton({ section, onOpen }: { section: AnalysisSection; onOpen: () => void }) {
  const count = section.rules_used?.length ?? 0;
  return <button className="evidence-link" type="button" onClick={onOpen}><ShieldCheck aria-hidden="true" size={16} />{count ? `${count} 條規則證據` : "查看資料來源"}<ChevronRight aria-hidden="true" size={15} /></button>;
}

function RuleList({ rules }: { rules: RuleTrace[] | undefined }) {
  if (!rules?.length) return <p className="muted">此區段目前沒有可顯示的規則證據。</p>;
  return <div className="drawer-rule-list">{rules.map((rule, index) => (
    <article key={`${rule.rule_id}-${rule.approval_id ?? index}`}>
      <div className="rule-heading"><strong>{rule.rule_id}</strong>{rule.evidence_level ? <EvidenceLevelBadge level={rule.evidence_level} /> : null}</div>
      <dl className="compact-dl">
        <div><dt>版本</dt><dd>{rule.rule_version ?? rule.version ?? "—"}</dd></div>
        <div><dt>實作模式</dt><dd>{rule.implementation_mode ?? "—"}</dd></div>
        <div><dt>核准 ID</dt><dd>{rule.approval_id ?? "未提供"}</dd></div>
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
  const valuationCells = analysis.valuation.target_matrix ?? [];
  const clusters = analysis.target_confluence.overlap_ranges ?? [];
  const scenarios = analysis.technical_support.scenarios ?? [];
  const plans = analysis.deployment_plan.plans ?? [];
  const screeningComponents = recordOf(analysis.screening.components);
  const openEvidence = (title: string, section: AnalysisSection) => setDrawer({ title, section });

  return <div className="page stock-workspace">
    <header className="workspace-heading">
      <div><span className="eyebrow">Evidence workspace / individual security</span><div className="title-row"><h1>{analysis.symbol}</h1><StatusBadge status={analysis.status} /></div><AsOfTimestamp value={analysis.knowledge_cutoff_at} /></div>
      <div className="model-disclosure"><Database aria-hidden="true" size={18} /><span>{analysis.model.name} · {analysis.model.version}</span><small>研究與決策支援 · 非官方關係、非投資建議</small></div>
    </header>

    <section className="decision-strip" aria-label="分析狀態摘要">
      <article><span>資料可用狀態</span><StatusBadge status={analysis.data_quality.status} /><small>{analysis.data_quality.available_sections.length} 個區段可用</small></article>
      <article><span>估值情境格</span><strong>{valuationCells.length}</strong><small>後端核准資料直接呈現</small></article>
      <article><span>目標交集區</span><strong>{clusters.length}</strong><small>各區獨立呈現，不代表推薦順位</small></article>
      <article><span>需要人工介入</span><strong>{analysis.data_quality.needs_human_input.length}</strong><small>{analysis.data_quality.needs_human_input.join("、") || "目前沒有"}</small></article>
    </section>

    <div className="workspace-grid">
      <section className="evidence-card evidence-card--wide">
        <header><div><span className="eyebrow">Valuation</span><h2>Forward EPS × PE 情境矩陣</h2></div><StatusBadge status={analysis.valuation.status} reason={analysis.valuation.reason} /></header>
        {valuationCells.length ? <div className="scenario-table-wrap" tabIndex={0} role="region" aria-label="估值情境矩陣，可水平捲動"><table className="scenario-table"><thead><tr><th>年度</th><th>來源</th><th>EPS 情境</th><th>EPS</th><th>PE</th><th>目標價</th></tr></thead><tbody>{valuationCells.slice(0, 8).map((cell) => <tr key={`${cell.observation_id}-${cell.eps_scenario}-${cell.pe_scenario_id}`}><td>{cell.fiscal_year}</td><td>{cell.source_name}</td><td>{cell.eps_scenario}</td><td>{cell.eps_value}</td><td>{cell.pe_value}</td><td className="numeric">{formatPrice(cell.target_price)}</td></tr>)}</tbody></table></div> : <SectionState status={analysis.valuation.status} reason={analysis.valuation.reason} />}
        <EvidenceButton section={analysis.valuation} onOpen={() => openEvidence("估值證據", analysis.valuation)} />
      </section>

      <section className="evidence-card evidence-card--focus">
        <header><div><span className="eyebrow">Target confluence</span><h2>目標方法交集區</h2></div><StatusBadge status={analysis.target_confluence.status} reason={analysis.target_confluence.reason} /></header>
        {clusters.length ? <><div className="target-cluster-list">{clusters.map((cluster, index) => <article className="target-range" key={cluster.cluster_id}><span>交集區 {index + 1}</span><strong>{formatPrice(cluster.price_low)} <ArrowRight aria-hidden="true" /> {formatPrice(cluster.price_high)}</strong><small>TWD / share</small><MethodStrengthIndicator strength={cluster.evidence_strength ?? "尚未分級"} independentMethods={cluster.independent_method_count} /><small>目標方法：{cluster.target_method_families.join("、")}</small></article>)}</div><p className="muted">交集區依系統穩定排序列示，不代表推薦順位。</p></> : <SectionState status={analysis.target_confluence.status} reason={analysis.target_confluence.reason} />}
        <EvidenceButton section={analysis.target_confluence} onOpen={() => openEvidence("目標交集證據", analysis.target_confluence)} />
      </section>

      <section className="evidence-card">
        <header><div><span className="eyebrow">Technical scenarios</span><h2>人工錨點技術情境</h2></div><StatusBadge status={analysis.technical_support.status} /></header>
        {scenarios.length ? <div className="stack-list">{scenarios.map((item) => { const role = scenarioRolePresentation[item.semantic_role]; return <article key={item.anchor_set_revision_id}><strong>{role.title} · {item.rule_trace.rule_id}</strong><span>{formatPrice(item.calculated_level)} TWD</span><small>Role: {role.label}；情境參考，不是保證價格或交易指令</small></article>; })}</div> : <SectionState status={analysis.technical_support.status} reason={analysis.technical_support.reason} />}
        <EvidenceButton section={analysis.technical_support} onOpen={() => openEvidence("技術情境證據", analysis.technical_support)} />
      </section>

      <section className="evidence-card">
        <header><div><span className="eyebrow">Screening</span><h2>二低一高研究篩選</h2></div><StatusBadge status={analysis.screening.status} /></header>
        {Object.keys(screeningComponents).length ? <div className="metric-list">{Object.entries(screeningComponents).map(([name, value]) => { const component = recordOf(value); return <div key={name}><span>{name}</span><StatusBadge status={component.status} reason={textOf(component.reason, "")} /><strong>{textOf(component.percentile)}</strong></div>; })}</div> : <SectionState status={analysis.screening.status} reason={analysis.screening.reason} />}
        <EvidenceButton section={analysis.screening} onOpen={() => openEvidence("篩選證據", analysis.screening)} />
      </section>

      <section className="evidence-card evidence-card--wide">
        <header><div><span className="eyebrow">Deployment context</span><h2>核准後的虛擬資金配置</h2></div><StatusBadge status={analysis.deployment_plan.status} /></header>
        {plans.length ? <div className="plan-grid">{plans.map((plan) => <article key={plan.plan_revision_id}><strong>{plan.logical_campaign_id}</strong><div>{plan.entries.map((entry) => <span key={entry.stage}>Stage {entry.stage} · Weight {entry.weight} · {entry.capital_budget} {entry.currency} · remaining {entry.remaining_entries_after_stage}</span>)}</div><small>僅限虛擬資金配置，不送出委託；trigger 由已核准 plan 保存。</small></article>)}</div> : <SectionState status={analysis.deployment_plan.status} reason={analysis.deployment_plan.reason} />}
        <EvidenceButton section={analysis.deployment_plan} onOpen={() => openEvidence("資金配置證據", analysis.deployment_plan)} />
      </section>
    </div>

    <section className="disclosure-panel"><ShieldCheck aria-hidden="true" /><div><strong>重要限制</strong><p>本頁只呈現 Evidence Model V2 後端保存或計算的數值與狀態，不重算金融模型，不提供買賣指令、排名或未來機率。</p></div></section>
    <EvidenceDrawer title={drawer?.title ?? "證據"} open={drawer !== null} onClose={() => setDrawer(null)}>{drawer ? <><StatusBadge status={drawer.section.status} reason={drawer.section.reason} /><RuleList rules={drawer.section.rules_used} /></> : null}</EvidenceDrawer>
  </div>;
}
