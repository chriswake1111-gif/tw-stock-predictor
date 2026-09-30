import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { guidanceRead } from '../api/guidanceClient';
import type { WaveQualification, WaveSessionCoverage } from '../api/waveQualificationClient';
import { WaveQualificationPanel } from '../components/WaveQualificationPanel';
import { waveFixture } from './waveAssistFixture';

vi.mock('../api/guidanceClient', async original => ({ ...await original<typeof import('../api/guidanceClient')>(), guidanceRead: vi.fn() }));

const symbol = '2330.TW';
const sessionCoverage: WaveSessionCoverage = {
  contract_version: 'wave_session_coverage_v1', mode: 'retrospective_local', status: 'partial',
  requested_range: { start: '2026-01-01', end: '2026-01-10' }, checked_at: '2026-09-30T01:30:00Z', known_at: '2026-09-30T01:00:00Z',
  historical_availability: 'not_asserted',
  counts: { interval_days: 10, market_open: 6, market_closed: 4, stock_traded: 5, suspended: 1, outside_listing: 0, intraday: 0, missing: 1, unknown: 2, conflicts: 1 },
  samples: [{ date: '2026-01-05', kind: 'missing', reason: '行情來源缺一筆' }],
  sources: [{ source_id: 'fixture-session', label: '測試交易日曆', status: 'partial', start: '2026-01-01', end: '2026-01-10', fetched_at: '2026-09-30T01:00:00Z', reference: 'fixture:session', url: 'https://example.invalid/calendar', limitations: ['沒有歷史發布時間證據'] }],
  next_step: { owner: 'assistant', action: '核對停復牌公告及行情缺口。' },
  assistant_request: '請只查證此固定日期區間的交易日與停復牌證據。',
};
const qualification = (overrides: Partial<WaveQualification> = {}): WaveQualification => ({
  contract_version: 'wave_qualification_v1', symbol, enabled: true, checked_at: '2026-09-30T01:00:00Z', knowledge_cutoff_at: '2026-09-29T16:00:00Z',
  snapshot: { snapshot_id: 'fixture-snapshot', source: 'Synthetic source', parser_version: '1.0', raw_sha256: 'a'.repeat(64), normalized_sha256: 'b'.repeat(64), observed_at: '2026-09-29T16:00:00Z', source_url: 'https://example.invalid/source' },
  requested_range: { start: '2026-01-01', end: '2026-09-29' }, actual_range: { start: '2026-01-02', end: '2026-09-29' },
  status: 'quality_warning', headline: '資料仍有待查項目。', next_step: { owner: 'engineering', action: '補齊交易日交叉核對。' },
  checks: [
    { id: 'calendar', title: '交易日完整性', status: 'passed', reason_code: 'calendar_checked', reason: '已完成指定範圍的對照。', impact: '這項檢查已確認。', owner: 'program', evidence: [{ kind: 'audit', reference: 'fixture-calendar' }], counts: { missing: 0 } },
    { id: 'actions', title: '公司行動', status: 'failed', reason_code: 'actions_unverified', reason: '尚未核對所有公司行動。', impact: '價格轉折可能受公司行動影響。', owner: 'engineering', evidence: [], samples: ['2026-03-01'] },
    { id: 'parser', title: '來源解析', status: 'unknown', reason_code: 'parser_unknown', reason: '尚無證據。', impact: '解析完整度未知。', owner: 'assistant', evidence: [] },
  ],
  notices: ['僅供研究參考。'], automatic_candidates_eligible: false,
  ...overrides,
});

beforeEach(() => vi.resetAllMocks());

it('renders status labels, next step, date range, and plain text provenance without enabling candidates', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification());
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByText('資料仍有待查項目。')).toBeInTheDocument();
  expect(screen.getByText(/2026-01-02 ～ 2026-09-29/)).toBeInTheDocument();
  expect(screen.getByText(/補齊交易日交叉核對/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('檢核細節（3）'));
  expect(await screen.findByText('已確認')).toBeInTheDocument();
  expect(screen.getByText('未通過')).toBeInTheDocument();
  expect(screen.getByText('尚無證據')).toBeInTheDocument();
  expect(screen.getByText('來源網址（純文字）：https://example.invalid/source')).toBeInTheDocument();
  expect(document.querySelector('a[href="https://example.invalid/source"]')).toBeNull();
  expect(screen.getByText(/不會啟用自動波段候選/)).toBeInTheDocument();
});

it('renders retrospective session counts and plain-text source limitations with a selectable copy request', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification({ session_coverage: sessionCoverage }));
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByRole('heading', { name: '交易日與停復牌：目前確認到哪裡' })).toBeInTheDocument();
  expect(screen.getByText('現在回頭查證，不代表歷史當時已知。')).toBeInTheDocument();
  expect(screen.getByText(/部分取得日期佐證/)).toBeInTheDocument();
  expect(screen.getByText('官方有成交')).toBeInTheDocument();
  expect(screen.getByText('缺行情')).toBeInTheDocument();
  expect(screen.getByText('缺證據')).toBeInTheDocument();
  expect(screen.getByText('資料衝突')).toBeInTheDocument();
  expect(screen.getByText('各項可能重疊，不宜直接相加。')).toBeInTheDocument();
  expect(document.querySelector('.wave-qualification-panel__coverage-details')).not.toHaveAttribute('open');
  expect(document.querySelector('.wave-qualification-panel__request-details')).not.toHaveAttribute('open');
  expect(screen.getByText(/此按鈕只複製文字/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('日期與來源限制（1 個來源）'));
  expect(screen.getByRole('rowheader', { name: '要求區間日數' })).toBeInTheDocument();
  expect(screen.getByText('最近一份證據取得時間：2026-09-30T01:00:00Z')).toBeInTheDocument();
  expect(screen.getByText('歷史當時可用性：尚未證明。')).toBeInTheDocument();
  expect(screen.getByText('日期例子')).toBeInTheDocument();
  expect(screen.getByText(/缺少行情：行情來源缺一筆/)).toBeInTheDocument();
  expect(screen.getByText(/部分核對/)).toBeInTheDocument();
  expect(screen.getByText(/沒有歷史發布時間證據/)).toBeInTheDocument();
  expect(screen.getByText('來源網址（純文字）：https://example.invalid/calendar')).toBeInTheDocument();
  expect(screen.getByText('來源類別（純文字）：fixture-session')).toBeInTheDocument();
  expect(document.querySelector('a[href="https://example.invalid/calendar"]')).toBeNull();
});

it('uses unique accessible ids when multiple coverage panels are rendered', async () => {
  vi.mocked(guidanceRead).mockResolvedValueOnce(qualification({ session_coverage: sessionCoverage }))
    .mockResolvedValueOnce(qualification({ symbol: '2317.TW', session_coverage: sessionCoverage }));
  const { container } = render(<>
    <WaveQualificationPanel symbol={symbol} data={waveFixture} />
    <WaveQualificationPanel symbol="2317.TW" data={waveFixture} />
  </>);
  await screen.findAllByRole('heading', { name: '交易日與停復牌：目前確認到哪裡' });
  const sections = Array.from(container.querySelectorAll('.wave-qualification-panel__coverage'));
  const headingIds = sections.map(section => section.getAttribute('aria-labelledby'));
  const requestIds = Array.from(container.querySelectorAll('.wave-qualification-panel__request-details textarea')).map(textarea => textarea.id);
  expect(new Set(headingIds).size).toBe(2);
  expect(new Set(requestIds).size).toBe(2);
});

it('keeps adversarial source text inert and offers manual selection when clipboard copying fails', async () => {
  const hostile = '<img src=x onerror=alert(1)> javascript:alert(1)';
  vi.mocked(guidanceRead).mockResolvedValue(qualification({ session_coverage: {
    ...sessionCoverage, assistant_request: hostile, sources: [{ ...sessionCoverage.sources[0]!, label: hostile, reference: hostile, url: hostile, limitations: [hostile] }],
  } }));
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByRole('heading', { name: '交易日與停復牌：目前確認到哪裡' })).toBeInTheDocument();
  fireEvent.click(screen.getByText('日期與來源限制（1 個來源）'));
  expect(screen.getByText(`來源網址（純文字）：${hostile}`)).toBeInTheDocument();
  expect(document.querySelector('img,a')).toBeNull();
  expect(navigator.clipboard).toBeUndefined();
  fireEvent.click(screen.getByRole('button', { name: '複製查證需求' }));
  expect(await screen.findByRole('status')).toHaveTextContent('無法自動複製');
  const request = screen.getByRole('textbox', { name: '查證需求（唯讀，可手動選取複製）' });
  expect(request).toHaveValue(hostile);
  expect(request).toHaveAttribute('readonly');
  expect(request).toHaveFocus();
  expect(request.closest('details')).toHaveAttribute('open');
});

it('rejects malformed optional session coverage instead of ignoring it', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification({ session_coverage: {
    ...sessionCoverage, counts: { ...sessionCoverage.counts, missing: -1 },
  } as WaveSessionCoverage }));
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('不能視為通過');
  expect(screen.queryByRole('heading', { name: '交易日與停復牌：目前確認到哪裡' })).not.toBeInTheDocument();
});

it('shows loading and fail-closed error with manual retry', async () => {
  let rejectRead!: (reason: Error) => void;
  vi.mocked(guidanceRead).mockImplementationOnce(() => new Promise((_, reject) => { rejectRead = reject; })).mockResolvedValueOnce(qualification());
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(screen.getByRole('status')).toHaveTextContent('正在讀取波段資料資格');
  rejectRead(new Error('offline'));
  expect(await screen.findByRole('alert')).toHaveTextContent('不能視為通過');
  expect(screen.queryByText('較早版本的資料說明')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重試波段資料檢核' }));
  expect(await screen.findByText('資料仍有待查項目。')).toBeInTheDocument();
});

it('keeps an unavailable result explicitly fail-closed and exposes retry', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification({ status: 'unavailable', headline: '目前無法完成資料檢核。' }));
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('不能視為通過');
  expect(screen.getByRole('button', { name: '重新檢核' })).toBeInTheDocument();
});

it('rejects wrong symbol, malformed contract, and automatic candidate eligibility', async () => {
  vi.mocked(guidanceRead).mockResolvedValueOnce(qualification({ symbol: '9999.TW' })).mockResolvedValueOnce({ ...qualification(), contract_version: 'wrong' })
    .mockResolvedValueOnce(qualification({ actual_range: { start: 'not-a-date', end: '2026-09-29' } }))
    .mockResolvedValueOnce(qualification({ automatic_candidates_eligible: true } as unknown as Partial<WaveQualification>));
  const view = render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重試波段資料檢核' }));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重試波段資料檢核' }));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重試波段資料檢核' }));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  view.unmount();
});

it('aborts the prior symbol read and discards its late result', async () => {
  let resolveOld!: (value: unknown) => void;
  const signals: AbortSignal[] = [];
  vi.mocked(guidanceRead).mockImplementation((path, signal) => {
    signals.push(signal!);
    return path.includes('/2330.TW') ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(qualification({ symbol: '2317.TW', headline: '新標的檢核結果。' }));
  });
  const view = render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  view.rerender(<WaveQualificationPanel symbol="2317.TW" data={{ ...waveFixture, snapshot_id: 'new-snapshot' }} />);
  expect(await screen.findByText('新標的檢核結果。')).toBeInTheDocument();
  expect(signals[0]?.aborted).toBe(true);
  resolveOld(qualification());
  await waitFor(() => expect(screen.queryByText('資料仍有待查項目。')).not.toBeInTheDocument());
});

it('rechecks when the source snapshot or prior check timestamp changes', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification());
  const view = render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  await screen.findByText('資料仍有待查項目。');
  view.rerender(<WaveQualificationPanel symbol={symbol} data={{ ...waveFixture, snapshot_id: 'new-snapshot' }} />);
  await waitFor(() => expect(guidanceRead).toHaveBeenCalledTimes(2));
  view.rerender(<WaveQualificationPanel symbol={symbol} data={{ ...waveFixture, checked_at: '2026-09-30T02:00:00Z' }} />);
  await waitFor(() => expect(guidanceRead).toHaveBeenCalledTimes(3));
});

it('uses old guidance without a request when historical or the server feature flag is off', async () => {
  const historical = render(<WaveQualificationPanel symbol={symbol} data={waveFixture} historical />);
  expect(screen.getByRole('heading', { name: '波段資料：現在能讀什麼' })).toBeInTheDocument();
  expect(guidanceRead).not.toHaveBeenCalled();
  historical.unmount();
  vi.mocked(guidanceRead).mockResolvedValue({ contract_version: 'wave_qualification_v1', enabled: false, symbol });
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  await waitFor(() => expect(guidanceRead).toHaveBeenCalledTimes(1));
  await expect(vi.mocked(guidanceRead).mock.results[0]?.value).resolves.toMatchObject({ enabled: false });
  expect(await screen.findByRole('heading', { name: '波段資料：現在能讀什麼' })).toBeInTheDocument();
});

it('preserves the existing display when the optional session coverage is absent', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification());
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  expect(await screen.findByText('資料仍有待查項目。')).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: '交易日與停復牌：目前確認到哪裡' })).not.toBeInTheDocument();
});

it('does not write, poll, or fetch on window focus', async () => {
  vi.mocked(guidanceRead).mockResolvedValue(qualification());
  render(<WaveQualificationPanel symbol={symbol} data={waveFixture} />);
  await screen.findByText('資料仍有待查項目。');
  window.dispatchEvent(new Event('focus'));
  await waitFor(() => expect(guidanceRead).toHaveBeenCalledTimes(1));
  expect(vi.mocked(guidanceRead).mock.calls[0]?.[0]).toBe('/api/v2/research/wave-qualification/2330.TW');
  expect(vi.mocked(guidanceRead).mock.calls[0]?.[2]).toBe('no-store');
});
