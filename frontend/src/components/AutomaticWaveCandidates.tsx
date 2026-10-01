import { useEffect, useRef, useState } from 'react';
import { createWaveCandidateDraft, previewWaveCandidate, readWaveCandidatePreview, readWaveCandidates, type WaveCandidate, type WaveCandidateAnchor, type WaveCandidateAnchorPreview, type WaveCandidatesResponse } from '../api/waveCandidateClient';
import { researchErrorMessage } from '../api/researchClient';
import './AutomaticWaveCandidates.css';

const price = (value: number) => `${value.toLocaleString('zh-TW', { maximumFractionDigits: 4 })} 元`;
const dateTime = (value: string | null) => value ? new Date(value).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false }) : '尚未提供';
const roleLabel = (role: WaveCandidateAnchor['role']) => role === 'origin' ? '起點' : '波段端點';
const displayValue = (value: unknown, fallback: string) => typeof value === 'string' && value.trim() ? value : fallback;
const ruleLabel = (value: unknown) => value === 'FB-03' ? '等幅推算（FB-03）' : value === 'FB-04' ? '回檔推算（FB-04）' : displayValue(value, '規則未提供');
const objectRecord = (value: unknown): Record<string, unknown> | null => !!value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;

function AnchorRows({ anchors, basisDate, basisLabel }: { anchors: WaveCandidateAnchor[]; basisDate: string; basisLabel: string | null }) {
  return <ol className="wave-candidate-anchors">{anchors.map((anchor, index) => <li key={`${anchor.role}-${anchor.market_date}-${index}`}>
    <strong>{roleLabel(anchor.role)}</strong><span>{anchor.market_date}</span>
    <span>{price(anchor.price)} <small>{basisLabel ? `同基準換算價 · ${basisLabel}` : '價格基準未提供，暫不作同基準比較'}</small></span>
    <details><summary>原價與換算資料</summary><p>原始價 {price(anchor.raw_price)} · 調整因子 {anchor.adjustment_factor}</p><p>基準日期 {basisDate}</p></details>
    <span>確認交易日 {anchor.confirmed_at}</span>
  </li>)}</ol>;
}

function CandidateCard({ candidate, basisDate, basisLabel, symbol, onChanged }: { candidate: WaveCandidate; basisDate: string; basisLabel: string | null; symbol: string; onChanged: () => void }) {
  const [preview, setPreview] = useState<WaveCandidateAnchorPreview | null>(null);
  const [previewResult, setPreviewResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [draftCreated, setDraftCreated] = useState(false);
  const [draftKey, setDraftKey] = useState(() => crypto.randomUUID());
  const operation = useRef<AbortController | null>(null);
  const previewInputs = objectRecord(objectRecord(previewResult)?.inputs);

  useEffect(() => () => operation.current?.abort(), []);

  async function prepare() {
    operation.current?.abort();
    const controller = new AbortController(); operation.current = controller;
    setBusy(true); setMessage(''); setPreview(null); setPreviewResult(null); setDraftCreated(false); setDraftKey(crypto.randomUUID());
    try {
      const payload = await readWaveCandidatePreview(symbol, candidate.candidate_id, controller.signal);
      if (controller.signal.aborted) return;
      const result = await previewWaveCandidate(symbol, payload);
      if (!controller.signal.aborted) { setPreview(payload); setPreviewResult(result); }
    } catch (error) {
      if (!controller.signal.aborted) setMessage(researchErrorMessage(error, '預覽未完成；候選內容沒有改變。'));
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  async function createDraft() {
    if (!preview) return;
    setBusy(true); setMessage('');
    try {
      await createWaveCandidateDraft(symbol, preview, draftKey);
      setDraftCreated(true); setMessage('已建立待核准草稿；尚未核准或保存研究。'); onChanged();
    } catch (error) {
      setMessage(researchErrorMessage(error, '草稿未建立；目前內容仍可閱讀。'));
      if (error instanceof Error && ['candidate_values_mismatch', 'research_evidence_changed_review_again'].includes(error.message)) {
        setPreview(null); setPreviewResult(null);
      }
    }
    finally { setBusy(false); }
  }

  return <article className="automatic-wave-card">
    <h3>{candidate.title}</h3>
    <AnchorRows anchors={candidate.anchors} basisDate={basisDate} basisLabel={basisLabel} />
    <p>候選確認交易日 {candidate.confirmed_at} · 最近得知 {dateTime(candidate.known_at)}</p>
    <p><strong>來源摘要：</strong>{candidate.source_summary}</p>
    {!!candidate.limitations.length && <details><summary>來源與候選限制（{candidate.limitations.length}）</summary><ul>{candidate.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></details>}
    {!preview && <button type="button" disabled={busy} onClick={() => void prepare()}>{busy ? '正在預覽…' : '預覽這份錨點假設'}</button>}
    {preview && <section className="automatic-wave-preview" aria-label={`${candidate.title}具體假設預覽`}>
      <h4>具體假設預覽</h4><p>這些值將送到既有錨點預覽流程；建立草稿後仍需由您核准。候選來源不會自動改變其他研究。</p>
      <dl>
        <div><dt>計算規則</dt><dd>{ruleLabel(preview.values.rule_id ?? previewInputs?.evidence_basis_rule_id)}</dd></div>
        {candidate.anchors.map(anchor => <div key={anchor.role}><dt>{roleLabel(anchor.role)}日期與同基準換算價</dt><dd>{anchor.market_date} · {price(anchor.price)} · {basisLabel ?? '基準名稱未提供'}（基準日 {basisDate}）</dd></div>)}
        <div><dt>來源</dt><dd>{displayValue(preview.values.source, displayValue(previewInputs?.source, candidate.source_summary))}</dd></div>
        <div><dt>採用理由</dt><dd>{displayValue(preview.values.rationale, displayValue(preview.values.source_note, candidate.source_summary))}</dd></div>
        <div><dt>限制</dt><dd>{candidate.limitations.length ? <ul>{candidate.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul> : '未提供個別候選限制，請回看上方來源摘要與整體資料限制。'}</dd></div>
      </dl>
      <details><summary>完整原始預覽回應</summary><pre>{JSON.stringify(previewResult, null, 2)}</pre></details>
      {!draftCreated && <button type="button" disabled={busy} onClick={() => void createDraft()}>{busy ? '正在建立…' : '建立待核准草稿'}</button>}
      <button type="button" disabled={busy} onClick={() => { setPreview(null); setPreviewResult(null); setMessage(''); }}>返回候選</button>
    </section>}
    {message && <p role="status">{message}</p>}
  </article>;
}

export function AutomaticWaveCandidates({ symbol, historical, onChanged }: { symbol: string; historical: boolean; onChanged: () => void }) {
  const [request, setRequest] = useState<{ symbol: string; state: 'loading' } | { symbol: string; state: 'error'; error: string } | { symbol: string; state: 'success'; data: WaveCandidatesResponse } | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    controllerRef.current?.abort();
    if (historical) return;
    const controller = new AbortController(); controllerRef.current = controller;
    void readWaveCandidates(symbol, controller.signal).then(result => {
      if (!controller.signal.aborted) setRequest({ symbol, state: 'success', data: result });
    }).catch(reason => {
      if (!controller.signal.aborted) setRequest({ symbol, state: 'error', error: researchErrorMessage(reason, '目前無法讀取波段候選。') });
    });
    return () => controller.abort();
  }, [symbol, historical]);

  async function reload() {
    controllerRef.current?.abort();
    const controller = new AbortController(); controllerRef.current = controller;
    setRequest({ symbol, state: 'loading' });
    try {
      const result = await readWaveCandidates(symbol, controller.signal);
      if (!controller.signal.aborted) setRequest({ symbol, state: 'success', data: result });
    }
    catch (reason) { if (!controller.signal.aborted) setRequest({ symbol, state: 'error', error: researchErrorMessage(reason, '目前無法讀取波段候選。') }); }
  }

  const current = request?.symbol === symbol ? request : null;
  const data = current?.state === 'success' ? current.data : null;
  const loading = current?.state === 'loading' || current === null;
  const error = current?.state === 'error' ? current.error : '';
  if (historical || (data && data.symbol !== symbol) || (data && !data.enabled)) return null;
  return <section className="automatic-wave-candidates" aria-labelledby="automatic-wave-candidates-title">
    <h2 id="automatic-wave-candidates-title">自動整理的波段研究候選</h2>
    <p>目前取得資料後的研究候選；不代表歷史當時已知，也不會自動判定第幾浪。候選依日期、價格基準與限制並列，不比較漲幅、不排名。</p>
    {loading && <p role="status">正在讀取本機候選…</p>}
    {error && <p role="alert">{error}</p>}
    {error && <button type="button" disabled={loading} onClick={() => void reload()}>重新讀取</button>}
    {data?.symbol === symbol && data.enabled && <>
      <p className="automatic-wave-headline">{data.headline}</p>
      <p>狀態：{({ available: '可供閱讀', insufficient_data: '資料不足', unavailable: '暫時無法提供', unsupported: '目前不支援' } as const)[data.status]} · 最近檢查 {dateTime(data.checked_at)}</p>
      {data.requested_range && <p>要求區間 {data.requested_range.start} 至 {data.requested_range.end} · 實際區間 {data.actual_range ? `${data.actual_range.start} 至 ${data.actual_range.end}` : '尚未提供'}</p>}
      {data.basis && <p>價格基準：{data.basis.label} · 基準日期 {data.basis.anchor_date}</p>}
      {data.status === 'available' && data.candidates.length > 0
        ? <div className="automatic-wave-grid">{data.candidates.map(candidate => <CandidateCard key={candidate.candidate_id} candidate={candidate} basisDate={data.basis?.anchor_date ?? '尚未提供'} basisLabel={data.basis?.label ?? null} symbol={symbol} onChanged={onChanged} />)}</div>
        : <p>{data.status === 'insufficient_data' ? '目前資料不足以整理候選；沒有補入推測數值。' : '目前沒有可供比較的候選。'}</p>}
      {!!data.checks.length && <details><summary>資料檢查項目</summary><ul>{data.checks.map(check => <li key={check.id}>{check.title}：{({ passed: '通過', failed: '未通過', unknown: '未知' } as const)[check.status]} · {check.reason}</li>)}</ul></details>}
      {!!data.limitations.length && <details><summary>資料限制（{data.limitations.length}）</summary><ul>{data.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></details>}
      <details><summary>資料版本與取得時間</summary><p>來源版本：{data.package_ref ?? '尚未提供'}</p><p>最近一份證據取得時間：{dateTime(data.known_at)}</p></details>
      <button type="button" disabled={loading} onClick={() => void reload()}>重新讀取候選</button>
    </>}
  </section>;
}
