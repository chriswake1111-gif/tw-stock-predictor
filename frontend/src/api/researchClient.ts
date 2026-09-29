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
  return /^\/api\/v2\/research\/(assumptions|journal|evidence|library)(\/|$)/.test(path)
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
  const guidanceErrors: Record<string, string> = {
    research_library_state_conflict: '標記已在其他頁面變更，請重新讀取並確認目前狀態後再操作。',
    research_library_storage_full: '本機清單紀錄已達上限；既有股票與研究仍可閱讀。',
    research_library_disabled: '我的股票功能目前已關閉。',
    research_library_unknown_symbol: '請先在本機股票名錄找到這檔股票，再設定標記。',
    research_content_changed_review_again: '資料或核准內容已更新，請重新預覽差異，再確認保存；原筆記仍保留。',
    research_evidence_changed_review_again: '這份來源已有新版本，請重新閱讀候選與限制。',
    candidate_values_mismatch: '候選內容與預覽不一致，請重新取得來源版本。',
    candidate_requires_concise_evidence_revision: '來源與限制超過現有假設欄位容量，請助理精簡查證版本；不會截掉重要內容。',
    research_evidence_storage_full: '本機查證區已達容量上限，既有研究仍可閱讀；請先決定紀錄整理方式。',
    evidence_not_selectable: '這份資料目前僅供查證，尚不能建立假設。',
  };
  const guidanceMessage = guidanceErrors[error.message];
  if (guidanceMessage) return guidanceMessage;
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
