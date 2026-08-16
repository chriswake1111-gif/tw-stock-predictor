import type { ResearchMembershipMutationResponse, ResearchWatchlistItem } from "./types";

let researchCsrfToken: string | null = null;

async function ensureResearchCsrf(): Promise<string> {
  if (researchCsrfToken) return researchCsrfToken;
  const response = await fetch("/api/v2/research/csrf-token", {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`research_csrf_error:${response.status}`);
  const payload = await response.json() as { csrf_token: string };
  researchCsrfToken = payload.csrf_token;
  return researchCsrfToken;
}

async function researchMutation<T>(path: string, payload: unknown, idempotencyKey?: string): Promise<T> {
  const token = await ensureResearchCsrf();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-CSRF-Token": token,
  };
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch(path, {
    method: "POST",
    headers,
    credentials: "same-origin",
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { detail?: string };
    if (response.status === 403 && ["csrf_session_expired", "csrf_session_invalid"].includes(error.detail ?? "")) {
      researchCsrfToken = null;
      try {
        await ensureResearchCsrf();
      } catch {
        researchCsrfToken = null;
        throw new Error("csrf_refresh_failed");
      }
      throw new Error("csrf_refresh_required");
    }
    throw new Error(error.detail ?? `research_write_error:${response.status}`);
  }
  return await response.json() as T;
}

export const researchWorkflowApi = {
  addSymbol(symbol: string) {
    return researchMutation<ResearchWatchlistItem & { created: boolean; restored: boolean }>(
      "/api/v2/research/queue",
      { symbol },
    );
  },
  archiveItem(itemId: string) {
    return researchMutation<ResearchMembershipMutationResponse>(
      `/api/v2/research/queue/${encodeURIComponent(itemId)}/archive`,
      {},
    );
  },
  unarchiveItem(itemId: string) {
    return researchMutation<ResearchMembershipMutationResponse>(
      `/api/v2/research/queue/${encodeURIComponent(itemId)}/unarchive`,
      {},
    );
  },
  acknowledgeSnapshot(itemId: string, snapshotId: string, cutoff: string, key: string) {
    return researchMutation(
      `/api/v2/research/queue/${encodeURIComponent(itemId)}/acknowledgments`,
      { acknowledged_snapshot_id: snapshotId, comparison_cutoff: cutoff },
      key,
    );
  },
};
