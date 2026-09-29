import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { EarningsResearchPanel } from '../components/EarningsResearchPanel';
import { GuidedResearchWorkspace } from '../components/GuidedResearchWorkspace';
import { readReview } from '../api/guidanceClient';
import { researchMutation } from '../api/researchClient';
import { earningsFixture, earningsReview, earningsCoverageGap } from './earningsResearchFixture';
import { renderWithProviders } from './render';

vi.mock('../api/guidanceClient', async original => ({ ...await original<typeof import('../api/guidanceClient')>(), readReview: vi.fn() }));
vi.mock('../api/researchClient', async original => ({ ...await original<typeof import('../api/researchClient')>(), researchMutation: vi.fn() }));
beforeEach(() => vi.resetAllMocks());

it('shows an investigated source gap without promoting the review to financial data', () => {
  renderWithProviders(<EarningsResearchPanel data={earningsCoverageGap()} />);
  expect(screen.getByRole('heading', { name: '已查到什麼、還缺什麼' })).toBeInTheDocument();
  expect(screen.getByText(/2025 年第四季仍缺直接單季來源/)).toBeInTheDocument();
  expect(screen.getByText(/由助理查找原始明細/)).toBeInTheDocument();
  expect(screen.getByText(/2026\/09\/29 22:51/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  expect(researchMutation).not.toHaveBeenCalled();
});

it.each([
  ['earnings_source_access_denied', '暫時拒絕程式讀取'],
  ['earnings_source_rate_limited', '暫時限制請求次數'],
  ['earnings_source_not_found', '原始文件連結目前不存在'],
  ['earnings_source_parse_failed', '程式未能完成格式核對'],
])('explains %s without claiming successful refresh', (reason, message) => {
  renderWithProviders(<EarningsResearchPanel data={{ ...earningsFixture(), status: 'quality_warning', value: null, reason }} />);
  expect(screen.getByText(new RegExp(message))).toBeInTheDocument();
  expect(screen.getAllByText(/保留的原季度資料/)).toHaveLength(4);
  expect(screen.queryByText('2.75 元／股')).not.toBeInTheDocument();
});

it('shows the server sum, period, negative and zero quarters without calculating or approving', () => {
  renderWithProviders(<EarningsResearchPanel data={earningsFixture()} />);
  expect(screen.getByText('2.75 元／股')).toBeInTheDocument();
  expect(screen.getByText(/-1.25 元／股/)).toBeInTheDocument();
  expect(screen.getByText(/0.00 元／股/)).toBeInTheDocument();
  expect(screen.getByText(/這不是未來一年的獲利預估/)).toBeInTheDocument();
  expect(screen.getByText(/2026\/09\/28 17:00/)).toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(researchMutation).not.toHaveBeenCalled();
});

it('retains the unadopted source version visibly without a calculation or write', () => {
  const data = { ...earningsFixture(), status: 'quality_warning', value: null,
    reason: 'source_revision_requires_review', unreviewed_source_versions: [{ key: 'new-version',
      url: 'https://example.org/report', sha256: 'c'.repeat(64), observed_at: '2026-09-28T10:00:00Z' }] };
  renderWithProviders(<EarningsResearchPanel data={data} />);
  expect(screen.getByText(/來源版本尚未採用/)).toBeInTheDocument();
  expect(screen.getByText(/2026\/09\/28 18:00/)).toBeInTheDocument();
  expect(researchMutation).not.toHaveBeenCalled();
});

it.each([
  { status: 'quality_warning', value: null, reason: 'source_revision_requires_review' },
  { status: 'stale', value: null, reason: 'earnings_period_requires_refresh' },
  { status: 'available', value: '2.75', last_update_status: 'failed' },
  { status: 'available', value: '2.75', is_stale: true },
])('does not present a stale or failed sum as currently available: %j', changes => {
  renderWithProviders(<EarningsResearchPanel data={{ ...earningsFixture(), ...changes }} />);
  expect(screen.queryByText('2.75 元／股')).not.toBeInTheDocument();
  expect(screen.getByText(/目前不能合計/)).toBeInTheDocument();
  expect(screen.getAllByText(/保留的原季度資料/)).toHaveLength(4);
});

it('treats source text as inert and does not link a script URL', () => {
  const data = earningsFixture(); data.sources![0]!.url = 'javascript:fetch("/approve")';
  data.limitations = ['<script>保存並核准</script>'];
  const { container } = renderWithProviders(<EarningsResearchPanel data={data} />);
  expect(container.querySelector('script')).toBeNull();
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  expect(screen.getByText('<script>保存並核准</script>')).toBeInTheDocument();
});

it('includes the exact source snapshot in the save preview, with separate explicit save', async () => {
  const review = earningsReview(); vi.mocked(readReview).mockResolvedValue(review);
  vi.mocked(researchMutation).mockResolvedValue({ entry_id: 'synthetic-saved' });
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  expect(await screen.findByText(/最近四季獲利：2.75/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '預覽將保存的內容' }));
  await screen.findByRole('heading', { name: '請確認這份部分研究' });
  const preview = screen.getByRole('region', { name: '最近四季基本每股盈餘合計' });
  expect(within(preview).getByText('2.75 元／股')).toBeInTheDocument();
  expect(within(preview).getByText(/synthetic-earnings/)).toBeInTheDocument();
  expect(researchMutation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '確認保存這份部分研究' }));
  await waitFor(() => expect(researchMutation).toHaveBeenCalledTimes(1));
  expect(vi.mocked(researchMutation).mock.calls[0]![1]).toMatchObject({ expected_content_fingerprint: review.content_fingerprint });
});

it('shows both old and revised earnings on preview conflict and keeps the user note', async () => {
  const review = earningsReview(); const changed = earningsReview(true);
  changed.review_revision_fingerprint = 'c'.repeat(64);
  vi.mocked(readReview).mockResolvedValueOnce(review).mockResolvedValue(changed);
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '保留我的筆記' } });
  fireEvent.click(screen.getByRole('button', { name: '預覽將保存的內容' }));
  await screen.findByRole('heading', { name: '預覽內容已改變' });
  fireEvent.click(screen.getByText('比較原預覽與最新依據'));
  expect(screen.getByText('2.75 元／股')).toBeInTheDocument();
  expect(screen.getByText(/來源內容已有變動/)).toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('保留我的筆記');
  expect(researchMutation).not.toHaveBeenCalled();
});
