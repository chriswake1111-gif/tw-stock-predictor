import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AutomaticWaveCandidates } from '../components/AutomaticWaveCandidates';
import { parseWaveCandidates } from '../api/waveCandidateClient';

const mocks = vi.hoisted(() => ({ researchMutation: vi.fn(), onChanged: vi.fn() }));
vi.mock('../api/researchClient', () => ({
  researchMutation: mocks.researchMutation,
  researchErrorMessage: (error: unknown, fallback: string) => error instanceof Error
    ? error.message === 'candidate_values_mismatch' ? '候選內容與預覽不一致，請重新取得來源版本。'
      : error.message === 'research_evidence_changed_review_again' ? '資料或核准內容已更新，請重新閱讀候選與限制。'
        : error.message
    : fallback,
}));

function fixture(symbol = '2330.TW', overrides: Record<string, unknown> = {}) {
  return {
    contract_version: 'wave_candidates_v1', symbol, enabled: true, status: 'available',
    headline: '已整理可閱讀的研究候選', checked_at: '2026-10-01T12:00:00+08:00',
    requested_range: { start: '2025-09-29', end: '2026-09-30' },
    actual_range: { start: '2025-09-29', end: '2026-09-30' }, package_ref: '匿名測試版本',
    known_at: '2026-10-01T11:00:00+08:00', basis: { label: '公告調整後價格', anchor_date: '2026-09-30' },
    limitations: ['來源仍有缺項'], checks: [{ id: 'calendar', title: '交易日覆蓋', status: 'passed', reason: '日期對帳完成' }],
    candidates: [1, 2, 3].map(i => ({
      candidate_id: `candidate-${i}`, title: `匿名候選 ${i}`,
      anchors: [
        { role: 'origin', market_date: `2026-0${i}-01`, price: 100 + i, raw_price: 90 + i, adjustment_factor: 1.1, confirmed_at: `2026-0${i}-02` },
        { role: 'swing_end', market_date: `2026-0${i}-15`, price: 120 + i, raw_price: 110 + i, adjustment_factor: 1.1, confirmed_at: `2026-0${i}-16` },
      ], confirmed_at: `2026-0${i}-16`, known_at: '2026-10-01T10:00:00+08:00',
      source_summary: '<img src=x onerror=alert(1)> 官方資料摘要', limitations: ['尚待人工核對'],
    })),
    ...overrides,
  };
}

const response = (value: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => value });
const detail = (candidateId: string) => ({ kind: 'anchor', candidate_id: candidateId, approval_required: true,
  values: { rule_id: 'FB-03', rationale: '匿名固定測試假設', anchors: [{ role: 'origin', price: 101, market_date: '2026-01-01' }, { role: 'swing_end', price: 121, market_date: '2026-01-15' }] } });

beforeEach(() => {
  mocks.researchMutation.mockReset(); mocks.onChanged.mockReset();
  mocks.researchMutation.mockResolvedValue({ status: 'preview_only', inputs: { anchors: [101, 121] }, calculation: { midpoint: 110 }, approval_required: true });
  vi.stubGlobal('fetch', vi.fn(async () => response(fixture())));
});
afterEach(() => vi.unstubAllGlobals());

describe('AutomaticWaveCandidates', () => {
  it('read-only loads at most three candidates and renders source strings as text', async () => {
    render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    expect(await screen.findByText('匿名候選 1')).toBeTruthy();
    expect(screen.getAllByRole('article')).toHaveLength(3);
    expect(screen.getAllByText(/<img src=x onerror=alert\(1\)> 官方資料摘要/)).toHaveLength(3);
    expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText(/確認交易日 2026-01-02/)).toBeTruthy();
    expect(screen.queryByText(/08:00/)).toBeNull();
    expect(mocks.researchMutation).not.toHaveBeenCalled();
  });

  it('keeps preview and draft separate; only the explicit draft action posts a draft', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/candidate-1')) return response(detail('candidate-1'));
      return response(fixture());
    }));
    render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    const card = await screen.findByText('匿名候選 1').then(node => node.closest('article')!);
    fireEvent.click(within(card).getByRole('button', { name: '預覽這份錨點假設' }));
    expect(await within(card).findByText(/preview_only/)).toBeTruthy();
    expect(within(card).getByText('計算規則')).toBeTruthy();
    expect(within(card).getByText('採用理由')).toBeTruthy();
    expect(within(card).getByText('限制')).toBeTruthy();
    expect(within(card).queryByText(/完整原始預覽回應/)).toBeTruthy();
    expect(mocks.researchMutation).toHaveBeenCalledTimes(1);
    expect(mocks.researchMutation.mock.calls[0]?.[0]).toBe('/api/v2/research/assumptions/2330.TW/anchor/preview');
    expect(within(card).queryByRole('button', { name: '建立待核准草稿' })).toBeTruthy();
    expect(mocks.onChanged).not.toHaveBeenCalled();
    fireEvent.click(within(card).getByRole('button', { name: '建立待核准草稿' }));
    await waitFor(() => expect(mocks.researchMutation).toHaveBeenCalledTimes(2));
    expect(mocks.researchMutation.mock.calls[1]?.[0]).toBe('/api/v2/research/assumptions/2330.TW/anchor/draft');
    expect(mocks.researchMutation.mock.calls[1]?.[2]).toMatch(/[0-9a-f-]{36}/i);
    expect(mocks.onChanged).toHaveBeenCalledTimes(1);
    expect(within(card).queryByRole('button', { name: /核准|保存研究/ })).toBeNull();
  });

  it('does not retain a prior successful response after a manual read fails', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response(fixture())).mockResolvedValueOnce(response({}, false));
    vi.stubGlobal('fetch', fetchMock);
    render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    expect(await screen.findByText('匿名候選 1')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '重新讀取候選' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByText('匿名候選 1')).toBeNull();
  });

  it('aborts the previous symbol request and never displays its result', async () => {
    let firstSignal: AbortSignal | undefined;
    let finishFirst!: (value: ReturnType<typeof response>) => void;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('2330.TW')) {
        firstSignal = init?.signal as AbortSignal;
        return new Promise<ReturnType<typeof response>>(resolve => { finishFirst = resolve; });
      }
      return Promise.resolve(response(fixture('2317.TW')));
    });
    vi.stubGlobal('fetch', fetchMock);
    const view = render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    view.rerender(<AutomaticWaveCandidates symbol="2317.TW" historical={false} onChanged={mocks.onChanged} />);
    expect(firstSignal?.aborted).toBe(true);
    expect(await screen.findByText('匿名候選 1')).toBeTruthy();
    finishFirst(response(fixture('2330.TW')));
    await waitFor(() => expect(screen.queryByText('已整理可閱讀的研究候選')).toBeTruthy());
    expect(fetchMock.mock.calls.length).toBe(2);
  });

  it('does not request or display dynamic data in historical mode and hides disabled responses', async () => {
    const fetchMock = vi.fn(async () => response(fixture('2330.TW', { enabled: false })));
    vi.stubGlobal('fetch', fetchMock);
    const historical = render(<AutomaticWaveCandidates symbol="2330.TW" historical onChanged={mocks.onChanged} />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(historical.container).toBeEmptyDOMElement();
    historical.unmount();
    const live = render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(live.container).toBeEmptyDOMElement();
  });

  it('rejects malformed data and candidate lists larger than the contract limit', () => {
    expect(() => parseWaveCandidates(fixture('2330.TW', { candidates: [1, 2, 3, 4].map(i => fixture().candidates[i % 3]) }))).toThrow('wave_candidates_contract_invalid');
    expect(() => parseWaveCandidates(fixture('2330.TW', { candidates: [{ ...fixture().candidates[0], anchors: [] }] }))).toThrow('wave_candidates_contract_invalid');
    expect(() => parseWaveCandidates(fixture('2330.TW', { checked_at: 'not-a-date' }))).toThrow('wave_candidates_contract_invalid');
    expect(() => parseWaveCandidates(fixture('2330.TW', { requested_range: { start: '2026-02-31', end: '2026-09-30' } }))).toThrow('wave_candidates_contract_invalid');
    const baseCandidate = fixture().candidates[0]!;
    for (const field of ['basis', 'requested_range', 'actual_range', 'package_ref', 'known_at']) {
      expect(() => parseWaveCandidates(fixture('2330.TW', { [field]: null }))).toThrow('wave_candidates_contract_invalid');
    }
    expect(() => parseWaveCandidates(fixture('2330.TW', { candidates: [{ ...baseCandidate, anchors: [...baseCandidate.anchors, baseCandidate.anchors[0]] }] }))).toThrow('wave_candidates_contract_invalid');
    expect(() => parseWaveCandidates(fixture('2330.TW', { candidates: [{ ...baseCandidate, anchors: [...baseCandidate.anchors].reverse() }] }))).toThrow('wave_candidates_contract_invalid');
    expect(() => parseWaveCandidates(fixture('2330.TW', { candidates: [{ ...baseCandidate, anchors: [{ ...baseCandidate.anchors[0], price: 150 }, baseCandidate.anchors[1]] }] }))).toThrow('wave_candidates_contract_invalid');
    expect(() => parseWaveCandidates(fixture('2330.TW', { candidates: [{ ...baseCandidate, known_at: '2026-01-01T00:00:00Z' }] }))).toThrow('wave_candidates_contract_invalid');
  });

  it('rejects stale preview and draft responses without offering a stale draft', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/candidate-1') ? response(detail('candidate-1')) : response(fixture())));
    mocks.researchMutation.mockRejectedValueOnce(new Error('candidate_values_mismatch'));
    const { container } = render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    const card = await screen.findByText('匿名候選 1').then(node => node.closest('article')!);
    fireEvent.click(within(card).getByRole('button', { name: '預覽這份錨點假設' }));
    expect(await within(card).findByText(/候選內容與預覽不一致/)).toBeTruthy();
    expect(within(card).queryByRole('button', { name: '建立待核准草稿' })).toBeNull();
    expect(container.querySelector('pre')).toBeNull();
  });

  it('retries a response-lost draft with the same idempotency key and disables creation after success', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/candidate-1') ? response(detail('candidate-1')) : response(fixture())));
    mocks.researchMutation.mockResolvedValueOnce({ status: 'preview_only', inputs: { anchors: [] }, calculation: {}, approval_required: true })
      .mockRejectedValueOnce(new Error('Failed to fetch'))
      .mockResolvedValueOnce({ record: { id: 'draft-1' } });
    render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    const card = await screen.findByText('匿名候選 1').then(node => node.closest('article')!);
    fireEvent.click(within(card).getByRole('button', { name: '預覽這份錨點假設' }));
    await within(card).findByRole('button', { name: '建立待核准草稿' });
    fireEvent.click(within(card).getByRole('button', { name: '建立待核准草稿' }));
    expect(await within(card).findByText('Failed to fetch')).toBeTruthy();
    fireEvent.click(within(card).getByRole('button', { name: '建立待核准草稿' }));
    await waitFor(() => expect(within(card).getByText(/已建立待核准草稿/)).toBeTruthy());
    const draftCalls = mocks.researchMutation.mock.calls.filter(call => String(call[0]).endsWith('/anchor/draft'));
    expect(draftCalls).toHaveLength(2);
    expect(draftCalls[0]?.[2]).toBe(draftCalls[1]?.[2]);
    expect(within(card).queryByRole('button', { name: '建立待核准草稿' })).toBeNull();
    expect(mocks.onChanged).toHaveBeenCalledTimes(1);
  });

  it('invalidates the concrete preview after a stale draft conflict', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/candidate-1') ? response(detail('candidate-1')) : response(fixture())));
    mocks.researchMutation.mockResolvedValueOnce({ status: 'preview_only', inputs: { anchors: [] }, calculation: {}, approval_required: true })
      .mockRejectedValueOnce(new Error('research_evidence_changed_review_again'));
    render(<AutomaticWaveCandidates symbol="2330.TW" historical={false} onChanged={mocks.onChanged} />);
    const card = await screen.findByText('匿名候選 1').then(node => node.closest('article')!);
    fireEvent.click(within(card).getByRole('button', { name: '預覽這份錨點假設' }));
    fireEvent.click(await within(card).findByRole('button', { name: '建立待核准草稿' }));
    expect(await within(card).findByText(/資料或核准內容已更新/)).toBeTruthy();
    expect(within(card).queryByRole('button', { name: '建立待核准草稿' })).toBeNull();
    expect(within(card).getByRole('button', { name: '預覽這份錨點假設' })).toBeTruthy();
  });
});
