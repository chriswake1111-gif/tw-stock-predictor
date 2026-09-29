import { fireEvent, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { AnchorDiagram, WaveQualification } from '../components/WaveAnchorAssist';
import { GuidedResearchWorkspace } from '../components/GuidedResearchWorkspace';
import { readReview, guidanceRead } from '../api/guidanceClient';
import { researchMutation } from '../api/researchClient';
import { renderWithProviders } from './render';
import { researchGuidanceFixture } from './researchGuidanceFixture';
import { waveFixture, anchorFixture } from './waveAssistFixture';

vi.mock('../api/guidanceClient', async original => ({ ...await original<typeof import('../api/guidanceClient')>(), readReview: vi.fn(), guidanceRead: vi.fn() }));
vi.mock('../api/researchClient', async original => ({ ...await original<typeof import('../api/researchClient')>(), researchMutation: vi.fn() }));
beforeEach(() => vi.resetAllMocks());

it('labels a source hypothesis and sorts diagram points without changing the input', () => {
  const input = [...anchorFixture.anchors!].reverse();
  renderWithProviders(<AnchorDiagram anchors={input} ruleId="FB-04" />);
  expect(screen.getByRole('img')).toHaveAccessibleName(/不是完整股價走勢/);
  expect(screen.getAllByRole('listitem')[0]).toHaveTextContent('A · 起點2026-01-02 · 100 元');
  expect(screen.getByText(/確認時點未提供/)).toBeInTheDocument();
  expect(input[0]?.role).toBe('swing_end');
});

it('qualifications explain missing evidence with no adoption or override button', () => {
  renderWithProviders(<WaveQualification data={waveFixture} />);
  expect(screen.getByText(/尚不能直接採用自動錨點/)).toBeInTheDocument();
  expect(screen.getByText(/核准只接受人工假設/)).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

it('renders anchor preview, then rejects a changed candidate without auto approval', async () => {
  const review = researchGuidanceFixture();
  review.guidance!.wave_support = waveFixture; review.guidance!.candidates = [anchorFixture];
  vi.mocked(readReview).mockResolvedValue(review);
  vi.mocked(guidanceRead).mockResolvedValue({ kind: 'anchor', candidate_id: anchorFixture.record_id, values: { rule_id: 'FB-04', anchors: anchorFixture.anchors, source: '原文第三頁', rationale: '人工假設' } });
  vi.mocked(researchMutation).mockResolvedValueOnce({ status: 'preview' }).mockRejectedValueOnce(new Error('research_evidence_changed_review_again'));
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical={false} onUpdate={vi.fn()}>legacy</GuidedResearchWorkspace>);
  fireEvent.click(await screen.findByRole('button', { name: '候選與選擇' }));
  fireEvent.click(screen.getByRole('button', { name: '預覽這份假設' }));
  fireEvent.click(await screen.findByRole('button', { name: '建立待核准草稿' }));
  expect(await screen.findByText(/這份來源已有新版本/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '確認核准此草稿' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '預覽這份假設' })).toBeInTheDocument();
});

it('historical research stays read-only', () => {
  const review = researchGuidanceFixture();
  renderWithProviders(<GuidedResearchWorkspace summary={review.current.summary} historical onUpdate={vi.fn()}>歷史唯讀</GuidedResearchWorkspace>);
  expect(screen.getByText('歷史唯讀')).toBeInTheDocument();
  expect(readReview).not.toHaveBeenCalled();
  expect(researchMutation).not.toHaveBeenCalled();
});
