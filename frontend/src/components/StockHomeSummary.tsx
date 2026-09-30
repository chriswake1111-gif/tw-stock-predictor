import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { localResearchLink, readHomeSummary } from '../api/homeSummaryClient';

const owners: Record<string, string> = { user: '您閱讀後選擇', assistant: '研究助理查證', program: '程式更新／核對', engineering: '資料能力待補強' };
const when = (value: string) => new Date(value).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });

export function StockHomeSummary({ symbol, pageKey }: { symbol: string; pageKey: string }) {
  const query = useQuery({ queryKey: ['home-summary', pageKey, symbol], queryFn: ({ signal }) => readHomeSummary(symbol, signal),
    retry: false, staleTime: 0, gcTime: 0, refetchOnMount: 'always', refetchOnWindowFocus: false });
  if (query.data?.enabled === false) return null;
  if (query.isFetching || query.isPending) return <p className="stock-library-muted" role="status">正在讀取本機摘要…</p>;
  if (query.isError || !query.data) return <div className="stock-home-summary"><p role="alert">暫時無法比較，既有研究仍保留。</p><button onClick={() => void query.refetch()}>重試本機摘要</button></div>;
  const data = query.data;
  const priceDate = data.dates.find(d => d.field === 'official_close');
  const notices = [...new Set(data.limitations.filter(l => /:(update|stale|quality)$/.test(l.id)).map(l => l.text))];
  const gap = data.limitations.find(l => !/:(update|stale|quality)$/.test(l.id));
  return <div className="stock-home-summary">
    <p className="stock-home-headline">{data.headline}</p>
    <p className="stock-library-muted">僅使用本機已取得內容 · 整理於 {when(data.prepared_at)}<br />
      {data.baseline ? `比較基準：${when(data.baseline.created_at)} 確認保存的研究` : data.status === 'unavailable' ? '比較基準尚未確認' : '尚無確認保存的比較基準'}
      {data.selected_year != null && ` · ${data.selected_year} 年研究`}</p>
    {priceDate && <p className="stock-library-muted">行情資料日：{priceDate.previous || '尚缺'} → {priceDate.current || '尚缺'}</p>}
    {notices.map(text => <p className="stock-library-muted" key={text}>{text}</p>)}
    {data.dates.length > 0 && <details><summary>實際比較的資料日期</summary><ul>{data.dates.map(d => <li key={d.field}>{d.label}：{d.previous || '尚缺'} → {d.current || '尚缺'}</li>)}</ul></details>}
    {data.limitations.length > 0 && <>{gap && <p className="stock-library-muted">{gap.text} · {owners[gap.owner]}</p>}
      <details><summary>資料限制與分工（{data.limitations.length}）</summary>{data.limitations.map(l => <p key={l.id}>{l.text} {l.impact} · {owners[l.owner]}</p>)}</details></>}
    <p className="stock-library-muted">下一步由：{owners[data.next_step.owner]}</p>
    <Link to={localResearchLink(symbol, data.next_step.target, data.selected_year)}>{data.next_step.label}</Link>
    {data.status === 'unavailable' && <button onClick={() => void query.refetch()}>重試本機摘要</button>}
  </div>;
}
