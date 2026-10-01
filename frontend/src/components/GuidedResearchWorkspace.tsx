import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { readReview, guidanceRead, evidenceLabels, topicLabels, type Evidence, type Review, type Guidance, type AnchorPoint } from '../api/guidanceClient';
import { researchMutation, researchErrorMessage } from '../api/researchClient';
import type { ResearchSummaryResponse } from '../api/types';
import { LocalAssumptionEditor, type Assumption } from './LocalAssumptionEditor';
import { DailyPublicDataPanel } from './DailyPublicDataPanel';
import { EarningsResearchPanel, type EarningsResearchData } from './EarningsResearchPanel';
import { ResearchModelResults } from './ResearchModelResults';
import { AnchorDiagram } from './WaveAnchorAssist';
import { WaveQualificationPanel } from './WaveQualificationPanel';
import { AutomaticWaveCandidates } from './AutomaticWaveCandidates';
import { SavedWaveCandidateEvidence } from './SavedWaveCandidateEvidence';
import './GuidedResearchWorkspace.css';

const ownerNames = { program: '程式更新', assistant: '研究助理查證', user: '您閱讀後選擇', engineering: '資料能力待補強' };
const when = (date?: string | null) => date ? new Date(date).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false }) : '日期未提供';
const amount = (value?: number | null) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-TW') : '尚無資料';
const factNames: Record<string, string> = { official_close: '官方收盤價', pe: '市場本益比', pb: '股價淨值比', yield_ratio: '殖利率', volume: '成交股數' };
const factValue = (field: string, value: number | null) => value === null ? '尚無資料' : field === 'yield_ratio' ? `${amount(value * 100)}%` : `${amount(value)} ${field === 'volume' ? '股' : field === 'official_close' ? '元' : '倍'}`;

function EvidenceCard({ item, children }: { item: Evidence; children?: ReactNode }) {
  return <article className="guidance-source">
    <span className="guidance-status">{evidenceLabels[item.review_status] || '查證紀錄'}</span>
    <h3>{item.title}</h3><p className="guidance-muted">{item.publisher || '發布者未提供'} · 發布 {item.published_date || '日期待查'} · 查證 {when(item.recorded_at)}</p>
    <p>{topicLabels[item.topic]} · {item.fiscal_year ? `${item.fiscal_year} 年` : '年度未定／不適用'}{item.value !== null && ` · ${amount(item.value)} ${item.unit === 'multiple' ? '倍' : '元'}`}</p>
    <p><strong>來源說法：</strong>{item.summary}</p>
    {item.interpretation && <p><strong>助理整理：</strong>{item.interpretation}</p>}
    <p><strong>限制：</strong>{item.limitations || '尚未完成限制查證'}</p>
    <details><summary>閱讀範圍與重新檢查條件</summary>
      <p>口徑：{item.basis}；實際閱讀：{item.reading_scope || '尚未核讀'}；定位：{item.locator || '待補'}</p>
      <p>重新檢查：{item.recheck_when || '待整理'}</p>
      <p>{item.source_type === 'attributed_secondary' ? '具名轉述，不表示已取得原始報告。' : '已閱讀不代表來源預估已被證實。'}</p>
      {/^https?:\/\//.test(item.source_url) && <a href={item.source_url} target="_blank" rel="noopener noreferrer">開啟來源</a>}
    </details>{children}
  </article>;
}

function CandidateChoice({ item, symbol, onChanged, waveEnabled }: { item: Evidence; symbol: string; onChanged: () => void; waveEnabled: boolean }) {
  const [prepared, setPrepared] = useState<{ kind: string; values: Record<string, unknown>; candidate_id: string } | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  const key = useRef(crypto.randomUUID());
  async function preview() {
    setBusy(true); setMessage('');
    try {
      const payload = await guidanceRead<NonNullable<typeof prepared>>(`/api/v2/research/evidence/${encodeURIComponent(symbol)}/${item.record_id}/candidate`);
      await researchMutation(`/api/v2/research/assumptions/${encodeURIComponent(symbol)}/${payload.kind}/preview`, { values: payload.values, candidate_id: payload.candidate_id });
      setPrepared(payload);
    } catch (e) {
      setMessage(researchErrorMessage(e, '預覽未完成'));
      if (e instanceof Error && e.message === 'research_evidence_changed_review_again') onChanged();
    }
    finally { setBusy(false); }
  }
  async function createDraft() {
    if (!prepared) return;
    setBusy(true); setMessage('');
    try {
      const result = await researchMutation<{ record: { id: string } }>(`/api/v2/research/assumptions/${encodeURIComponent(symbol)}/${prepared.kind}/draft`, { values: prepared.values, candidate_id: prepared.candidate_id }, key.current);
      setDraft(result.record.id); setMessage('已建立待核准草稿；尚未採用。'); onChanged();
    } catch (e) {
      setMessage(researchErrorMessage(e, '草稿未完成'));
      if (e instanceof Error && e.message === 'research_evidence_changed_review_again') { setPrepared(null); onChanged(); }
    }
    finally { setBusy(false); }
  }
  return <EvidenceCard item={item}>
    {waveEnabled && !prepared && item.topic === 'anchor' && item.anchors && <AnchorDiagram anchors={item.anchors} ruleId={item.rule_id} />}
    {!prepared && <button disabled={busy} onClick={() => void preview()}>預覽這份假設</button>}
    {prepared && <div className="guidance-preview"><h4>預覽具體假設</h4>
      <p>上方來源、年度、數值與限制將一併帶入，不會取代其他系列或繼承核准。</p>
      <dl>{Object.entries(prepared.values).map(([label, value]) => <div key={label}><dt>{{ fiscal_year: '獲利年度', eps_base: '全年預估每股盈餘', pe_value: '本益比倍數', label: '情境名稱', source: '來源', source_date: '發布日期', rationale: '採用前提與限制', rule_id: '計算規則', anchors: '波段日期與價格' }[label] || label}</dt><dd>{waveEnabled && label === 'anchors' && Array.isArray(value) ? <AnchorDiagram anchors={value as AnchorPoint[]} ruleId={String(prepared.values.rule_id)} /> : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
      {!draft && <button disabled={busy} onClick={() => void createDraft()}>建立待核准草稿</button>}
      <button disabled={busy} onClick={() => setPrepared(null)}>返回閱讀</button>
    </div>}{message && <p role="status">{message}</p>}
  </EvidenceCard>;
}

function PendingAssumption({ item, symbol, onChanged }: { item: Assumption; symbol: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  const key = useRef(crypto.randomUUID());
  return <article className="guidance-source"><h3>待核准：{topicLabels[item.kind]}</h3>
    <p>{item.fiscal_year ? `${item.fiscal_year} 年 · ` : ''}{item.kind === 'eps' ? `${amount(item.eps_base)} 元／股` : item.kind === 'pe' ? `${amount(item.pe_value)} 倍` : item.anchors?.map(a => `${a.market_date} ${a.price} 元`).join(' → ')}</p>
    <p>{item.source_name || item.source}</p><p>{item.quality_note || item.rationale || item.source_note}</p>
    <p>核准表示您接受此假設的前提，不代表預估已被證實。</p>
    <button disabled={busy || (item.kind === 'pe' && !item.fiscal_year)} onClick={async () => {
      setBusy(true); try {
        await researchMutation(`/api/v2/research/assumptions/${encodeURIComponent(symbol)}/${item.kind}/${item.id}/approve`, { rationale: '使用者閱讀來源、年度、數值與限制後明確核准此草稿。' }, key.current);
        setMessage('核准完成，正在重新讀取程式結果。'); onChanged();
      } catch (e) { setMessage(researchErrorMessage(e, '核准未完成')); } finally { setBusy(false); }
    }}>確認核准此草稿</button>{message && <p role="status">{message}</p>}
  </article>;
}

function GuidedSave({ review, reload, readFailed }: { review: Review; reload: () => void; readFailed: boolean }) {
  const [userNote, setUserNote] = useState(''); const [aiNote, setAiNote] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ review: Review; note: string } | null>(null);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  const [changed, setChanged] = useState<Review | null>(null);
  const keys = useRef<Record<string, string>>({});
  const assistantNote = aiNote ?? review.guidance?.note_draft ?? '';
  const note = [userNote.trim() && `使用者筆記：\n${userNote.trim()}`, assistantNote.trim() && `人工智慧草稿（經使用者確認保存）：\n${assistantNote.trim()}`].filter(Boolean).join('\n\n');
  return <section id="guided-save" className="guidance-save" aria-label="預覽與保存研究"><h2>保存這次已完成的研究</h2><p>可以保存部分研究，缺項與適用限制會一併保留。</p>
    <label>你的觀察（可留白）<textarea value={userNote} rows={3} maxLength={4000} onChange={e => { setUserNote(e.target.value); setPreview(null); }} /></label>
    {review.guidance?.note_draft_stale && <p role="status">先前助理草稿與目前資料不一致，請助理更新後再採用；你的筆記仍保留。</p>}
    {(assistantNote || aiNote !== null) && <label>人工智慧草稿（可修改）<textarea value={assistantNote} rows={5} maxLength={4000} onChange={e => { setAiNote(e.target.value); setPreview(null); }} /></label>}
    <p className="guidance-muted">合計 {note.length}／4000 字；保存不會核准假設或加入自選。</p>
    <button disabled={busy || readFailed || note.length > 4000} onClick={async () => {
      setBusy(true); setMessage(''); try {
        const latest = await readReview(review.symbol, review.guidance?.selected_year ?? undefined);
        if (latest.review_revision_fingerprint !== review.review_revision_fingerprint) {
          setChanged(latest); setMessage('資料、查證或核准內容已更新；請閱讀下方差異，再重新預覽。'); setPreview(null);
        } else { setChanged(null); setPreview({ review: latest, note }); }
      } catch (e) { setMessage(researchErrorMessage(e, '預覽未完成')); } finally { setBusy(false); }
    }}>預覽將保存的內容</button>
    {changed && <div role="alert" className="guidance-preview"><h3>預覽內容已改變</h3>
      <p>研究發現：{review.guidance?.finding} → {changed.guidance?.finding}</p>
      <p>資料日期：{review.current.summary.market_context?.settled_trade_date || '尚缺'} → {changed.current.summary.market_context?.settled_trade_date || '尚缺'}</p>
      <p>缺項：{review.guidance?.gaps.map(g => g.title).join('、')} → {changed.guidance?.gaps.map(g => g.title).join('、')}</p>
      <p>下一步：{review.guidance?.next_step.title} → {changed.guidance?.next_step.title}</p>
      <p>核准紀錄：{review.guidance?.approved_assumptions.length} → {changed.guidance?.approved_assumptions.length}；來源版本與數值請展開檢查。</p>
      <details><summary>比較原預覽與最新依據</summary><h4>原預覽</h4><ResearchModelResults summary={review.current.summary} />
        {review.current.summary.public_data?.VerifiedQuarterlyEarnings && <EarningsResearchPanel data={review.current.summary.public_data.VerifiedQuarterlyEarnings as EarningsResearchData} />}
        {review.guidance?.evidence.map(i => <EvidenceCard key={i.record_id} item={i} />)}<h4>最新內容</h4><ResearchModelResults summary={changed.current.summary} />
        {changed.current.summary.public_data?.VerifiedQuarterlyEarnings && <EarningsResearchPanel data={changed.current.summary.public_data.VerifiedQuarterlyEarnings as EarningsResearchData} />}
        {changed.guidance?.evidence.map(i => <EvidenceCard key={i.record_id} item={i} />)}</details>
      <button onClick={() => { setChanged(null); setAiNote(null); reload(); }}>閱讀後載入最新內容</button>
    </div>}
    {preview && <div className="guidance-preview"><h3>請確認這份部分研究</h3>
      <p>{preview.review.symbol} · 行情 {preview.review.current.summary.market_context?.settled_trade_date || '日期尚缺'} · 資訊截止 {when(preview.review.knowledge_cutoff_at)}</p>
      <p>{preview.review.guidance?.finding}</p><ul>{preview.review.guidance?.gaps.map(g => <li key={g.id}>{g.title}：{g.impact}</li>)}</ul>
      <details><summary>將保存的程式情境與依據</summary><ResearchModelResults summary={preview.review.current.summary} /></details>
      {preview.review.current.summary.public_data?.VerifiedQuarterlyEarnings && <EarningsResearchPanel data={preview.review.current.summary.public_data.VerifiedQuarterlyEarnings as EarningsResearchData} />}
      <p>附帶本次查證版本 {preview.review.guidance?.evidence.length || 0} 份；之後的來源修訂不會改寫這份研究。</p>
      <h4>完整研究筆記</h4><p className="guidance-note">{preview.note || '未填寫筆記'}</p>
      <button disabled={busy || readFailed || preview.note !== note} onClick={async () => {
        const body = { knowledge_cutoff_at: preview.review.knowledge_cutoff_at, note: preview.note,
          expected_content_fingerprint: preview.review.content_fingerprint, research_year: preview.review.guidance?.selected_year,
          include_research_context: true };
        const identity = JSON.stringify(body); keys.current[identity] ||= crypto.randomUUID();
        setBusy(true); try {
          await researchMutation(`/api/v2/research/journal/${encodeURIComponent(review.symbol)}`, body, keys.current[identity]);
          setMessage('已保存部分研究、完整筆記與查證版本。'); setPreview(null); reload();
        } catch (e) {
          setMessage(researchErrorMessage(e, '保存未完成'));
          if (e instanceof Error && e.message === 'research_content_changed_review_again') { setPreview(null); setChanged(await readReview(review.symbol, review.guidance?.selected_year ?? undefined).catch(() => null)); }
        } finally { setBusy(false); }
      }}>確認保存這份部分研究</button><button disabled={busy} onClick={() => setPreview(null)}>返回修改</button>
    </div>}{message && <p role="status">{message}</p>}
  </section>;
}

export function GuidedResearchWorkspace({ summary, historical, children, onUpdate }: { summary: ResearchSummaryResponse; historical: boolean; children: ReactNode; onUpdate: () => void }) {
  const location = useLocation();
  const [year, setYear] = useState<number | undefined>(() => {
    const value = new URLSearchParams(location.search).get('research_year');
    return value && /^\d{4}$/.test(value) && Number(value) >= 1900 && Number(value) <= 2200 ? Number(value) : undefined;
  });
  const navigation = `${location.key}:${location.hash}`;
  const [tabChoice, setTabChoice] = useState({ navigation: '', tab: 'summary' });
  const requestedTab = ['#research-candidates', '#local-assumptions'].includes(location.hash) ? 'candidates'
    : ['#research-data', '#research-models'].includes(location.hash) ? 'evidence' : 'summary';
  const tab = tabChoice.navigation === navigation ? tabChoice.tab : requestedTab;
  const positioned = useRef('');
  const [deferred, setDeferred] = useState(false); const [legacy, setLegacy] = useState(false);
  const [copyMessage, setCopyMessage] = useState(''); const [compared, setCompared] = useState<string[]>([]);
  const [extraHistory, setExtraHistory] = useState<Evidence[]>([]); const [cursor, setCursor] = useState<string | null | undefined>();
  const [fullHistory, setFullHistory] = useState(false);
  const query = useQuery({ queryKey: ['research-guidance', summary.canonical_symbol, summary.knowledge_cutoff_at, year],
    enabled: !historical && !legacy, retry: false,
    queryFn: ({ signal }) => readReview(summary.canonical_symbol, year, signal) });
  useEffect(() => {
    if (historical || !query.data || positioned.current === navigation) return;
    const target = location.hash.slice(1);
    if (!['research-changes', 'research-candidates', 'research-data'].includes(target)) return;
    const fallback: Record<string, string> = { 'research-changes': 'daily-journal', 'research-candidates': 'local-assumptions', 'research-data': 'daily-public-data' };
    const element = document.getElementById(target) || ((!query.data.guidance || legacy) ? document.getElementById(fallback[target]!) : null);
    if (!element) return;
    positioned.current = navigation;
    if (element instanceof HTMLDetailsElement) element.open = true;
    if (target === 'research-changes') element.querySelectorAll('details').forEach(d => { d.open = true; });
    element.scrollIntoView({ block: 'start' });
    const heading = element.matches('h2') ? element : element.querySelector<HTMLElement>('h2, summary');
    if (heading instanceof HTMLElement) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
  }, [historical, query.data, navigation, location.hash, tab, legacy]);
  if (historical || legacy || (query.data && !query.data.guidance)) return <>{children}</>;
  if (!query.data) return <section className="guided-research"><h1>{summary.short_name || summary.company_name || summary.canonical_symbol}</h1>
    <p>本機行情日期：{summary.market_context?.settled_trade_date || '尚缺'}</p>
    <p>{typeof summary.market_context?.official_close === 'number' ? `${summary.market_context.official_close.toFixed(2)} 元` : '官方收盤價尚缺'}</p>
    <p role={query.isError ? 'alert' : 'status'}>{query.isError ? '研究引導暫時無法讀取，沒有改動既有研究。' : '正在整理可讀資料與下一步…'}</p>
    {query.isError && <><button onClick={() => void query.refetch()}>重試</button><button onClick={() => setLegacy(true)}>使用原研究入口</button></>}</section>;
  const review = query.data; const guide = review.guidance as Guidance; const current = review.current.summary;
  const refresh = () => { void query.refetch(); };
  const chooseTab = (next: string) => { setTabChoice({ navigation, tab: next }); setDeferred(false); };
  const selected = guide.candidates.filter(c => compared.includes(c.record_id));
  const currentCursor = cursor === undefined ? guide.evidence_next_cursor : cursor;
  const pending = review.assumptions.filter(a => !a.superseded && !a.approval && (a.kind === 'anchor' || a.fiscal_year === guide.selected_year));
  return <section className="guided-research" aria-label="引導研究工作區">
    {query.isError && <p role="alert">重新讀取未完成，以下保留上次內容，尚不能確認最新核准狀態。<button onClick={refresh}>重新讀取引導</button></p>}
    <header><div><h1>{summary.short_name || summary.company_name || summary.canonical_symbol}</h1><p>{summary.official_code} · {summary.venue === 'TPEX' ? '上櫃' : summary.venue === 'TWSE' ? '上市' : summary.venue}</p></div>
      <p>行情日 {current.market_context?.settled_trade_date || '尚缺'}<br /><span className="guidance-muted">資訊截止 {when(review.knowledge_cutoff_at)} · 台灣時間</span></p></header>
    <nav className="guidance-tabs" aria-label="研究閱讀層次">{([['summary', '快速摘要'], ['candidates', '候選與選擇'], ['evidence', '完整證據']] as const).map(([id, name]) => <button key={id} aria-pressed={tab === id} onClick={() => chooseTab(id)}>{name}</button>)}</nav>
    <div hidden={tab !== 'summary'}>
      <div className="guidance-columns"><section><h2>資料是否足夠</h2><p>{guide.data_readiness === 'partial' ? '部分可用，缺項仍保留' : '已有資料，仍需閱讀適用限制'}</p><span className="guidance-muted">已取得的官方收盤價</span><p className="guidance-price">{amount(current.market_context?.official_close)} {current.market_context?.official_close != null && '元'}</p></section>
      <section><h2>目前研究發現</h2><p>{guide.finding}</p>
        {current.public_data?.VerifiedQuarterlyEarnings && <p>最近四季獲利：{current.public_data.VerifiedQuarterlyEarnings.status === 'available' && current.public_data.VerifiedQuarterlyEarnings.value != null ? `${current.public_data.VerifiedQuarterlyEarnings.value} 元／股（合計至 ${current.public_data.VerifiedQuarterlyEarnings.period_end}）` : '尚不能合計，已取得的逐季資料仍可閱讀'}。</p>}
        <p>資料已取得、假設已核准與結果可計算，是不同狀態。</p><button onClick={() => chooseTab('evidence')}>查看程式結果與依據</button></section></div>
      <section className="guidance-next"><h2>{guide.next_step.title}</h2><p>{guide.next_step.impact}</p><p>下一步由：{ownerNames[guide.next_step.owner]}</p>
        <button onClick={() => guide.next_step.id === 'read' ? document.getElementById('guided-save')?.scrollIntoView({ block: 'start' }) : chooseTab('candidates')}>{guide.next_step.id === 'read' ? '往下預覽與保存' : guide.next_step.action}</button></section>
      <details><summary>缺項分工（{guide.gaps.length}）</summary>{guide.gaps.map(g => <article className="guidance-gap" key={g.id}><h3>{g.title}</h3><p>{g.impact}</p><p>由{ownerNames[g.owner]}處理 · {g.action}</p></article>)}</details>
      <section id="research-changes"><h2>與前次研究相比</h2>{review.previous ? <><p>前次保存 {when(review.previous.created_at)}</p><p>{review.comparison.assumptions_changed ? '程式情境內容有差異，需要閱讀變更；不能直接解讀為你修改了假設。' : '程式情境內容與前次相同，仍請留意資料日期。'}</p>
        <details><summary>比較資料與前次完整筆記</summary><div className="guidance-table"><table><thead><tr><th>項目</th><th>前次／日期</th><th>目前／日期</th></tr></thead><tbody>{review.comparison.facts.map(f => <tr key={f.field}><th>{factNames[f.field] || f.field}</th><td>{factValue(f.field, f.before.value)}／{f.before.date || '尚缺'}</td><td>{factValue(f.field, f.after.value)}／{f.after.date || '尚缺'}{f.status !== 'comparable' && '（不可直接比較）'}</td></tr>)}</tbody></table></div><p>相同資料日不能代表下一個交易日沒有變化。</p><p className="guidance-note">{review.previous.note || '未填筆記'}</p></details></> : <p>尚無前次保存研究，可先完成這次部分研究。</p>}</section>
      <GuidedSave review={review} reload={refresh} readFailed={query.isError} />
    </div>
    {tab === 'candidates' && <>
      <h2 id="research-candidates">先看依據，再決定是否採用</h2><p>列入比較不代表選用、核准或保存研究。</p>
      {guide.wave_support && <AutomaticWaveCandidates key={review.symbol} symbol={review.symbol} historical={historical} onChanged={refresh} />}
      {guide.wave_support && <p>自動候選的資料資格可在「完整證據」閱讀。<button onClick={() => chooseTab('evidence')}>查看波段資料資格</button></p>}
      <label>研究年度<select value={guide.selected_year ?? ''} onChange={e => { setYear(e.target.value ? Number(e.target.value) : undefined); setCompared([]); setDeferred(false); setExtraHistory([]); setCursor(undefined); }}><option value="">需要年度選擇時再決定</option>{guide.available_years.map(y => <option key={y} value={y}>{y} 年</option>)}</select></label>
      <div className="guidance-actions"><button onClick={async () => { try { await navigator.clipboard.writeText(guide.assistant_request); setCopyMessage('已複製，請貼到 Codex；尚未啟動查證。'); } catch { setCopyMessage('無法自動複製，請展開下方完整需求自行複製。'); } }}>複製需求，交給助理查證</button><button onClick={() => setDeferred(true)}>稍後處理，先看資料</button></div>
      {copyMessage && <p role="status">{copyMessage}</p>}<details><summary>完整查證需求</summary><p className="guidance-note">{guide.assistant_request}</p><p>程式不會在背景持續搜尋。</p></details>
      {deferred ? <p role="status">已收起本次選擇，缺項與紀錄仍保留。<button onClick={() => chooseTab('summary')}>返回快速摘要</button><button onClick={() => setDeferred(false)}>繼續閱讀候選</button></p> : <>
        {!guide.candidates.length && <p>{guide.wave_support ? '目前沒有其他可直接審閱的外部來源候選。' : '目前沒有適用且可直接審閱的候選。'}可以先閱讀或保存部分研究，不需要填數字。</p>}
        {guide.candidates.map(item => <div key={item.record_id}><label className="guidance-check"><input type="checkbox" checked={compared.includes(item.record_id)} onChange={e => setCompared(v => e.target.checked ? [...v, item.record_id] : v.filter(id => id !== item.record_id))} />比較：{item.title}</label><CandidateChoice item={item} symbol={review.symbol} onChanged={refresh} waveEnabled={!!guide.wave_support} /></div>)}
        {selected.length > 0 && <div className="guidance-table" aria-live="polite"><h3>已列入 {selected.length} 份資料</h3><table><thead><tr><th>來源</th><th>年度與數值</th><th>限制</th></tr></thead><tbody>{selected.map(i => <tr key={i.record_id}><th>{i.title}</th><td>{i.topic === 'anchor' ? i.anchors?.map(a => `${a.market_date} · ${amount(a.price)} 元`).join(' → ') || '錨點待整理' : `${i.fiscal_year || '不適用'} · ${amount(i.value)} ${i.unit === 'multiple' ? '倍' : '元'}`}</td><td>{i.limitations}</td></tr>)}</tbody></table></div>}
      </>}
      {pending.length > 0 && <section><h2>已選用，待你核准的草稿</h2>{pending.map(a => <PendingAssumption key={a.id} item={a} symbol={review.symbol} onChanged={refresh} />)}</section>}
      <details><summary>已有核准紀錄（{guide.approved_assumptions.length}）</summary><p>實際適用與採用組合仍以程式結果為準，不必每天重建。</p>{guide.approved_assumptions.map(a => <p key={a.id}>{topicLabels[a.kind]} · {a.fiscal_year || '年度未提供'} · {a.kind === 'eps' ? `${a.eps_base} 元／股` : a.kind === 'pe' ? `${a.pe_value} 倍` : a.source_note}</p>)}</details>
      {!!guide.assumption_evidence?.length && <details><summary>目前假設原先引用的來源版本</summary><p>來源修訂不會自行取代這些依據；是否適用仍以假設內容與程式結果為準。</p>{guide.assumption_evidence.map(ref => <div key={ref.assumption_id}><p>{ref.approval?.decision === 'approved' ? '已有核准紀錄' : '未核准／已撤銷'} · 假設 {ref.assumption_id}</p>{'contract_version' in ref.evidence ? <SavedWaveCandidateEvidence evidence={ref.evidence} /> : <EvidenceCard item={ref.evidence} />}</div>)}</details>}
      <details><summary>查證歷程、線索與歷史案例</summary>{(fullHistory ? extraHistory : [...guide.evidence, ...extraHistory]).map(i => <EvidenceCard key={i.record_id} item={i} />)}
        {!fullHistory && <button onClick={async () => { try { const p = await guidanceRead<{ items: Evidence[]; next_cursor: string | null }>(`/api/v2/research/evidence/${review.symbol}?history=true`); setExtraHistory(p.items); setCursor(p.next_cursor); setFullHistory(true); } catch { setCopyMessage('版本歷程讀取失敗，請重試。'); } }}>讀取包含舊版本的完整歷程</button>}
        {currentCursor && <button onClick={async () => { try { const p = await guidanceRead<{ items: Evidence[]; next_cursor: string | null }>(`/api/v2/research/evidence/${review.symbol}?history=${fullHistory}&before=${currentCursor}`); setExtraHistory(v => [...v, ...p.items]); setCursor(p.next_cursor); } catch { setCopyMessage('更多查證紀錄讀取失敗，請重試。'); } }}>載入更多查證紀錄</button>}</details>
    </>}
    {tab === 'evidence' && <><h2 id="research-data">每個判斷，都能回到依據</h2><p>程式資料、計算情境與外部查證分開呈現。</p></>}
    {guide.wave_support && <div hidden={tab !== 'evidence'}><WaveQualificationPanel symbol={review.symbol} data={guide.wave_support} historical={historical} /></div>}
    {tab === 'evidence' && <>
      <DailyPublicDataPanel data={current.public_data || {}} /><ResearchModelResults summary={current} />
      <details><summary>完整程式資料、日期與模型追溯</summary><pre className="guidance-note">{JSON.stringify(current, null, 2)}</pre></details>
      <details><summary>缺項原始原因與資料責任</summary>{guide.gaps.map(g => <p key={g.id}>{g.title} · {ownerNames[g.owner]} · {g.reason || '請依來源與模型狀態查證'}</p>)}</details>
      <details><summary>進階：手動設定、修改或撤銷假設</summary><LocalAssumptionEditor symbol={review.symbol} onChanged={() => { refresh(); onUpdate(); }} /></details>
      <button onClick={() => setLegacy(true)}>切回原研究入口</button></>}
  </section>;
}
