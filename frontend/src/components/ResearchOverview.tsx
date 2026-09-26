import type { ResearchSummaryResponse } from "../api/types";
import { formatPrice, formatRatioPercent, formatTurnover } from "../lib/humanStatusAdapter";
import "./ResearchOverview.css";

type Props = { summary: ResearchSummaryResponse; historical: boolean; onOpenSection: (id: string) => void };
type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] => Array.isArray(value)
  ? value.filter((row): row is Row => !!row && typeof row === "object") : [];
const numeric = (value: unknown) => typeof value === "number" && Number.isFinite(value);
const display = (value: unknown, suffix = "") => numeric(value)
  ? `${(value as number).toLocaleString("zh-TW", { maximumFractionDigits: 4 })}${suffix}` : "尚無資料";
const latest = (value: unknown) => rows(value).filter(row => typeof row.date === "string")
  .sort((a, b) => String(a.date).localeCompare(String(b.date))).at(-1);
const date = (value: unknown) => typeof value === "string" && value ? value : "日期未提供";

export function ResearchOverview({ summary, historical, onOpenSection }: Props) {
  const market = summary.market_context;
  const prices = summary.public_data?.TaiwanStockPrice;
  const ratios = summary.public_data?.TaiwanStockPER;
  const financials = summary.public_data?.TaiwanStockFinancialStatements;
  const price = latest(prices?.rows);
  const ratio = latest(ratios?.rows);
  const valuations = summary.valuation_context.status === "available"
    ? rows(summary.valuation_context.target_matrix).filter(row => (!row.status || row.status === "available") && numeric(row.target_price)) : [];
  const waves = summary.technical_context.status === "available"
    ? rows(summary.technical_context.targets?.scenarios).filter(row => (!row.status || row.status === "available") && numeric(row.calculated_level)) : [];
  const years = [...new Set(valuations.map(row => row.fiscal_year).filter(Boolean))].join("、");
  const moduleStatus = (data: typeof prices) => {
    if (data?.last_update_status === "failed") return "本次更新失敗，保留已取得資料";
    if (data?.last_update_status === "partial") return "本次更新部分完成";
    if (data?.status === "available") return "已有資料，請留意日期與適用限制";
    if (data?.status === "quality_warning" || data?.status === "partial") return "已有部分資料，品質仍待確認";
    return "資料不足，尚不能完整判讀";
  };
  const missing = [
    ...(market.close_status !== "available" || !numeric(market.official_close) ? ["官方收盤價尚未就緒；可先查看已取得的其他資料。"] : []),
    ...(!price ? ["歷史價量尚缺，請更新資料後再查看。"] : []),
    ...(!ratio || !numeric(ratio.pe) || !numeric(ratio.pb) || !numeric(ratio.yield_ratio) ? ["本益比、股價淨值比或殖利率仍有缺值。"] : []),
    // The current collector has not verified share basis; never infer a usable TTM from quarter rows.
    "財報每股盈餘的股數基準尚未核對，暫不提供可採用的 TTM EPS。",
    ...(summary.valuation_context.year_pairing?.status === "needs_human_input"
      ? ["部分估值缺少同年度 PE，或舊 PE 尚未設定年度；請補上適用年度並核准新版本。已可計算的年度仍可查閱。"] : []),
    ...(!valuations.length ? [summary.valuation_context.status === "needs_human_judgment"
      ? "估值需要確認全年預估 EPS 與 PE 假設；沒有依據時可以先略過。"
      : "估值目前缺少可用輸入或適用證據；曾核准不代表本次可計算。"] : []),
    ...(!waves.length ? ["波浪目前沒有可用的計算結果；需要適用資料與有效核准的錨點。"] : []),
    ...(market.market_turnover_status !== "available" ? ["雙市場成交額尚缺，不影響其他已取得資料的閱讀。"] : []),
    ...(market.cbc_status !== "available" ? ["市場成交額與 M1B 比率尚缺；月資料與日行情須分別看日期。"] : []),
  ];
  return <div className="research-overview">
    <p className="research-overview__intro">收盤後研究 · 先看資料，再決定是否設定情境。可以保存部分研究，不必先填完所有假設。</p>
    <nav className="research-overview__shortcuts" aria-label="研究閱讀順序">
      {[["daily-public-data", "看價量與基本面"], ["research-models", "看計算情境"], ...(!historical ? [["daily-journal", "找前次研究與筆記"]] : [])].map(([id, label]) =>
        <a key={id} href={`#${id}`} onClick={event => { event.preventDefault(); onOpenSection(id!); }}>{label}</a>)}
    </nav>
    <div className="research-overview__columns">
      <section aria-labelledby="research-facts-heading">
        <h2 id="research-facts-heading">目前發生什麼</h2>
        <p className="research-overview__quote-label">已取得的官方收盤價</p>
        <p className="research-overview__quote">{market.close_status === "available" ? formatPrice(market.official_close, market.currency) : "尚無官方報價"}</p>
        <p className="research-overview__muted">{date(market.settled_trade_date)} · {summary.venue} · 已取得日期不代表來源已發布最新資料</p>
        {price && <div className="research-overview__supplement">
          <strong>補充行情：{display(price.close, " 元")}</strong>
          <p>{date(price.date)} · {prices?.source || "來源未提供"}（{prices?.official_exchange_source ? "官方交易所來源" : "非官方交易所來源"}）</p>
          <p>來源所列當日漲跌 {display(price.change, " 元")}；成交量 {display(price.volume, " 股")}</p>
          {price.date !== market.settled_trade_date && <p className="research-overview__warning">兩筆行情日期不同，請分開閱讀。</p>}
          {prices?.last_update_status === "failed" && <p className="research-overview__warning">補充行情更新失敗；此處保留先前取得的資料。</p>}
        </div>}
        <dl className="research-overview__metrics">
          <div><dt>本益比 PE</dt><dd>{display(ratio?.pe, " 倍")}</dd></div>
          <div><dt>股價淨值比 PB</dt><dd>{display(ratio?.pb, " 倍")}</dd></div>
          <div><dt>殖利率</dt><dd>{numeric(ratio?.yield_ratio) ? formatRatioPercent(ratio!.yield_ratio as number) : "尚無資料"}</dd></div>
        </dl>
        <p className="research-overview__muted">上述指標：{date(ratio?.date)} · {ratios?.source || "來源未提供"}；市場 PE 不等於您採用的估值倍數。</p>
        <button type="button" onClick={() => onOpenSection("daily-public-data")}>查看價量圖與基本面</button>
      </section>
      <section aria-labelledby="research-scenarios-heading">
        <h2 id="research-scenarios-heading">程式算出什麼</h2>
        <p><strong>{valuations.length ? `${valuations.length} 組估值情境可查閱` : "估值情境尚不可計算"}</strong></p>
        {valuations.length > 0 && <p>預估年度：{years || "未提供"}。依各筆 EPS 與 PE 假設分開呈現，不合併成單一目標價。</p>}
        <p>{waves.length ? `${waves.length} 組波浪情境可查閱` : "目前沒有可用的波浪情境"}</p>
        <p className="research-overview__warning">自訂倍數屬敏感度情境；核准不會讓它變成券商共識或已驗證的杜金龍固定倍數。</p>
        <p>模型版本：{summary.audit_reference.model_version || "未提供"}。成立與失效條件、來源及核准依據，請一併閱讀。</p>
        <button type="button" onClick={() => onOpenSection("research-models")}>查看程式情境與依據</button>
        <details className="research-overview__glossary"><summary>第一次看 EPS、PE、PB？</summary>
          <p>EPS 是每股盈餘。單季與近四季實績描述過去；全年預估 EPS 是對指定年度的假設。</p>
          <p>PE 是本益比；估值情境使用「全年預估 EPS × 您核准的 PE」。PB 是股價與每股淨值的比值。</p>
          <p>殖利率呈現資料來源定義下的股利與股價比率，不保證未來配息或總報酬。</p>
        </details>
      </section>
    </div>
    <section aria-labelledby="research-gaps-heading" className="research-overview__gaps">
      <h2 id="research-gaps-heading">還缺什麼、哪些要留意</h2>
      <ul>{missing.map(message => <li key={message}>{message}</li>)}</ul>
      <div className="research-overview__sources">
        {[['歷史價量', prices], ['市場估值指標', ratios], ['已公布財報', financials]].map(([name, value]) => {
          const data = value as typeof prices;
          return <p key={String(name)}><strong>{String(name)}：</strong>{moduleStatus(data)}。
            {data?.last_update_reason && <span> 原因：{data.last_update_reason}</span>}</p>;
        })}
        {price && <p>補充價量未還原權息，交易日完整性未稽核；圖表不代表完整調整價序列。</p>}
        <p>雙市場成交額：{formatTurnover(market.market_turnover_total)} · {market.market_turnover_date || "日期未提供"}；
          成交額／M1B：{market.cbc_status === "available" ? formatRatioPercent(market.cbc_m1b_ratio) : "尚無比率資料"} · {market.cbc_period || "月份未提供"}</p>
      </div>
    </section>
    <section aria-labelledby="research-next-heading" className="research-overview__next">
      <h2 id="research-next-heading">下一步做什麼</h2>
      {historical ? <p>目前依歷史截止時間查閱。回到最新研究後，才可修改假設或保存當次筆記。</p> : <>
        <p>先閱讀資料與限制；需要估值時再設定假設。已有有效假設可以沿用，不需每天重填。</p>
        <div className="research-overview__actions">
          <button type="button" className="research-overview__primary" onClick={() => onOpenSection("daily-journal")}>比較前次／保存筆記</button>
          <button type="button" onClick={() => onOpenSection("local-assumptions")}>查看或設定研究假設</button>
        </div>
        <p className="research-overview__muted">上述入口不會自動核准或保存。資料缺失與假設條件會隨研究保留。</p>
      </>}
    </section>
  </div>;
}
