import { useId } from "react";

export type EarningsQuarter = {
  period_start: string; period_end: string; value: string;
  versions?: { source_keys: string[]; locators?: { report_page?: number; statement_page?: number } }[];
};
export type EarningsResearchData = {
  status?: string; value?: string | null; reason?: string | null; rows?: EarningsQuarter[];
  period_start?: string; period_end?: string; observed_at?: string; last_checked_at?: string;
  last_update_status?: string; last_update_reason?: string | null; is_stale?: boolean;
  limitations?: string[]; snapshot_id?: string; parser_version?: string; raw_sha256?: string;
  sources?: { key: string; url: string; role: string; year: number; quarter: number; sha256: string; observed_at: string }[];
  unreviewed_source_versions?: { key: string; url: string; sha256: string; observed_at: string }[];
  basis?: { ledgers?: { page: number; period_start: string; period_end: string }[] };
};

const reasons: Record<string, string> = {
  source_format_not_supported: "這家公司的財報格式尚未完成程式核對。由開發端補強，先閱讀逐季資料即可。",
  not_collected: "尚未取得這組財報。可使用頁面原有的更新資料功能。",
  source_revision_requires_review: "來源內容已有變動，需要重新核對；上次取得的逐季資料仍保留。",
  earnings_parser_requires_refresh: "核對方式已更新，請更新資料後再閱讀合計。",
  earnings_period_requires_refresh: "這組財報涵蓋的期間較舊，需要補查後續季度；不再顯示為目前可用合計。",
  earnings_source_fetch_or_parse_failed: "本次下載或核對未完成。程式保留已取得的資料，稍後可再更新。",
  earnings_storage_limit: "本機獲利資料區已達保存上限，暫停新增，既有紀錄仍可閱讀。",
  quarter_missing: "還缺少連續季度，請研究助理查找公司原始財報。",
  quarter_revision_conflict: "同一季度出現不同版本，需要查明修訂原因。",
  snapshot_integrity_error: "本機資料內容與保存時不同，需要重新查核。",
};

const localTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '日期待核對' : new Intl.DateTimeFormat('zh-TW', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(date);
};

export function EarningsResearchPanel({ data }: { data: EarningsResearchData }) {
  const headingId = useId();
  const available = data.status === "available" && data.value != null && !data.is_stale && data.last_update_status !== "failed";
  return <section aria-labelledby={headingId} style={{ marginTop: 20, padding: 16, border: "1px solid #d6dee7", borderRadius: 10, overflowWrap: "anywhere" }}>
    <h3 id={headingId} style={{ marginTop: 0 }}>最近四季基本每股盈餘合計</h3>
    {available ? <p><strong style={{ fontSize: "1.4rem" }}>{data.value} 元／股</strong></p>
      : <p style={{ color: "#92400e" }}>目前不能合計。{reasons[data.reason || ""] || "資料口徑仍待核對，先閱讀已取得內容；不需要填值或核准資料正確性。"}</p>}
    {data.period_start && <p>涵蓋期間：{data.period_start} 至 {data.period_end}。僅供這四季研究，後續季度需另行核對。</p>}
    <p>來源：公司原始財報及季度發布資料。方法：四季已公布的基本每股盈餘相加。</p>
    <p style={{ color: "#475569" }}>這不是未來一年的獲利預估，也不一定等於公司公布的全年每股盈餘。</p>
    {data.observed_at && <p>本機首次取得：{localTime(data.observed_at)}。{data.last_checked_at && <>最近檢查：{localTime(data.last_checked_at)}。</>}（台灣時間）</p>}
    {!!data.rows?.length && <ul>{data.rows.map(row => <li key={row.period_end}>{row.period_start} 至 {row.period_end}：{row.value} 元／股{!available && "（保留的原季度資料）"}</li>)}</ul>}
    <details>
      <summary>查看來源版本、核對範圍與限制</summary>
      <p>核對包含報表期間、幣別、基本每股盈餘、對應股數、重複揭露及股本變動。已閱讀不等於保證資料沒有錯誤。</p>
      <ul>{data.limitations?.map(item => <li key={item}>{item}</li>)}</ul>
      {data.basis?.ledgers?.map(row => <p key={row.period_end}>股數／股本變動表：{row.period_start} 至 {row.period_end}，原始文件第 {row.page} 頁。</p>)}
      <ul>{data.sources?.map(source => <li key={source.key} style={{ marginBottom: 8 }}>
        {source.url.startsWith("https://") ? <a href={source.url} target="_blank" rel="noreferrer">{source.year} 年第 {source.quarter} 季{source.role === "basis" ? "完整財報" : source.role === "report" ? "季度發布說明" : "財務明細"}</a> : "來源連結無法使用"}
        <div>內容指紋：{source.sha256}</div>
      </li>)}</ul>
      {!!data.unreviewed_source_versions?.length && <><p>另有新取得的來源版本尚未採用，保留供後續核對：</p><ul>{data.unreviewed_source_versions.map(source => <li key={source.key}>{localTime(source.observed_at)} · 內容指紋：{source.sha256}</li>)}</ul></>}
      <p>研究規則：FIN-01。屬本專案的資料整理方法，不代表杜金龍本人認可，也沒有取得歷史回測資格。</p>
      {data.reason && <p>查核代碼：{data.reason}</p>}
      {data.snapshot_id && <p>保存版本：{data.snapshot_id}；核對程式：{data.parser_version}</p>}
    </details>
  </section>;
}
