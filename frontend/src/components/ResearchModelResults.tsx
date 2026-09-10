import type { ResearchSummaryResponse } from "../api/types";

function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> =>
    typeof row === "object" && row !== null) : [];
}
function number(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("zh-TW", { maximumFractionDigits: 4 }) : "尚不可計算";
}

export function ResearchModelResults({ summary }: { summary: ResearchSummaryResponse }) {
  const valuation = rows(summary.valuation_context.target_matrix);
  const technical = rows(summary.technical_context.targets?.scenarios);
  return <section className="card" aria-label="模型研究結果" style={{ padding: "1.5rem", margin: "1rem 0" }}>
    <h2>模型研究結果</h2>
    <p>以下為情境推算，並非保證價格或交易指令。資料知識截止時間：{summary.knowledge_cutoff_at}</p>
    <h3>估值情境</h3>
    {!valuation.length && <p>尚無可計算的估值情境。需要有效核准的預估 EPS 與本益比假設；不影響行情查詢。</p>}
    {valuation.map((cell, index) => <article key={index} style={{ borderBottom: "1px solid #cbd5e1", padding: "0.75rem 0" }}>
      <p><strong>{String(cell.pe_label || "估值情境")}：{number(cell.target_price)}{typeof cell.target_price === "number" ? " 元" : ""}</strong></p>
      <p>預估 EPS {number(cell.eps_value)} 元 × 核准本益比 {number(cell.pe_value)} 倍。年度：{String(cell.fiscal_year ?? "未提供")}；來源：{String(cell.source_name ?? "未提供")}。</p>
      <p>成立前提：此預估 EPS 與本益比假設適用。假設變更或核准撤銷時，需重新評估。</p>
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
