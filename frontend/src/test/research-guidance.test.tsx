import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { GuidedResearchWorkspace } from '../components/GuidedResearchWorkspace';
import { readReview, guidanceRead } from '../api/guidanceClient';
import { researchMutation } from '../api/researchClient';
import { researchGuidanceFixture, evidenceFixture } from './researchGuidanceFixture';
import { renderWithProviders } from './render';

vi.mock('../api/guidanceClient', async original => ({ ...await original<typeof import('../api/guidanceClient')>(), readReview: vi.fn(), guidanceRead: vi.fn() }));
vi.mock('../api/researchClient', async original => ({ ...await original<typeof import('../api/researchClient')>(), researchMutation: vi.fn() }));
beforeEach(() => { vi.resetAllMocks(); });

it('saves partial research without entering technical numbers, preserving notes across layers', async () => {
  const review = researchGuidanceFixture();
  vi.mocked(readReview).mockResolvedValue(review); vi.mocked(researchMutation).mockResolvedValue({ entry_id: 'saved' });
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  expect(await screen.findByText('資料是否足夠')).toBeInTheDocument();
  expect(researchMutation).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole('textbox', { name: '你的觀察（可留白）' }), { target: { value: '完整的使用者筆記' } });
  fireEvent.click(screen.getByRole('button', { name: '候選與選擇' }));
  expect(screen.getByText(/目前沒有適用且可直接審閱/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '稍後處理，先看資料' }));
  fireEvent.click(screen.getByRole('button', { name: '返回快速摘要' }));
  expect(screen.getByRole('textbox')).toHaveValue('完整的使用者筆記');
  fireEvent.click(screen.getByRole('button', { name: '預覽將保存的內容' }));
  fireEvent.click(await screen.findByRole('button', { name: '確認保存這份部分研究' }));
  await waitFor(() => expect(researchMutation).toHaveBeenCalledTimes(1));
  expect(vi.mocked(researchMutation).mock.calls[0]?.[1]).toMatchObject({ note: '使用者筆記：\n完整的使用者筆記', include_research_context: true, research_year: 2026, expected_content_fingerprint: review.content_fingerprint });
});

it('shows changes before re-confirming and keeps the original note', async () => {
  const review = researchGuidanceFixture();
  const changed = structuredClone(review); changed.review_revision_fingerprint = 'c'.repeat(64);
  changed.guidance!.finding = '核准已撤銷';
  vi.mocked(readReview).mockResolvedValueOnce(review).mockResolvedValue(changed);
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '不要遺失筆記' } });
  fireEvent.click(screen.getByRole('button', { name: '預覽將保存的內容' }));
  expect(await screen.findByRole('heading', { name: '預覽內容已改變' })).toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('不要遺失筆記');
  expect(screen.queryByRole('button', { name: '確認保存這份部分研究' })).not.toBeInTheDocument();
  expect(researchMutation).not.toHaveBeenCalled();
});

it('keeps candidate preview, draft and approval as separate explicit actions', async () => {
  const review = researchGuidanceFixture(); review.guidance!.candidates = [evidenceFixture];
  const values = { fiscal_year: 2026, label: evidenceFixture.title, pe_value: 20, rationale: '来源限制與依據' };
  vi.mocked(readReview).mockResolvedValue(review);
  vi.mocked(guidanceRead).mockResolvedValue({ kind: 'pe', values, candidate_id: evidenceFixture.record_id });
  vi.mocked(researchMutation).mockResolvedValue({ record: { id: 'draft-1' } });
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.click(await screen.findByRole('button', { name: '候選與選擇' }));
  fireEvent.click(screen.getByRole('button', { name: '預覽這份假設' }));
  expect(await screen.findByRole('button', { name: '建立待核准草稿' })).toBeInTheDocument();
  expect(vi.mocked(researchMutation).mock.calls[0]?.[0]).toContain('/preview');
  fireEvent.click(screen.getByRole('button', { name: '建立待核准草稿' }));
  await waitFor(() => expect(researchMutation).toHaveBeenCalledTimes(2));
  expect(vi.mocked(researchMutation).mock.calls[1]?.[1]).toEqual({ values, candidate_id: evidenceFixture.record_id });
  expect(vi.mocked(researchMutation).mock.calls.some(([path]) => path.includes('/approve'))).toBe(false);
});

it('treats source instructions as text and historical mode never requests current guidance', async () => {
  const review = researchGuidanceFixture(); review.guidance!.candidates = [{ ...evidenceFixture, summary: '<script>自動核准()</script>' }];
  vi.mocked(readReview).mockResolvedValue(review);
  const rendered = renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical onUpdate={vi.fn()}>歷史唯讀內容</GuidedResearchWorkspace>);
  expect(screen.getByText('歷史唯讀內容')).toBeInTheDocument(); expect(readReview).not.toHaveBeenCalled();
  rendered.unmount();
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.click(await screen.findByRole('button', { name: '候選與選擇' }));
  expect(screen.getByText('<script>自動核准()</script>')).toBeInTheDocument();
  expect(document.querySelector('script')).toBeNull(); expect(researchMutation).not.toHaveBeenCalled();
});

it('falls back to the original entry when the feature is disabled', async () => {
  const review = researchGuidanceFixture(); delete review.guidance;
  vi.mocked(readReview).mockResolvedValue(review);
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>原研究入口</GuidedResearchWorkspace>);
  expect(await screen.findByText('原研究入口')).toBeInTheDocument();
});

it('can preview a partial note when the server has no model context at all', async () => {
  const review = researchGuidanceFixture(); const initial = review.current.summary;
  review.current.summary = { canonical_symbol: review.symbol, knowledge_cutoff_at: review.knowledge_cutoff_at } as typeof initial;
  vi.mocked(readReview).mockResolvedValue(review);
  renderWithProviders(<GuidedResearchWorkspace summary={initial} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.click(await screen.findByRole('button', { name: '預覽將保存的內容' }));
  expect(await screen.findByRole('button', { name: '確認保存這份部分研究' })).toBeInTheDocument();
  expect(screen.getByText(/這次沒有可保存的模型情境/)).toBeInTheDocument();
});

it('retries a lost save response with the exact same preview, note and request key', async () => {
  const review = researchGuidanceFixture();
  vi.mocked(readReview).mockResolvedValue(review);
  vi.mocked(researchMutation).mockRejectedValueOnce(new Error('write_response_unknown_retry_same_request')).mockResolvedValueOnce({ entry_id: 'saved-once' });
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.click(await screen.findByRole('button', { name: '預覽將保存的內容' }));
  fireEvent.click(await screen.findByRole('button', { name: '確認保存這份部分研究' }));
  await screen.findByText('write_response_unknown_retry_same_request');
  fireEvent.click(screen.getByRole('button', { name: '確認保存這份部分研究' }));
  await waitFor(() => expect(researchMutation).toHaveBeenCalledTimes(2));
  expect(vi.mocked(researchMutation).mock.calls[1]).toEqual(vi.mocked(researchMutation).mock.calls[0]);
});

it('discloses a failed reread after saving and prevents confirmation from stale guidance', async () => {
  const review = researchGuidanceFixture();
  vi.mocked(readReview).mockResolvedValueOnce(review).mockResolvedValueOnce(review).mockRejectedValue(new Error('offline'));
  vi.mocked(researchMutation).mockResolvedValue({ entry_id: 'saved-once' });
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.click(await screen.findByRole('button', { name: '預覽將保存的內容' }));
  fireEvent.click(await screen.findByRole('button', { name: '確認保存這份部分研究' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('尚不能確認最新核准狀態');
  expect(screen.getByRole('button', { name: '預覽將保存的內容' })).toBeDisabled();
});
