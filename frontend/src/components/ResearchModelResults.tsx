import type { ResearchSummaryResponse } from "../api/types";
import { useState } from "react";

function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> =>
    typeof row === "object" && row !== null) : [];
}
function number(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-TW", { maximumFractionDigits: 4 }) : "尚不可計算";
}

export function ResearchModelResults({ summary }: { summary: ResearchSummaryResponse }) {
  const [selection, setSelection] = useState("");
  const valuation = summary.valuation_context.status === "available" ? rows(summary.valuation_context.target_matrix)
    .filter(row => (!row.status || row.status === "available") && typeof row.target_price === "number" && Number.isFinite(row.target_price)) : [];
  const technical = summary.technical_context.status === "available" ? rows(summary.technical_context.targets?.scenarios)
    .filter(row => (!row.status || row.status === "available") && typeof row.calculated_level === "number" && Number.isFinite(row.calculated_level)) : [];
  const groupKey = (cell: Record<string, unknown>) => JSON.stringify([cell.observation_id, cell.fiscal_year, cell.source_name, cell.eps_scenario, cell.eps_value]);
  const groups = [...new Map(valuation.map(cell => [groupKey(cell), cell])).entries()];
  const activeSelection = groups.some(([key]) => key === selection) ? selection : "";
  const visible = activeSelection ? valuation.filter(cell => groupKey(cell) === activeSelection) : valuation;
  return <section className="card" aria-label="模型研究結果" style={{ padding: "1.5rem", margin: "1rem 0" }}>
    <h2>模型研究結果</h2>
    <p>以下為情境推算，並非保證價格或交易指令。資料知識截止時間：{summary.knowledge_cutoff_at}；模型版本：{summary.audit_reference.model_version || "未提供"}。</p>
    <h3>估值情境</h3>
    {groups.length > 1 && <div className="research-model-filter"><label htmlFor="valuation-display">想先看哪一組 EPS？</label>
      <select id="valuation-display" value={activeSelection} onChange={event => setSelection(event.target.value)}>
        <option value="">查看全部已計算情境（{groups.length} 組 EPS）</option>
        {groups.map(([key, cell]) => <option key={key} value={key}>{String(cell.fiscal_year ?? "年度未提供")} 年 · EPS {number(cell.eps_value)} 元 · {String(cell.source_name || "來源未提供")} · v{String(cell.observation_revision_number ?? "未提供")}</option>)}
      </select><p>各年度、來源與 EPS 情境分開看；篩選只改變顯示，保存研究仍包含全部情境。</p></div>}
    {!valuation.length && <p>尚無可計算的估值情境。需要有效核准的預估 EPS 與本益比假設；不影響行情查詢。</p>}
    {visible.map((cell, index) => <article key={index} style={{ borderBottom: "1px solid #cbd5e1", padding: "0.75rem 0" }}>
      <p><strong>{String(cell.pe_label || "估值情境")}：{number(cell.target_price)}{typeof cell.target_price === "number" ? " 元" : ""}</strong></p>
      <p>預估 EPS {number(cell.eps_value)} 元 × 核准本益比 {number(cell.pe_value)} 倍。年度：{String(cell.fiscal_year ?? "未提供")}；來源：{String(cell.source_name ?? "未提供")}。</p>
      <p>成立前提：此預估 EPS 與本益比假設適用。假設變更或核准撤銷時，需重新評估。</p>
      <p>輸入版本：EPS v{String(cell.observation_revision_number ?? "未提供")}／PE v{String(cell.pe_revision_number ?? "未提供")}。資料品質與來源限制請併同上方摘要閱讀；自訂敏感度不代表固定倍數已獲驗證。</p>
      <details><summary>計算與核准依據</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(cell, null, 2)}</pre></details>
    </article>)}
    <h3>波浪與費波南希情境</h3>
    {!technical.length && <p>尚無可計算的波浪情境。需要有效核准的轉折錨點；不影響行情查詢。</p>}
    {technical.map((scenario, index) => <article key={index} style={{ borderBottom: "1px solid #cbd5e1", padding: "0.75rem 0" }}>
      <p><strong>{scenario.scenario_type === "equal_amplitude" ? "等幅目標參考" : "回撤支撐參考"}：{number(scenario.calculated_level)} 元</strong></p>
      <p>計算式：{String(scenario.formula_expression ?? "未提供")}；錨點可用時間：{String(scenario.anchor_available_at ?? "未提供")}。</p>
      <p>成立前提：核准的波段與錨點仍適用。波段判斷改變或核准撤銷時，應重新選定錨點。</p>
      <details><summary>錨點與核准依據</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(scenario, null, 2)}</pre></details>
    </article>)}
  </section>;
}
