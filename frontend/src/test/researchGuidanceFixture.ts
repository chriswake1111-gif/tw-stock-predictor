import type { Review, Evidence } from '../api/guidanceClient';
import { guidedResearchFixture } from './guidedResearchFixture';

// Synthetic and anonymous, imported only by tests.
export const evidenceFixture: Evidence = {
  record_id: 'evidence_fixture', revision: 1, recorded_at: '2026-09-01T00:00:00Z',
  kind: 'candidate', topic: 'pe', fiscal_year: 2026, title: '匿名來源估值情境',
  summary: '來源明示同年度二十倍及可比較公司。', interpretation: '預估仍可能修訂。',
  source_url: 'https://example.org/report', publisher: '測試機構', published_date: '2026-09-01',
  locator: '第三頁', reading_scope: '估值段落', source_type: 'original', review_status: 'limited',
  limitations: '獲利尚未實現', recheck_when: '新財報公布', basis: '2026 全年獲利', value: 20,
  unit: 'multiple', lookup_scope: '', lookup_outcome: null,
};

export function researchGuidanceFixture(): Review {
  const summary = guidedResearchFixture();
  summary.short_name = '研究示例公司';
  summary.company_name = '匿名研究示例';
  summary.valuation_context = { status: 'needs_human_judgment', reason_code: 'pe_missing', target_matrix: [] };
  const gap = { id: 'pe', title: '同年度估值倍數待查證', owner: 'assistant' as const,
    impact: '尚不能建立完整估值；可以先閱讀或保存部分研究。', action: '交給助理查證' };
  return {
    symbol: summary.canonical_symbol, knowledge_cutoff_at: summary.knowledge_cutoff_at,
    content_fingerprint: 'a'.repeat(64), review_revision_fingerprint: 'b'.repeat(64),
    current: { summary }, assumptions: [], previous: null,
    comparison: { status: 'no_previous', facts: [], assumptions_changed: false },
    guidance: { contract_version: 'research_guidance_v1', selected_year: 2026, available_years: [2026],
      gaps: [gap, { id: 'ttm', title: '過去一年獲利尚不能可靠合計', owner: 'engineering',
        impact: '股數口徑尚未核對，不能由核准代替資料驗證。', action: '先閱讀已公布財報' }],
      next_step: gap, data_readiness: 'partial', finding: '估值仍待資料與適用依據', candidates: [],
      evidence: [], evidence_next_cursor: null, approved_assumptions: [],
      assistant_request: '請查證研究示例公司的同年度估值來源；不要代為核准或保存。',
      note_draft: '', note_draft_stale: false },
  };
}
