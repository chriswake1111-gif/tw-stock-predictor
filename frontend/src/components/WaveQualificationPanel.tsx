import { useEffect, useId, useRef, useState } from 'react';
import type { WaveSupport } from '../api/guidanceClient';
import { readWaveQualification, type WaveQualification, type WaveQualificationOwner, type WaveQualificationResponse, type WaveQualificationStatus, type WaveSessionCoverage } from '../api/waveQualificationClient';
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
const sessionCountLabels: Record<string, string> = {
  interval_days: '要求區間日數', market_open: '市場交易日', market_closed: '市場休市日', stock_traded: '個股有成交',
  suspended: '已確認整日停牌', outside_listing: '非上市期間', intraday: '盤中狀態', missing: '缺行情日', unknown: '缺證據日', conflicts: '資料衝突日',
};
const sessionStatusLabels = { available: '已取得日期佐證', partial: '部分取得日期佐證', missing: '尚無日期佐證', unavailable: '目前不可用' };
const sessionSourceStatusLabels = { accepted: '已讀取', partial: '部分核對', failed: '查詢失敗', revoked: '已撤銷' };
const sessionSampleKindLabels: Record<string, string> = {
  conflicts: '資料衝突', unknown: '缺少證據', missing: '缺少行情', intraday: '盤中狀態不明',
  suspended: '已確認整日停牌', outside_listing: '尚未上市', traded: '官方有成交', closed: '市場休市',
  market_open: '市場交易日', market_closed: '市場休市', stock_traded: '個股有成交',
};

function dateRange(range: { start: string; end: string } | null) {
  return range ? `${range.start} ～ ${range.end}` : '尚無可用日期';
}

function SessionCoverage({ coverage }: { coverage: WaveSessionCoverage }) {
  const id = useId();
  const [copyStatus, setCopyStatus] = useState('');
  const requestDetails = useRef<HTMLDetailsElement>(null);
  const requestText = useRef<HTMLTextAreaElement>(null);
  const copyRequest = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
      await navigator.clipboard.writeText(coverage.assistant_request);
      setCopyStatus('已複製查證需求文字。沒有啟動研究助理或背景更新。');
    } catch {
      setCopyStatus('無法自動複製。請選取下方唯讀文字手動複製；沒有啟動研究助理或背景更新。');
      if (requestDetails.current) requestDetails.current.open = true;
      requestText.current?.focus();
      requestText.current?.select();
    }
  };
  return <section className={`wave-qualification-panel__coverage wave-qualification-panel__coverage--${coverage.status}`} aria-labelledby={`${id}-title`}>
    <p className="wave-qualification-panel__eyebrow">交易日與停復牌：{sessionStatusLabels[coverage.status]}</p>
    <h3 id={`${id}-title`}>交易日與停復牌：目前確認到哪裡</h3>
    <p>現在回頭查證，不代表歷史當時已知。</p>
    <p>本段數字只呈現目前找到的資料與缺口，不會把未知日數當成休市或正常交易。</p>
    <dl className="wave-qualification-panel__coverage-summary">
      {(['stock_traded', 'missing', 'unknown', 'conflicts'] as const).map(key => <div key={key}>
        <dt>{{ stock_traded: '官方有成交', missing: '缺行情', unknown: '缺證據', conflicts: '資料衝突' }[key]}</dt><dd>{coverage.counts[key]} 日</dd>
      </div>)}
      {coverage.counts.intraday > 0 && <div><dt>{sessionCountLabels.intraday}</dt><dd>{coverage.counts.intraday} 日</dd></div>}
    </dl>
    <p>各項可能重疊，不宜直接相加。</p>
    <p><strong>下一步：</strong>{coverage.next_step.action} · {ownerLabels[coverage.next_step.owner]}</p>
    <div className="wave-qualification-panel__copy-request">
      <button type="button" onClick={() => void copyRequest()}>複製查證需求</button>
      <p>此按鈕只複製文字，沒有啟動研究助理或背景更新。</p>
      <p role="status" aria-live="polite">{copyStatus}</p>
    </div>
    <details className="wave-qualification-panel__coverage-details">
      <summary>日期與來源限制（{coverage.sources.length} 個來源）</summary>
      <div className="wave-qualification-panel__coverage-table-wrap">
        <table className="wave-qualification-panel__coverage-table">
          <caption>完整查證日數（部分分類可能重疊）</caption>
          <tbody>{Object.entries(coverage.counts).map(([key, value]) => <tr key={key}>
            <th scope="row">{sessionCountLabels[key] ?? key}</th><td>{value} 日</td>
          </tr>)}</tbody>
        </table>
      </div>
      <p>要求區間：{dateRange(coverage.requested_range)}</p>
      <p>查證時間：{coverage.checked_at}</p>
      <p>最近一份證據取得時間：{coverage.known_at ?? '尚無證據'}</p>
      <p>歷史當時可用性：尚未證明。</p>
      {coverage.samples.length > 0 && <div>
        <h4>日期例子</h4>
        <ul>{coverage.samples.map((sample, index) => <li key={`${sample.date}-${sample.kind}-${index}`}>{sample.date} · {sessionSampleKindLabels[sample.kind] ?? sample.kind}：{sample.reason}</li>)}</ul>
      </div>}
      {coverage.sources.length > 0 ? <ul className="wave-qualification-panel__coverage-sources">{coverage.sources.map((source, index) => <li key={`${source.source_id}-${index}`}>
        <strong>{source.label}</strong>（{sessionSourceStatusLabels[source.status]}）
        <p>來源類別（純文字）：{source.source_id}</p>
        <p>資料區間：{source.start} ～ {source.end} · 取得時間：{source.fetched_at}</p>
        <p>參考資料（純文字）：{source.reference}</p>
        <p>來源網址（純文字）：{source.url}</p>
        {source.limitations.length > 0 && <p>限制：{source.limitations.join('；')}</p>}
      </li>)}</ul> : <p>目前沒有可列出的來源。</p>}
    </details>
    <details className="wave-qualification-panel__request-details" ref={requestDetails}>
      <summary>查證需求內容</summary>
      <label htmlFor={`${id}-request`}>查證需求（唯讀，可手動選取複製）</label>
      <textarea ref={requestText} id={`${id}-request`} readOnly rows={4} value={coverage.assistant_request} onFocus={event => event.currentTarget.select()} />
    </details>
  </section>;
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
    {qualification.session_coverage && <SessionCoverage coverage={qualification.session_coverage} />}
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
