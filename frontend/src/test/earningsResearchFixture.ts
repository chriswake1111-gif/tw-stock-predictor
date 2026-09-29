import type { EarningsResearchData } from '../components/EarningsResearchPanel';
import { researchGuidanceFixture } from './researchGuidanceFixture';

export function earningsFixture(): EarningsResearchData {
  return { status: 'available', value: '2.75', period_start: '2025-07-01', period_end: '2026-06-30',
    observed_at: '2026-09-28T09:00:00Z', last_checked_at: '2026-09-28T09:00:00Z', last_update_status: 'available',
    snapshot_id: 'synthetic-earnings', parser_version: 'synthetic-parser',
    limitations: ['虛構測試數字；四季合計不等於全年加權計算。'],
    rows: [
      { period_start: '2025-07-01', period_end: '2025-09-30', value: '2.50' },
      { period_start: '2025-10-01', period_end: '2025-12-31', value: '-1.25' },
      { period_start: '2026-01-01', period_end: '2026-03-31', value: '0.00' },
      { period_start: '2026-04-01', period_end: '2026-06-30', value: '1.50' }],
    sources: [{ key: 'fictional-document', url: 'https://example.org/fictional-financial-report.pdf', role: 'basis',
      year: 2026, quarter: 2, sha256: 'b'.repeat(64), observed_at: '2026-09-28T09:00:00Z' }],
    basis: { ledgers: [{ period_start: '2026-01-01', period_end: '2026-06-30', page: 20 }] } };
}

export function earningsReview(partial = false) {
  const review = researchGuidanceFixture();
  review.knowledge_cutoff_at = '2026-09-28T09:05:00Z';
  review.current.summary.knowledge_cutoff_at = review.knowledge_cutoff_at;
  const earnings = earningsFixture();
  if (partial) Object.assign(earnings, { status: 'quality_warning', value: null, reason: 'source_revision_requires_review', last_update_status: 'failed' });
  review.current.summary.public_data = { ...review.current.summary.public_data, VerifiedQuarterlyEarnings: earnings };
  if (!partial) review.guidance!.gaps = review.guidance!.gaps.filter(g => g.id !== 'ttm');
  return review;
}
