import type {
  AnalysisResponse,
  CaptureMode,
  EvaluationResultsResponse,
  EvaluationRunDetailResponse,
  EvaluationRunListResponse,
  MarketOverviewResponse,
  PerformanceSummaryResponse,
  RuleRegistryResponse,
  SnapshotDetailResponse,
  SnapshotDependencyStatusResponse,
  SnapshotListResponse,
  SnapshotComparisonResponse,
  ResearchQueueDetail,
  ResearchQueueResponse,
  UniverseListResponse,
  UniverseResponse,
  EodCloseContextResponse,
} from "./types";

type QueryValue = string | number | boolean | null | undefined;

function withQuery(path: string, query: Record<string, QueryValue> = {}): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  });
  const suffix = params.toString();
  return suffix ? `${path}?${suffix}` : path;
}

async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw new Error(`read_api_error:${response.status}`);
  }
  return (await response.json()) as T;
}

export const evidenceApi = {
  analysis(symbol: string, knowledgeCutoffAt?: string, signal?: AbortSignal) {
    return fetchJson<AnalysisResponse>(
      withQuery(`/api/v2/analysis/${encodeURIComponent(symbol)}`, {
        knowledge_cutoff_at: knowledgeCutoffAt,
      }),
      signal,
    );
  },
  marketOverview(knowledgeCutoffAt?: string, signal?: AbortSignal) {
    return fetchJson<MarketOverviewResponse>(
      withQuery("/api/v2/market-overview", {
        knowledge_cutoff_at: knowledgeCutoffAt,
      }),
      signal,
    );
  },
  snapshots(
    filters: {
      symbol?: string;
      captureMode?: CaptureMode;
      before?: string;
      limit?: number;
    } = {},
    signal?: AbortSignal,
  ) {
    return fetchJson<SnapshotListResponse>(
      withQuery("/api/v2/analysis/snapshots", {
        symbol: filters.symbol,
        capture_mode: filters.captureMode,
        before: filters.before,
        limit: filters.limit,
      }),
      signal,
    );
  },
  snapshot(snapshotId: string, signal?: AbortSignal) {
    return fetchJson<SnapshotDetailResponse>(
      `/api/v2/analysis/snapshots/${encodeURIComponent(snapshotId)}`,
      signal,
    );
  },
  snapshotDependencyStatus(snapshotId: string, signal?: AbortSignal) {
    return fetchJson<SnapshotDependencyStatusResponse>(
      `/api/v2/analysis/snapshots/${encodeURIComponent(snapshotId)}/dependency-status`,
      signal,
    );
  },
  compareSnapshots(
    baseSnapshotId: string,
    comparisonSnapshotId: string,
    comparisonCutoff: string,
    signal?: AbortSignal,
  ) {
    return fetchJson<SnapshotComparisonResponse>(
      withQuery("/api/v2/analysis/snapshots/compare", {
        base_snapshot_id: baseSnapshotId,
        comparison_snapshot_id: comparisonSnapshotId,
        comparison_cutoff: comparisonCutoff,
      }),
      signal,
    );
  },
  evaluationRuns(before?: string, limit = 50, signal?: AbortSignal) {
    return fetchJson<EvaluationRunListResponse>(
      withQuery("/api/v2/evaluations/runs", { before, limit }),
      signal,
    );
  },
  evaluationRun(runId: string, signal?: AbortSignal) {
    return fetchJson<EvaluationRunDetailResponse>(
      `/api/v2/evaluations/runs/${encodeURIComponent(runId)}`,
      signal,
    );
  },
  evaluationResults(runId: string, signal?: AbortSignal) {
    return fetchJson<EvaluationResultsResponse>(
      `/api/v2/evaluations/runs/${encodeURIComponent(runId)}/results`,
      signal,
    );
  },
  performanceSummary(runId: string, signal?: AbortSignal) {
    return fetchJson<PerformanceSummaryResponse>(
      withQuery("/api/v2/performance/summary", { evaluation_run_id: runId }),
      signal,
    );
  },
  rules(signal?: AbortSignal) {
    return fetchJson<RuleRegistryResponse>("/api/v2/model-rules", signal);
  },
  researchQueue(comparisonCutoff: string, includeArchived = false, signal?: AbortSignal) {
    return fetchJson<ResearchQueueResponse>(withQuery("/api/v2/research/queue", {
      comparison_cutoff: comparisonCutoff, include_archived: includeArchived, order: "symbol",
    }), signal);
  },
  researchQueueDetail(itemId: string, comparisonCutoff: string, signal?: AbortSignal) {
    return fetchJson<ResearchQueueDetail>(withQuery(
      `/api/v2/research/queue/${encodeURIComponent(itemId)}`,
      { comparison_cutoff: comparisonCutoff },
    ), signal);
  },
  universeInstrument(canonicalSymbol: string, knowledgeCutoffAt: string, signal?: AbortSignal) {
    return fetchJson<UniverseResponse>(withQuery(
      `/api/v2/universe/instruments/${encodeURIComponent(canonicalSymbol)}`,
      { knowledge_cutoff_at: knowledgeCutoffAt },
    ), signal);
  },
  universeResolve(officialCode: string, venue: string, knowledgeCutoffAt: string, signal?: AbortSignal) {
    return fetchJson<UniverseResponse>(withQuery("/api/v2/universe/resolve", {
      official_code: officialCode, venue, knowledge_cutoff_at: knowledgeCutoffAt,
    }), signal);
  },
  universeInstruments(knowledgeCutoffAt: string, filters: { query?: string; venue?: string; listingStatus?: string; limit?: number } = {}, signal?: AbortSignal) {
    return fetchJson<UniverseListResponse>(withQuery("/api/v2/universe/instruments", {
      knowledge_cutoff_at: knowledgeCutoffAt, query: filters.query, venue: filters.venue,
      listing_status: filters.listingStatus, limit: filters.limit,
    }), signal);
  },
  eodCloseCurrent(canonicalSymbol: string, signal?: AbortSignal) {
    return fetchJson<EodCloseContextResponse>(
      `/api/v2/market-context/eod-close/current/${encodeURIComponent(canonicalSymbol)}`,
      signal,
    );
  },
  eodCloseAsOf(canonicalSymbol: string, knowledgeCutoffAt: string, signal?: AbortSignal) {
    return fetchJson<EodCloseContextResponse>(withQuery(
      `/api/v2/market-context/eod-close/as-of/${encodeURIComponent(canonicalSymbol)}`,
      { knowledge_cutoff_at: knowledgeCutoffAt },
    ), signal);
  },
};
