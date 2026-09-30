import { useEffect, useState } from 'react';
import type { WaveSupport } from '../api/guidanceClient';
import { readWaveQualification, type WaveQualification, type WaveQualificationOwner, type WaveQualificationResponse, type WaveQualificationStatus } from '../api/waveQualificationClient';
import { WaveQualification as OlderWaveQualification } from './WaveAnchorAssist';
import './WaveQualificationPanel.css';

const ownerLabels: Record<WaveQualificationOwner, string> = {
  program: '程式更新／核對',
  engineering: '程式開發方補強',
  assistant: '研究助理查證',
  user: '您閱讀後選擇',
};
const statusLabels: Record<WaveQualificationStatus, string> = { passed: '已確認', failed: '未通過', unknown: '尚無證據' };
const statusClass: Record<WaveQualificationStatus, string> = { passed: 'is-passed', failed: 'is-failed', unknown: 'is-unknown' };
const countLabels: Record<string, string> = { rows: '價格筆數', invalid: '異常筆數', duplicate_dates: '重複日期', excluded: '已排除筆數', interval_days: '區間日數', missing: '已知缺日', unknown: '尚缺證據日數', conflicts: '衝突日數', suspended: '停牌日數', outside_listing: '非上市期間日數' };
const evidenceLabels: Record<string, string> = { price_snapshot: '價格快照', calendar_revision: '交易日修訂', lifecycle_event_id: '上市狀態事件', operational_event_id: '交易狀態事件', project_rule: '專案實驗規則' };

function dateRange(range: { start: string; end: string } | null) {
  return range ? `${range.start} ～ ${range.end}` : '尚無可用日期';
}

export function WaveQualificationPanel({ symbol, data, historical = false }: { symbol: string; data: WaveSupport; historical?: boolean }) {
  if (historical) return <OlderWaveQualification data={data} />;
  return <CurrentWaveQualification key={`${symbol}:${data.snapshot_id}:${data.checked_at}`} symbol={symbol} data={data} />;
}

function CurrentWaveQualification({ symbol, data }: { symbol: string; data: WaveSupport }) {
  const [result, setResult] = useState<WaveQualificationResponse | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void readWaveQualification(symbol, controller.signal).then(payload => {
      if (active) setResult(payload);
    }).catch(reason => {
      if (active && !(reason instanceof DOMException && reason.name === 'AbortError')) setError(true);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; controller.abort(); };
  }, [symbol, retryCount]);

  if (result?.enabled === false) return <OlderWaveQualification data={data} />;

  const retry = () => { setResult(null); setError(false); setLoading(true); setRetryCount(value => value + 1); };
  if (loading) return <section className="wave-qualification-panel" aria-label="波段資料資格">
    <p role="status">正在讀取波段資料資格…</p>
  </section>;

  if (error || !result) return <section className="wave-qualification-panel wave-qualification-panel--unavailable" aria-label="波段資料資格">
    <div role="alert"><strong>暫時無法檢查波段資料資格。</strong><p>這次檢核未完成，不能視為通過；請重試。既有人工情境仍可獨立閱讀。</p></div>
    <button type="button" onClick={retry}>重試波段資料檢核</button>
  </section>;

  const qualification = result as WaveQualification;
  return <section className="wave-qualification-panel" aria-label="波段資料資格">
    <header className="wave-qualification-panel__summary">
      <p className="wave-qualification-panel__eyebrow">波段資料檢核 · {qualification.status === 'unavailable' ? '目前不可用' : qualification.status === 'quality_warning' ? '資料品質待確認' : '資料不足'}</p>
      <h3>波段資料：現在能讀什麼</h3>
      <p>{qualification.headline}</p>
      <p>要求檢查區間：{dateRange(qualification.requested_range)}</p>
      <p>實際行情區間：{dateRange(qualification.actual_range)}</p>
      <p className="wave-qualification-panel__next"><strong>下一步：</strong>{qualification.next_step.action} · {ownerLabels[qualification.next_step.owner]}</p>
      <p className="wave-qualification-panel__muted">檢查時間：{qualification.checked_at}</p>
    </header>
    {qualification.status === 'unavailable' && <div className="wave-qualification-panel__unavailable" role="alert">
      <strong>本次檢核不可用，不能視為通過。</strong><p>請重試資料檢核；目前不會啟用自動波段候選。</p>
    </div>}
    {qualification.notices.length > 0 && <ul className="wave-qualification-panel__notices">{qualification.notices.map((notice, index) => <li key={`${index}-${notice}`}>{notice}</li>)}</ul>}
    <details className="wave-qualification-panel__details">
      <summary>檢核細節（{qualification.checks.length}）</summary>
      {qualification.checks.length > 0 ? <ul className="wave-qualification-panel__checks">
        {qualification.checks.map(check => <li key={check.id}>
          <div className="wave-qualification-panel__check-heading"><strong>{check.title}</strong><span className={statusClass[check.status]}>{statusLabels[check.status]}</span></div>
          <p>{check.reason}</p>
          <p><strong>影響：</strong>{check.impact}</p>
          <p><strong>負責：</strong>{ownerLabels[check.owner]}</p>
          {check.counts && Object.keys(check.counts).length > 0 && <p><strong>數量：</strong>{Object.entries(check.counts).map(([key, value]) => `${countLabels[key] ?? key}：${value}`).join('、')}</p>}
          {check.samples && check.samples.length > 0 && <p><strong>日期例子：</strong>{check.samples.join('、')}</p>}
          {check.evidence.length > 0 && <p><strong>證據：</strong>{check.evidence.map(item => `${evidenceLabels[item.kind] ?? item.kind}：${item.reference}`).join('；')}</p>}
        </li>)}
      </ul> : <p>目前沒有檢核項目。</p>}
      <p><strong>請求區間：</strong>{dateRange(qualification.requested_range)}</p>
      {qualification.snapshot && <div className="wave-qualification-panel__provenance">
        <h4>資料來源與快照</h4>
        <p>來源：{qualification.snapshot.source} · 快照：{qualification.snapshot.snapshot_id}</p>
        <p>解析版本：{qualification.snapshot.parser_version} · 資料取得時間：{qualification.snapshot.observed_at}</p>
        <p>原始內容雜湊（SHA-256）：{qualification.snapshot.raw_sha256}</p>
        <p>標準化內容雜湊（SHA-256）：{qualification.snapshot.normalized_sha256}</p>
        <p>來源網址（純文字）：{qualification.snapshot.source_url}</p>
      </div>}
    </details>
    <p className="wave-qualification-panel__eligibility">本檢核不會啟用自動波段候選；有來源的人工假設仍可獨立閱讀。</p>
    <button type="button" className="wave-qualification-panel__retry" onClick={retry}>重新檢核</button>
  </section>;
}
