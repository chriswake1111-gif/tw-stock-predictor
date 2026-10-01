import { researchMutation } from './researchClient';

export type WaveCandidateStatus = 'available' | 'insufficient_data' | 'unavailable' | 'unsupported';
export type WaveCandidateCheckStatus = 'passed' | 'failed' | 'unknown';
export interface WaveCandidateAnchor {
  role: 'origin' | 'swing_end'; market_date: string; price: number; raw_price: number;
  adjustment_factor: number; confirmed_at: string;
}
export interface WaveCandidate {
  candidate_id: string; title: string; anchors: WaveCandidateAnchor[]; confirmed_at: string;
  known_at: string | null; source_summary: string; limitations: string[];
}
export interface WaveCandidatesResponse {
  contract_version: 'wave_candidates_v1'; symbol: string; enabled: boolean;
  status: WaveCandidateStatus; headline: string; checked_at: string;
  requested_range: { start: string; end: string } | null;
  actual_range: { start: string; end: string } | null;
  package_ref: string | null; known_at: string | null; basis: { label: string; anchor_date: string } | null;
  limitations: string[]; checks: Array<{ id: string; title: string; status: WaveCandidateCheckStatus; reason: string }>;
  candidates: WaveCandidate[];
}
export interface WaveCandidateAnchorPreview {
  kind: 'anchor'; values: Record<string, unknown>; candidate_id: string; approval_required: true;
}

const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= 4000;
const nullableText = (v: unknown): v is string | null => v === null || text(v);
const timestamp = (v: unknown): v is string => text(v) && !Number.isNaN(Date.parse(v));
const nullableTimestamp = (v: unknown): v is string | null => v === null || timestamp(v);
const date = (v: unknown): v is string => {
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const parsed = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === v;
};
const range = (v: unknown): v is { start: string; end: string } | null => v === null || (!!v && typeof v === 'object' && date((v as {start?: unknown}).start) && date((v as {end?: unknown}).end) && (v as {start:string}).start <= (v as {end:string}).end);
const listOfText = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 100 && v.every(text);
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

function validAnchor(v: unknown): v is WaveCandidateAnchor {
  if (!record(v)) return false;
  return (v.role === 'origin' || v.role === 'swing_end') && date(v.market_date)
    && typeof v.price === 'number' && Number.isFinite(v.price) && v.price > 0
    && typeof v.raw_price === 'number' && Number.isFinite(v.raw_price) && v.raw_price > 0
    && typeof v.adjustment_factor === 'number' && Number.isFinite(v.adjustment_factor) && v.adjustment_factor > 0
    && date(v.confirmed_at) && v.confirmed_at >= v.market_date;
}

function validCandidate(v: unknown): v is WaveCandidate {
  if (!record(v)) return false;
  if (!text(v.candidate_id) || !text(v.title) || !Array.isArray(v.anchors) || v.anchors.length !== 2
    || !v.anchors.every(validAnchor) || !date(v.confirmed_at) || !nullableTimestamp(v.known_at)
    || !text(v.source_summary) || !listOfText(v.limitations)) return false;
  const origin = v.anchors[0] as WaveCandidateAnchor;
  const end = v.anchors[1] as WaveCandidateAnchor;
  return origin.role === 'origin' && end.role === 'swing_end'
    && origin.market_date < end.market_date && origin.price < end.price
    && v.confirmed_at >= end.confirmed_at
    && (v.known_at === null || Date.parse(v.known_at as string) >= Date.parse(`${v.confirmed_at}T00:00:00Z`));
}

export function parseWaveCandidates(value: unknown, expectedSymbol?: string): WaveCandidatesResponse {
  if (!record(value) || value.contract_version !== 'wave_candidates_v1' || !text(value.symbol)
    || (expectedSymbol && value.symbol !== expectedSymbol) || typeof value.enabled !== 'boolean'
    || !['available', 'insufficient_data', 'unavailable', 'unsupported'].includes(String(value.status))
    || !text(value.headline) || !timestamp(value.checked_at) || !range(value.requested_range) || !range(value.actual_range)
    || !nullableText(value.package_ref) || !nullableTimestamp(value.known_at) || !listOfText(value.limitations)
    || !Array.isArray(value.checks) || value.checks.length > 100
    || !value.checks.every((c: unknown) => record(c) && text(c.id) && text(c.title)
      && ['passed', 'failed', 'unknown'].includes(String(c.status)) && text(c.reason))
    || !Array.isArray(value.candidates) || value.candidates.length > 3 || !value.candidates.every(validCandidate)) {
    throw new Error('wave_candidates_contract_invalid');
  }
  const basis = value.basis;
  if (basis !== null && (!record(basis) || !text(basis.label) || !date(basis.anchor_date))) {
    throw new Error('wave_candidates_contract_invalid');
  }
  if (value.status === 'available') {
    if (!record(basis) || !range(value.requested_range) || value.requested_range === null
      || !range(value.actual_range) || value.actual_range === null || !text(value.package_ref)
      || !timestamp(value.known_at)
      || !(value.candidates as WaveCandidate[]).every(candidate => timestamp(candidate.known_at)
        && Date.parse(candidate.known_at) >= Date.parse(`${candidate.confirmed_at}T00:00:00Z`))) {
      throw new Error('wave_candidates_contract_invalid');
    }
    const latestConfirmed = (value.candidates as WaveCandidate[]).reduce((latest, candidate) => candidate.confirmed_at > latest ? candidate.confirmed_at : latest, '0000-00-00');
    if (latestConfirmed !== '0000-00-00' && Date.parse(value.known_at) < Date.parse(`${latestConfirmed}T00:00:00Z`)) {
      throw new Error('wave_candidates_contract_invalid');
    }
  }
  return value as unknown as WaveCandidatesResponse;
}

async function readJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { method: 'GET', cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' }, signal });
  if (!response.ok) throw new Error(`wave_candidates_read_error:${response.status}`);
  return await response.json() as T;
}

export async function readWaveCandidates(symbol: string, signal: AbortSignal): Promise<WaveCandidatesResponse> {
  const raw = await readJson<unknown>(`/api/v2/research/wave-candidates/${encodeURIComponent(symbol)}`, signal);
  return parseWaveCandidates(raw, symbol);
}

export async function readWaveCandidatePreview(symbol: string, candidateId: string, signal: AbortSignal): Promise<WaveCandidateAnchorPreview> {
  const path = `/api/v2/research/wave-candidates/${encodeURIComponent(symbol)}/${encodeURIComponent(candidateId)}`;
  const value = await readJson<unknown>(path, signal);
  if (!record(value) || value.kind !== 'anchor' || value.candidate_id !== candidateId || value.approval_required !== true || !record(value.values)) {
    throw new Error('wave_candidate_preview_contract_invalid');
  }
  return value as unknown as WaveCandidateAnchorPreview;
}

export function previewWaveCandidate(symbol: string, payload: WaveCandidateAnchorPreview): Promise<unknown> {
  return researchMutation<unknown>(`/api/v2/research/assumptions/${encodeURIComponent(symbol)}/anchor/preview`, {
    values: payload.values, candidate_id: payload.candidate_id,
  });
}

export function createWaveCandidateDraft(symbol: string, payload: WaveCandidateAnchorPreview, idempotencyKey: string) {
  return researchMutation(`/api/v2/research/assumptions/${encodeURIComponent(symbol)}/anchor/draft`, {
    values: payload.values, candidate_id: payload.candidate_id,
  }, idempotencyKey);
}
