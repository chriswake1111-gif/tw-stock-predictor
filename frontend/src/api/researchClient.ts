import type {
  DailyBaselineSelectionResponse,
  DailySnapshotRefreshResponse,
  ResearchMembershipMutationResponse,
  ResearchWatchlistItem,
} from "./types";
import { getLocalCsrfToken, invalidateLocalCsrfToken, isCsrfRejection } from "./localCsrfSession";

function csrfEndpoint(path: string): "/api/v2/data-operations/csrf-token" | "/api/v2/research/csrf-token" {
  // Installed local research is independent of the older workflow write gate.
  // Both endpoints issue sessions consumed by the existing security middleware.
  return /^\/api\/v2\/research\/(assumptions|journal)(\/|$)/.test(path)
    ? "/api/v2/data-operations/csrf-token"
    : "/api/v2/research/csrf-token";
}

async function ensureResearchCsrf(path: string): Promise<string> {
  return getLocalCsrfToken(csrfEndpoint(path));
}

export async function researchMutation<T>(path: string, payload: unknown, idempotencyKey?: string): Promise<T> {
  const token = await ensureResearchCsrf(path);
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
    if (isCsrfRejection(response.status, error.detail)) {
      invalidateLocalCsrfToken(token);
      try {
        await ensureResearchCsrf(path);
      } catch {
        throw new Error("csrf_refresh_failed");
      }
      throw new Error("csrf_refresh_required");
    }
    throw new Error(error.detail ?? `research_write_error:${response.status}`);
  }
  return await response.json() as T;
}

export function researchErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof Error)) return fallback;
  if (error.message === "pe_fiscal_year_required") return "請填寫 PE 適用年度；只會搭配同年度的全年預估 EPS。";
  if (error.message === "pe_fiscal_year_required_create_revision") return "這份舊 PE 尚未註明年度。請修改此版本、補上適用年度，再核准新版本。";
  if (error.message === "csrf_refresh_required") return "操作驗證已更新，本次操作尚未執行。請再按一次原操作；已填內容仍保留。";
  if (error.message === "csrf_refresh_failed") return "操作驗證更新失敗，本次操作尚未執行。請稍後再試；已填內容仍保留。";
  return error.message;
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
  selectDailyBaseline(itemId: string, snapshotId: string, cutoff: string, key: string) {
    return researchMutation<DailyBaselineSelectionResponse>(
      `/api/v2/research/daily-context/${encodeURIComponent(itemId)}/baseline-selections`,
      { baseline_snapshot_id: snapshotId, knowledge_cutoff_at: cutoff },
      key,
    );
  },
  refreshDailySnapshot(
    itemId: string,
    payload: {
      market_date: string;
      loaded_knowledge_cutoff_at: string;
      expected_snapshot_id: string | null;
      advance_knowledge_cutoff: true;
    },
    key: string,
  ) {
    return researchMutation<DailySnapshotRefreshResponse>(
      `/api/v2/research/queue/${encodeURIComponent(itemId)}/snapshot-refresh`,
      payload,
      key,
    );
  },
};
