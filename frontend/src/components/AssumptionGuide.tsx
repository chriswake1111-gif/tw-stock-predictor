import type { Assumption } from "./LocalAssumptionEditor";
import "./AssumptionGuide.css";

type Props = {
  items: Assumption[];
  loading: boolean;
  loadFailed: boolean;
  busy: boolean;
  deferred: boolean;
  onStart: (kind: Assumption["kind"]) => void;
  onEdit: (item: Assumption) => void;
  onLater: () => void;
};

export function AssumptionGuide({ items, loading, loadFailed, busy, deferred, onStart, onEdit, onLater }: Props) {
  const approved = items.filter(i => !i.superseded && i.approval?.decision === "approved");
  const pending = items.filter(i => !i.superseded && i.approval?.decision !== "approved");
  return <div className="assumption-guide">
    <p className="assumption-guide-lead">先看資料，需要估值時再設定假設</p>
    <p>行情、歷史價量、已公布 EPS 與研究筆記不需要先填這張表。估值需要「全年預估 EPS」和「PE 假設」；波浪則另需確認錨點。</p>
    <ol className="assumption-guide-steps">
      <li><strong>準備全年預估 EPS</strong><span>確認預估年度、元／股數值、來源與發布日。單季實績或歷史 TTM 不能直接代填。</span></li>
      <li><strong>設定 PE 假設</strong><span>確認與 EPS 相同的適用年度，並記錄採用倍數的理由；畫面上的客觀本益比不等於適用的估值倍數。</span></li>
      <li><strong>預覽，再確認採用</strong><span>草稿不會自動核准。核准代表您同意採用假設，不代表預估已被證實。</span></li>
    </ol>
    <div className="assumption-guide-actions">
      <button type="button" disabled={busy} onClick={() => onStart("eps")}>設定全年預估 EPS</button>
      <button type="button" disabled={busy} onClick={() => onStart("pe")}>設定 PE 假設</button>
      <button type="button" disabled={busy} onClick={() => onStart("anchor")}>設定波浪錨點</button>
      <button type="button" disabled={busy} onClick={onLater}>稍後設定，先看資料</button>
    </div>
    {deferred && <p role="status">已暫時收起設定，沒有新增或撤銷任何假設。<a href="#daily-public-data">查看客觀資料</a>，或<a href="#daily-journal">保存研究與筆記</a>；隨時可以回來設定。</p>}
    <details><summary>沒有預估 EPS，可以怎麼做？</summary>
      <p>可先查閱您能取得的研究報告或公司投資人資料，確認是否真的提供「指定年度的全年預估 EPS」。公司公布的歷史財報與營運展望不能直接當成這個數字。</p>
      <p>若自行估算，來源明寫「自行估算」，並在理由記錄計算依據。沒有足夠依據時先略過；這個入口不會自動搜尋或生成預估值。</p>
    </details>
    <section aria-label="已保存假設摘要">
      <h3>已保存假設摘要</h3>
      {loading ? <p role="status">正在讀取假設紀錄…</p> : loadFailed ? <p role="alert">無法讀取假設摘要，不能據此判定沒有已保存紀錄。請重新整理後再確認。</p> : <>
        {approved.length === 0 && <p>目前沒有未被新版取代的已核准紀錄。可以先閱讀資料，不必為了完成表單而填數字。</p>}
        {approved.length > 0 && <p>以下為已核准且未被新版取代的紀錄；實際適用性與採用組合以模型情境為準，不必每天重建。</p>}
        <ul>{approved.map(i => <li key={i.id}>
          <strong>{i.kind === "eps" ? `${i.fiscal_year} 年預估 EPS：${i.eps_base} 元／股` : i.kind === "pe" ? `${i.fiscal_year ?? "年度待確認"} · ${i.label}：PE ${i.pe_value} 倍` : `波浪錨點：${i.evidence_basis_rule_id}`}</strong>
          <span>v{i.revision_number} · 已核准</span>
          {i.kind === "pe" && !i.fiscal_year && <span>舊紀錄未註明年度，暫不參與新計算。請修改這份 PE 假設，補上年度後核准新版本；舊研究仍保留。</span>}
          {i.kind === "eps" && <span>來源：{i.source_name || "未記錄"}；發布日：{i.published_at || "未記錄"}</span>}
          <button type="button" disabled={busy} onClick={() => onEdit(i)}>修改這份{i.kind === "eps" ? "EPS" : i.kind === "pe" ? "PE" : "錨點"}假設</button>
        </li>)}</ul>
        {pending.length > 0 && <p>另有 {pending.length} 份未核准或已撤銷紀錄。<button type="button" disabled={busy} onClick={() => onStart("eps")}>查看與管理紀錄</button></p>}
      </>}
    </section>
  </div>;
}
