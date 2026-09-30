import { guidanceRead } from './guidanceClient';

export type WaveQualificationStatus = 'passed' | 'failed' | 'unknown';
export type WaveQualificationOwner = 'program' | 'engineering' | 'assistant' | 'user';
export type WaveQualificationCheck = {
  id: string;
  title: string;
  status: WaveQualificationStatus;
  reason_code: string;
  reason: string;
  impact: string;
  owner: WaveQualificationOwner;
  evidence: { kind: string; reference: string }[];
  counts?: Record<string, number>;
  samples?: string[];
};
export type WaveQualificationSnapshot = {
  snapshot_id: string;
  source: string;
  parser_version: string;
  raw_sha256: string;
  normalized_sha256: string;
  observed_at: string;
  source_url: string;
};
export type WaveQualification = {
  contract_version: 'wave_qualification_v1';
  symbol: string;
  enabled: true;
  checked_at: string;
  knowledge_cutoff_at: string;
  snapshot: WaveQualificationSnapshot | null;
  requested_range: { start: string; end: string } | null;
  actual_range: { start: string; end: string } | null;
  status: 'insufficient_data' | 'quality_warning' | 'unavailable';
  headline: string;
  next_step: { owner: WaveQualificationOwner; action: string };
  checks: WaveQualificationCheck[];
  notices: string[];
  automatic_candidates_eligible: false;
};
export type DisabledWaveQualification = {
  contract_version: 'wave_qualification_v1';
  enabled: false;
  symbol: string;
};
export type WaveQualificationResponse = WaveQualification | DisabledWaveQualification;

const owners: WaveQualificationOwner[] = ['program', 'engineering', 'assistant', 'user'];
const checkStatuses: WaveQualificationStatus[] = ['passed', 'failed', 'unknown'];
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const validDate = (value: unknown) => text(value) && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
const validTimestamp = (value: unknown) => text(value) && Number.isFinite(Date.parse(value));
const validRange = (value: unknown) => value === null || (record(value) && validDate(value.start) && validDate(value.end));
const validSnapshot = (value: unknown) => value === null || (record(value)
  && ['snapshot_id', 'source', 'parser_version', 'source_url'].every(key => text(value[key]))
  && /^[a-f\d]{64}$/i.test(String(value.raw_sha256)) && /^[a-f\d]{64}$/i.test(String(value.normalized_sha256))
  && validTimestamp(value.observed_at));
const validCheck = (value: unknown): value is WaveQualificationCheck => {
  if (!record(value) || !text(value.id) || !text(value.title) || !checkStatuses.includes(value.status as WaveQualificationStatus)
      || !text(value.reason_code) || !text(value.reason) || !text(value.impact) || !owners.includes(value.owner as WaveQualificationOwner)
      || !Array.isArray(value.evidence) || !value.evidence.every(e => record(e) && text(e.kind) && text(e.reference))) return false;
  if (value.counts !== undefined && (!record(value.counts) || !Object.values(value.counts).every(n => typeof n === 'number' && Number.isFinite(n)))) return false;
  return value.samples === undefined || (Array.isArray(value.samples) && value.samples.every(text));
};

function validateResponse(value: unknown, expectedSymbol: string): WaveQualificationResponse {
  if (!record(value) || value.contract_version !== 'wave_qualification_v1' || value.symbol !== expectedSymbol || typeof value.enabled !== 'boolean') {
    throw new Error('wave_qualification_contract_invalid');
  }
  if (value.enabled === false) {
    if (Object.keys(value).sort().join(',') !== 'contract_version,enabled,symbol') throw new Error('wave_qualification_contract_invalid');
    return value as DisabledWaveQualification;
  }
  if (!validTimestamp(value.checked_at) || !validTimestamp(value.knowledge_cutoff_at) || !validSnapshot(value.snapshot)
      || !validRange(value.requested_range) || !validRange(value.actual_range)
      || !['insufficient_data', 'quality_warning', 'unavailable'].includes(String(value.status))
      || !text(value.headline) || !record(value.next_step) || !owners.includes(value.next_step.owner as WaveQualificationOwner)
      || !text(value.next_step.action) || !Array.isArray(value.checks) || !value.checks.every(validCheck)
      || !Array.isArray(value.notices) || !value.notices.every(text) || value.automatic_candidates_eligible !== false) {
    throw new Error('wave_qualification_contract_invalid');
  }
  return value as WaveQualification;
}

export async function readWaveQualification(symbol: string, signal: AbortSignal): Promise<WaveQualificationResponse> {
  const payload = await guidanceRead<unknown>(`/api/v2/research/wave-qualification/${encodeURIComponent(symbol)}`, signal, 'no-store');
  return validateResponse(payload, symbol);
}
