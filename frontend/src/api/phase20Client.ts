import type {
  ResearchBootstrapResponse,
  ResearchSummaryResponse,
  UniverseCoverage,
  UniverseSearchResponse,
} from "./types";
import { getCsrfToken, triggerSync } from "./dataOperationsClient";

export async function searchUniverse(
  query: string,
  limit: number = 10,
  signal?: AbortSignal
): Promise<UniverseSearchResponse> {
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
  });
  const res = await fetch(`/api/v2/universe/search?${params.toString()}`, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`search_universe_error:${res.status}`);
  }
  return res.json();
}

export async function getUniverseCoverage(signal?: AbortSignal): Promise<UniverseCoverage> {
  const res = await fetch("/api/v2/universe/coverage", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) {
    throw new Error(`universe_coverage_error:${res.status}`);
  }
  return res.json();
}

export async function getResearchSummary(
  canonicalSymbol: string,
  asOf?: string,
  signal?: AbortSignal
): Promise<ResearchSummaryResponse> {
  const params = new URLSearchParams();
  if (asOf) {
    params.set("as_of", asOf);
  }
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(
    `/api/v2/research/summary/${encodeURIComponent(canonicalSymbol)}${queryStr}`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    }
  );
  if (!res.ok) {
    throw new Error(`research_summary_error:${res.status}`);
  }
  return res.json();
}

export async function bootstrapSymbol(
  canonicalSymbol: string
): Promise<ResearchBootstrapResponse> {
  const token = await getCsrfToken();
  const res = await fetch("/api/v2/research/bootstrap", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": token,
    },
    body: JSON.stringify({ canonical_symbol: canonicalSymbol }),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({}));
    throw new Error(errorBody.detail || `research_bootstrap_error:${res.status}`);
  }
  return res.json();
}

export async function triggerUniversePrep(): Promise<{ operation_id: string }> {
  const result = await triggerSync(undefined, 90);
  return { operation_id: result.operation_id };
}
