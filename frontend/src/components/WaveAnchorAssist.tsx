import { useId } from 'react';
import type { AnchorPoint, WaveSupport } from '../api/guidanceClient';
import './WaveAnchorAssist.css';

export function WaveQualification({ data }: { data: WaveSupport }) {
  return <section className="wave-qualification" aria-label="波段資料資格">
    <h3>波段資料：現在能讀什麼</h3>
    <p>{data.source} · {data.price_basis}<br />行情區間：{data.first_date || '尚缺'} ～ {data.last_date || '尚缺'}</p>
    <p>目前可閱讀行情與人工來源候選，尚不能直接採用自動錨點。</p>
    <details><summary>為什麼還不能自動選出波段</summary><ul>{data.blockers.map(b => <li key={b}>{b}</li>)}</ul><p>{data.confirmation_policy}</p><p>{data.approval_policy}</p>
      <p>取得時間：{data.observed_at || '尚未取得'}<br />最近檢查：{data.checked_at || '尚未檢查'}</p>
      {data.snapshot_id && <p className="wave-reference">來源快照：{data.snapshot_id}<br />內容指紋：{data.source_sha256}</p>}
    </details><p><strong>下一步：</strong>{data.next_action}</p>
  </section>;
}

const roles: Record<string, string> = { origin: '起點', swing_end: '波段終點', projection_origin: '推算起點' };
export function AnchorDiagram({ anchors: input, ruleId }: { anchors: AnchorPoint[]; ruleId?: string | null }) {
  const id = useId();
  const anchors = [...input].sort((a, b) => a.market_date.localeCompare(b.market_date));
  const valid = anchors.length >= 2 && anchors.length <= 3 && anchors.every(p => Number.isFinite(p.price) && p.price > 0 && Number.isFinite(Date.parse(p.market_date)));
  if (!valid) return <p>錨點內容尚未完整，請先查證來源。</p>;
  const min = Math.min(...anchors.map(a => a.price)); const max = Math.max(...anchors.map(a => a.price));
  const dates = anchors.map(a => Date.parse(a.market_date)); const span = Math.max(...dates) - Math.min(...dates);
  const points = anchors.map((a, i) => ({ x: 40 + 240 * (span ? (dates[i]! - Math.min(...dates)) / span : i / (anchors.length - 1)), y: 120 - 76 * ((a.price - min) / (max - min || 1)) }));
  return <figure className="wave-anchor-diagram">
    <figcaption>{ruleId === 'FB-03' ? '等幅推算的三個錨點' : ruleId === 'FB-04' ? '回檔推算的兩個錨點' : '候選錨點'} · 人工假設</figcaption>
    <svg viewBox="0 0 320 156" role="img" aria-labelledby={id}><title id={id}>候選錨點連線示意；各點日期與價格列於下方，不是完整股價走勢。</title>
      <polyline fill="none" stroke="#155e75" strokeWidth="2" points={points.map(p => `${p.x},${p.y}`).join(' ')} />
      {points.map((p, i) => <g key={anchors[i]!.role}><circle cx={p.x} cy={p.y} r="5" fill="#155e75" /><text x={p.x} y={p.y - 14} textAnchor="middle" fill="#1e293b" fontSize="14">{String.fromCharCode(65 + i)}</text></g>)}
    </svg>
    <ol>{anchors.map((a, i) => <li key={a.role}><strong>{String.fromCharCode(65 + i)} · {roles[a.role] || a.role}</strong><span>{a.market_date} · {a.price.toLocaleString('zh-TW')} 元</span></li>)}</ol>
    <p>連線僅表示候選點的關係，不是完整走勢；確認時點未提供，不能當作即時已確認波浪。價格口徑及理由請閱讀來源。</p>
  </figure>;
}
