import type { ResearchSummaryResponse } from './types';
import type { Assumption } from '../components/LocalAssumptionEditor';

export type Evidence = {
  rule_id?: 'FB-03' | 'FB-04' | null;
  anchors?: AnchorPoint[];
  record_id: string; revision: number; recorded_at: string; kind: 'candidate' | 'lookup' | 'brief';
  topic: 'eps' | 'pe' | 'anchor' | 'general'; fiscal_year: number | null; title: string; summary: string;
  interpretation: string; source_url: string; publisher: string; published_date: string | null;
  locator: string; reading_scope: string; source_type: string; review_status: string;
  limitations: string; recheck_when: string; basis: string; value: number | null; unit: string;
  lookup_scope: string; lookup_outcome: string | null;
};
export type AnchorPoint = { role: string; market_date: string; price: number };
export type WaveSupport = {
  contract_version: 'wave_anchor_guidance_v1'; status: string; price_basis: string; source: string;
  first_date: string | null; last_date: string | null; observed_at: string | null; checked_at: string | null;
  blockers: string[]; next_action: string; confirmation_policy: string; approval_policy: string;
  automatic_candidates_eligible: false; snapshot_id: string | null; source_sha256: string | null;
};
export type Gap = { id: string; title: string; owner: 'program' | 'assistant' | 'user' | 'engineering'; impact: string; action: string; reason?: string };
export type Guidance = {
  wave_support?: WaveSupport | null;
  contract_version: 'research_guidance_v1'; selected_year: number | null; available_years: number[];
  gaps: Gap[]; next_step: Gap; data_readiness: string; finding: string; candidates: Evidence[];
  evidence: Evidence[]; evidence_next_cursor: string | null; approved_assumptions: Assumption[];
  assistant_request: string; note_draft: string; note_draft_stale: boolean;
  assumption_evidence?: { assumption_id: string; approval: { decision?: string } | null; evidence: Evidence }[];
};
export type Review = {
  symbol: string; knowledge_cutoff_at: string; content_fingerprint: string; review_revision_fingerprint: string;
  current: { summary: ResearchSummaryResponse }; assumptions: Assumption[]; guidance?: Guidance;
  previous: { entry_id: string; created_at: string; note: string; summary: ResearchSummaryResponse } | null;
  comparison: { assumptions_changed: boolean; status: string; facts: { field: string; before: { value: number | null; date: string }; after: { value: number | null; date: string }; delta: number | null; status: string }[] };
};
export async function guidanceRead<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, credentials: 'same-origin' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { detail?: unknown };
    const known = ['research_evidence_changed_review_again', 'evidence_not_selectable', 'candidate_requires_concise_evidence_revision'];
    if (typeof error.detail === 'string' && known.includes(error.detail)) throw new Error(error.detail);
    throw new Error('無法讀取研究引導，請重試；既有研究仍保留。');
  }
  return response.json() as Promise<T>;
}
export function readReview(symbol: string, year?: number, signal?: AbortSignal) {
  return guidanceRead<Review>(`/api/v2/research/journal/${encodeURIComponent(symbol)}/preview${year ? `?research_year=${year}` : ''}`, signal);
}
export const evidenceLabels: Record<string, string> = { reviewable: '可供審閱', limited: '有限制，請先閱讀', lead: '僅線索', not_applicable: '不適用' };
export const topicLabels: Record<string, string> = { eps: '全年預估每股盈餘', pe: '估值本益比', anchor: '波浪錨點', general: '研究資料' };
