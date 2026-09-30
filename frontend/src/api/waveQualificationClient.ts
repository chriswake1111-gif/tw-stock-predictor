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
export type WaveSessionCoverageStatus = 'available' | 'partial' | 'missing' | 'unavailable';
export type WaveSessionCoverageSource = {
  source_id: string;
  label: string;
  status: 'accepted' | 'partial' | 'failed' | 'revoked';
  start: string;
  end: string;
  fetched_at: string;
  reference: string;
  url: string;
  limitations: string[];
};
export type WaveSessionCoverage = {
  contract_version: 'wave_session_coverage_v1';
  mode: 'retrospective_local';
  status: WaveSessionCoverageStatus;
  requested_range: { start: string; end: string };
  checked_at: string;
  known_at: string | null;
  historical_availability: 'not_asserted';
  counts: Record<'interval_days' | 'market_open' | 'market_closed' | 'stock_traded' | 'suspended' | 'outside_listing' | 'intraday' | 'missing' | 'unknown' | 'conflicts', number>;
  samples: Array<{ date: string; kind: string; reason: string }>;
  sources: WaveSessionCoverageSource[];
  next_step: { owner: WaveQualificationOwner; action: string };
  assistant_request: string;
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
  session_coverage?: WaveSessionCoverage;
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
const validDate = (value: unknown): value is string => text(value) && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
const validTimestamp = (value: unknown): value is string => text(value) && Number.isFinite(Date.parse(value));
const validRange = (value: unknown) => value === null || (record(value) && validDate(value.start) && validDate(value.end));
const sessionCoverageCountKeys = ['interval_days', 'market_open', 'market_closed', 'stock_traded', 'suspended', 'outside_listing', 'intraday', 'missing', 'unknown', 'conflicts'] as const;
const validSessionCoverage = (value: unknown): value is WaveSessionCoverage => {
  if (!record(value) || value.contract_version !== 'wave_session_coverage_v1' || value.mode !== 'retrospective_local'
      || !['available', 'partial', 'missing', 'unavailable'].includes(String(value.status))
      || !validTimestamp(value.checked_at) || !(value.known_at === null || validTimestamp(value.known_at))
      || value.historical_availability !== 'not_asserted' || !Array.isArray(value.samples) || value.samples.length > 8
      || !Array.isArray(value.sources) || value.sources.length > 12 || !record(value.next_step)
      || !owners.includes(value.next_step.owner as WaveQualificationOwner) || !text(value.next_step.action)
      || !text(value.assistant_request)) return false;
  const requestedRange = value.requested_range;
  if (!record(requestedRange) || !validDate(requestedRange.start) || !validDate(requestedRange.end) || requestedRange.start > requestedRange.end) return false;
  const counts = value.counts;
  if (!record(counts) || Object.keys(counts).sort().join(',') !== [...sessionCoverageCountKeys].sort().join(',')
      || !sessionCoverageCountKeys.every(key => typeof counts[key] === 'number' && Number.isInteger(counts[key]) && (counts[key] as number) >= 0)) return false;
  if (!value.samples.every(sample => record(sample) && validDate(sample.date) && text(sample.kind) && text(sample.reason))) return false;
  if (!value.sources.every(source => record(source) && text(source.source_id) && text(source.label)
      && ['accepted', 'partial', 'failed', 'revoked'].includes(String(source.status))
      && validDate(source.start) && validDate(source.end) && source.start <= source.end && validTimestamp(source.fetched_at)
      && text(source.reference) && text(source.url) && Array.isArray(source.limitations) && source.limitations.every(text))) return false;
  return true;
};
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
  if (value.session_coverage !== undefined && !validSessionCoverage(value.session_coverage)) throw new Error('wave_qualification_contract_invalid');
  return value as WaveQualification;
}

export async function readWaveQualification(symbol: string, signal: AbortSignal): Promise<WaveQualificationResponse> {
  const payload = await guidanceRead<unknown>(`/api/v2/research/wave-qualification/${encodeURIComponent(symbol)}`, signal, 'no-store');
  return validateResponse(payload, symbol);
}
